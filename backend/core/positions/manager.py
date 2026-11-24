"""Position manager - mutate positions via sell/merge operations."""

import asyncio
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from core.http_retry import fetch_json_with_retry
from core.paths import GAMMA_API_BASE_URL
from core.positions.service import (
    LEG_ROLES,
    PositionService,
    legacy_sale_estimate,
    share_claim,
)
from core.positions.storage import PositionStorage
from core.wallet.manager import WalletManager


@dataclass
class SellResult:
    """Result of selling tokens via CLOB."""

    success: bool
    token_id: str
    amount: float
    order_id: Optional[str]
    filled: bool
    recovered_value: float  # pUSD received, as reported by the exchange
    error: Optional[str] = None


@dataclass
class MergeResult:
    """Result of merging YES+NO tokens to USDC."""

    success: bool
    market_id: str
    merged_amount: float
    tx_hash: Optional[str]
    error: Optional[str] = None


@dataclass
class SideExitResult:
    """Result of exiting one side of a position."""

    merged: float = 0.0  # Tokens merged back to pUSD
    merge_tx: Optional[str] = None
    sold_wanted: float = 0.0  # Wanted tokens sold via CLOB
    sold_unwanted: float = 0.0  # Unwanted tokens sold via CLOB
    recovered: float = 0.0  # pUSD recovered: merged pairs + sale proceeds
    error: Optional[str] = None


@dataclass
class RedeemResult:
    """Result of redeeming a position's resolved legs."""

    success: bool
    payout: float  # pUSD credited to this position
    tx_hashes: list[str]
    error: Optional[str] = None


@dataclass
class ExitResult:
    """Result of exiting an entire position."""

    success: bool
    target: SideExitResult
    cover: SideExitResult
    total_recovered: float
    message: str


class PositionManager:
    """Manages position mutation operations (sell, merge)."""

    def __init__(
        self,
        wallet: WalletManager,
        storage: PositionStorage,
        service: PositionService,
    ):
        self.wallet = wallet
        self.storage = storage
        self.service = service

    async def _get_market_info(self, market_id: str) -> dict:
        """Fetch market info from Polymarket API."""
        async with httpx.AsyncClient(timeout=30.0) as http:
            return await fetch_json_with_retry(
                http, f"{GAMMA_API_BASE_URL}/markets/{market_id}"
            )

    def _merge_tokens(
        self,
        condition_id: str,
        amount: float,
    ) -> tuple[Optional[str], Optional[str]]:
        """Merge YES+NO tokens back to pUSD via a gasless relayer batch.

        Returns (tx_hash, error). The SDK resolves NegRisk routing internally.
        """
        from core.trading.secure_client import get_secure_client

        client = get_secure_client(self.wallet)
        if client is None:
            return None, "Trading client init failed (unlock + relayer key required)"

        try:
            handle = client.merge_positions(
                condition_id=condition_id,
                amount=int(amount * 1e6),  # base units (6 decimals)
            )
            outcome = handle.wait()
            tx_hash = outcome.transaction_hash
            logger.info(f"Merge via deposit wallet: {tx_hash}")
            return tx_hash, None
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Merge error: {error_msg}")
            return None, error_msg

    def _start_tracking(self, position_id: str) -> Optional[str]:
        """Older positions: seed share counts and sale proceeds from balances.

        Positions recorded before tracking have estimated cost and share
        counts. Seeding them before the first sell/merge (from this
        position's part of the wallet balances) keeps today's P&L and lets
        each later sale be recorded exactly. Balances are read strictly: a
        failed read must not pass for "all sold", so it returns an error and
        the caller changes nothing.
        """
        entry = self.storage.get(position_id)
        if not entry:
            return None
        fields = [f"{side}_{role}" for side, role in LEG_ROLES]
        needs_shares = any(entry.get(f"{f}_shares") is None for f in fields)
        needs_proceeds = any(
            entry.get(f"{side}_sell_proceeds") is None for side in ("target", "cover")
        )
        if not (needs_shares or needs_proceeds):
            return None

        try:
            held = dict(self.service.allocated_balances(entry))
            # A resolved leg keeps its recorded claim: auto-redeem may have
            # emptied the wallet, which doesn't mean the shares were sold.
            for side in ("target", "cover"):
                if self.service.leg_payout_rates(entry, side) is not None:
                    for role in ("wanted", "unwanted"):
                        held[f"{side}_{role}"] = share_claim(entry, side, role)[0]
        except Exception as e:
            return (
                f"Couldn't read this position's balances to set it up ({e}). "
                "Nothing was changed; try again."
            )
        if needs_proceeds:
            self.storage.start_tracking_proceeds(
                position_id,
                legacy_sale_estimate(entry, "target", held["target_unwanted"]),
                legacy_sale_estimate(entry, "cover", held["cover_unwanted"]),
            )
        if needs_shares:
            self.storage.seed_shares(position_id, held)
        return None

    async def sell_position_tokens(
        self,
        position_id: str,
        side: str,  # "target" or "cover"
        token_type: str,  # "wanted" or "unwanted"
        slippage: float = 10,
    ) -> SellResult:
        """Sell tokens from a position via CLOB."""
        # Get position with live data
        position = self.service.get_position(position_id)
        if not position:
            return SellResult(
                success=False,
                token_id="",
                amount=0,
                order_id=None,
                filled=False,
                recovered_value=0,
                error="Position not found",
            )

        tracking_error = self._start_tracking(position_id)
        if tracking_error:
            return SellResult(
                success=False,
                token_id="",
                amount=0,
                order_id=None,
                filled=False,
                recovered_value=0,
                error=tracking_error,
            )

        # Determine which tokens to sell
        if side == "target":
            market_id = position.target_market_id
            wanted_position = position.target_position
            if token_type == "wanted":
                balance = position.target_balance
                token_id = position.target_token_id
                price = position.target_current_price
            else:  # unwanted
                balance = position.target_unwanted_balance
                # Get unwanted token ID
                yes_id, no_id = self.service.get_market_token_ids(market_id)
                token_id = no_id if wanted_position == "YES" else yes_id
                price = 1 - position.target_current_price
        else:  # cover
            market_id = position.cover_market_id
            wanted_position = position.cover_position
            if token_type == "wanted":
                balance = position.cover_balance
                token_id = position.cover_token_id
                price = position.cover_current_price
            else:  # unwanted
                balance = position.cover_unwanted_balance
                yes_id, no_id = self.service.get_market_token_ids(market_id)
                token_id = no_id if wanted_position == "YES" else yes_id
                price = 1 - position.cover_current_price

        if balance < 0.01:
            return SellResult(
                success=False,
                token_id=token_id,
                amount=0,
                order_id=None,
                filled=False,
                recovered_value=0,
                error=f"Insufficient balance: {balance:.4f}",
            )

        # Execute sell via deposit-wallet (sigtype-3) client
        from core.trading.secure_client import get_secure_client

        client = get_secure_client(self.wallet)
        if not client:
            return SellResult(
                success=False,
                token_id=token_id,
                amount=balance,
                order_id=None,
                filled=False,
                recovered_value=0,
                error="Trading client init failed (unlock + relayer key required)",
            )

        from core.trading.clob import sell_via_clob

        sale = sell_via_clob(
            client,
            token_id,
            balance,
            price,
            slippage=slippage,
            fee_market=self.service.fee_stub(market_id),
        )
        filled = sale.filled > 0

        # Record what the exchange paid, so P&L sees the cash that replaced
        # the tokens. Wanted tokens -> realized; unwanted -> the leg's sale
        # proceeds (lowers its cost), as at buy time.
        if filled and token_type == "unwanted":
            self.storage.update_clob_status(position_id, side, sale.order_id, True)
            self.storage.add_sell_proceeds(position_id, side, sale.proceeds)
        elif filled:
            self.storage.add_realized_proceeds(position_id, sale.proceeds)
        if filled:
            self.storage.adjust_shares(position_id, side, token_type, -sale.filled)

        return SellResult(
            success=filled,
            token_id=token_id,
            amount=sale.filled,
            order_id=sale.order_id,
            filled=filled,
            recovered_value=round(sale.proceeds, 4),
            error=sale.error,
        )

    async def merge_position_tokens(
        self,
        position_id: str,
        side: str,  # "target" or "cover"
    ) -> MergeResult:
        """Merge YES+NO tokens to USDC for a position side."""
        # Get position with live data
        position = self.service.get_position(position_id)
        if not position:
            return MergeResult(
                success=False,
                market_id="",
                merged_amount=0,
                tx_hash=None,
                error="Position not found",
            )

        tracking_error = self._start_tracking(position_id)
        if tracking_error:
            return MergeResult(
                success=False,
                market_id="",
                merged_amount=0,
                tx_hash=None,
                error=tracking_error,
            )

        # Get balances for merge
        if side == "target":
            market_id = position.target_market_id
            wanted_balance = position.target_balance
            unwanted_balance = position.target_unwanted_balance
        else:  # cover
            market_id = position.cover_market_id
            wanted_balance = position.cover_balance
            unwanted_balance = position.cover_unwanted_balance

        # Mergeable amount is min of YES and NO balances
        mergeable = min(wanted_balance, unwanted_balance)

        if mergeable < 0.01:
            return MergeResult(
                success=False,
                market_id=market_id,
                merged_amount=0,
                tx_hash=None,
                error=f"Insufficient tokens for merge: wanted={wanted_balance:.4f}, unwanted={unwanted_balance:.4f}",
            )

        # Get condition_id from market
        try:
            market_data = await self._get_market_info(market_id)
            condition_id = market_data.get("conditionId") or ""
            if not condition_id:
                return MergeResult(
                    success=False,
                    market_id=market_id,
                    merged_amount=0,
                    tx_hash=None,
                    error="Could not fetch market condition ID",
                )
        except Exception as e:
            return MergeResult(
                success=False,
                market_id=market_id,
                merged_amount=0,
                tx_hash=None,
                error=f"Failed to fetch market info: {e}",
            )

        tx_hash, error = self._merge_tokens(condition_id, mergeable)

        if error:
            return MergeResult(
                success=False,
                market_id=market_id,
                merged_amount=0,
                tx_hash=None,
                error=error,
            )

        # Each merged YES+NO pair returns exactly 1 pUSD.
        merged = int(mergeable * 1e6) / 1e6
        self.storage.add_realized_proceeds(position_id, merged)
        self.storage.adjust_shares(position_id, side, "wanted", -merged)
        self.storage.adjust_shares(position_id, side, "unwanted", -merged)

        return MergeResult(
            success=True,
            market_id=market_id,
            merged_amount=round(mergeable, 4),
            tx_hash=tx_hash,
            error=None,
        )

    async def _exit_side(
        self,
        position_id: str,
        side: str,
        wanted_balance: float,
        unwanted_balance: float,
        slippage: float,
    ) -> SideExitResult:
        """Exit one side: merge if possible, then sell excess via CLOB."""
        result = SideExitResult()
        THRESHOLD = 0.01
        mergeable = min(wanted_balance, unwanted_balance)

        # Step 1: Merge YES+NO → pUSD (gasless via the SDK, no CLOB needed)
        if mergeable >= THRESHOLD:
            merge = await self.merge_position_tokens(position_id, side)
            if merge.success:
                result.merged = merge.merged_amount
                result.merge_tx = merge.tx_hash
                result.recovered = merge.merged_amount
                logger.info(f"Exit {side}: merged {merge.merged_amount:.2f} → pUSD")
                # Invalidate cache so sell steps see post-merge balances
                self.service.invalidate_cache()
            else:
                result.error = f"Merge failed: {merge.error}"
                return result

        # Step 2: Sell excess wanted tokens via CLOB
        excess_wanted = wanted_balance - mergeable
        if excess_wanted >= THRESHOLD:
            sell = await self.sell_position_tokens(
                position_id, side, "wanted", slippage=slippage
            )
            if sell.success:
                result.sold_wanted = sell.amount
                result.recovered += sell.recovered_value
            else:
                # Non-fatal: merge already recovered what it could
                if result.error:
                    result.error += f"; Sell wanted failed: {sell.error}"
                else:
                    result.error = f"Sell wanted failed: {sell.error}"

        # Step 3: Sell excess unwanted tokens via CLOB
        excess_unwanted = unwanted_balance - mergeable
        if excess_unwanted >= THRESHOLD:
            sell = await self.sell_position_tokens(
                position_id, side, "unwanted", slippage=slippage
            )
            if sell.success:
                result.sold_unwanted = sell.amount
                result.recovered += sell.recovered_value
            else:
                if result.error:
                    result.error += f"; Sell unwanted failed: {sell.error}"
                else:
                    result.error = f"Sell unwanted failed: {sell.error}"

        return result

    async def exit_position(self, position_id: str, slippage: float = 10) -> ExitResult:
        """Exit entire position: merge where possible, sell excess via CLOB."""
        position = self.service.get_position(position_id)
        if not position:
            return ExitResult(
                success=False,
                target=SideExitResult(),
                cover=SideExitResult(),
                total_recovered=0,
                message="Position not found",
            )

        target_result = await self._exit_side(
            position_id,
            "target",
            position.target_balance,
            position.target_unwanted_balance,
            slippage,
        )

        # Invalidate cache between sides so cover gets fresh balances
        self.service.invalidate_cache()

        cover_result = await self._exit_side(
            position_id,
            "cover",
            position.cover_balance,
            position.cover_unwanted_balance,
            slippage,
        )

        # Each merge and sale already recorded its own proceeds.
        total = round(target_result.recovered + cover_result.recovered, 4)
        any_success = target_result.recovered > 0 or cover_result.recovered > 0

        messages = []
        if target_result.merged > 0:
            messages.append(f"Target: merged ${target_result.merged:.2f}")
        if target_result.sold_wanted > 0:
            messages.append(f"Target: sold {target_result.sold_wanted:.1f} tokens")
        if cover_result.merged > 0:
            messages.append(f"Cover: merged ${cover_result.merged:.2f}")
        if cover_result.sold_wanted > 0:
            messages.append(f"Cover: sold {cover_result.sold_wanted:.1f} tokens")

        errors = []
        if target_result.error:
            errors.append(f"Target: {target_result.error}")
        if cover_result.error:
            errors.append(f"Cover: {cover_result.error}")

        if not messages and not errors:
            msg = "Nothing to exit (no token balances)"
        elif errors:
            msg = "; ".join(messages + errors)
        else:
            msg = "; ".join(messages)

        self.service.invalidate_cache()

        return ExitResult(
            success=any_success,
            target=target_result,
            cover=cover_result,
            total_recovered=total,
            message=msg,
        )

    async def retry_pending_sells(self, position_id: str, slippage: float = 10) -> dict:
        """Retry selling unwanted tokens for pending positions."""
        position = self.service.get_position(position_id)
        if not position:
            return {"success": False, "message": "Position not found"}

        results = {
            "success": True,
            "target_result": None,
            "cover_result": None,
            "message": "",
        }
        messages = []

        # Retry target unwanted if balance > 0
        if position.target_unwanted_balance > 0.01:
            result = await self.sell_position_tokens(
                position_id,
                "target",
                "unwanted",
                slippage=slippage,
            )
            results["target_result"] = {
                "success": result.success,
                "token_id": result.token_id,
                "amount": result.amount,
                "order_id": result.order_id,
                "filled": result.filled,
                "recovered_value": result.recovered_value,
                "error": result.error,
            }
            if result.success:
                messages.append(f"Target: sold {result.amount:.2f} tokens")
            else:
                messages.append(f"Target: {result.error}")
                results["success"] = False

        # Retry cover unwanted if balance > 0
        if position.cover_unwanted_balance > 0.01:
            result = await self.sell_position_tokens(
                position_id,
                "cover",
                "unwanted",
                slippage=slippage,
            )
            results["cover_result"] = {
                "success": result.success,
                "token_id": result.token_id,
                "amount": result.amount,
                "order_id": result.order_id,
                "filled": result.filled,
                "recovered_value": result.recovered_value,
                "error": result.error,
            }
            if result.success:
                messages.append(f"Cover: sold {result.amount:.2f} tokens")
            else:
                messages.append(f"Cover: {result.error}")
                results["success"] = False

        if not messages:
            results["message"] = "No pending tokens to sell"
        else:
            results["message"] = "; ".join(messages)

        return results

    # ── redeem ───────────────────────────────────────────────────────────

    def _client(self):
        from core.trading.secure_client import get_secure_client

        client = get_secure_client(self.wallet)
        if client is None:
            raise ValueError(
                "Trading client init failed (unlock + relayer key required)"
            )
        return client

    def _market_credits(
        self, market_id: str, own: dict, own_balances: dict
    ) -> list[tuple[str, str, float]]:
        """Every listed leg on this market: (position_id, side, payout).

        One redeem pays out the wallet's whole balance of the market, so each
        position sharing it is credited its own allocated part.
        """
        credits = []
        for entry, side in self.service.leg_holders(market_id):
            if entry["position_id"] == own["position_id"]:
                balances = own_balances
            else:
                try:
                    balances = self.service.allocated_balances(entry)
                except Exception as e:
                    logger.warning(f"Redeem skips {entry['position_id']}: {e}")
                    continue
            wanted = balances[f"{side}_wanted"]
            unwanted = balances[f"{side}_unwanted"]
            if wanted + unwanted < 0.01:
                continue
            rates = self.service.leg_payout_rates(entry, side)
            if rates is None:
                continue
            payout = wanted * rates[0] + unwanted * rates[1]
            credits.append((entry["position_id"], side, payout))
        return credits

    async def redeem_position(self, position_id: str) -> RedeemResult:
        """Redeem every resolved leg of a position (gasless, via the SDK).

        Unresolved legs are left alone; a leg that lost pays nothing and
        sends no transaction.
        """
        entry = self.storage.get(position_id)
        if not entry:
            return RedeemResult(False, 0.0, [], "Position not found")

        balances = self.service.allocated_balances(entry)
        payout = 0.0
        tx_hashes: list[str] = []
        notes: list[str] = []
        settled = False
        # Legs paid by a redeem in this call: both legs can be on one market.
        done: set[tuple[str, str]] = set()

        for side in ("target", "cover"):
            if (position_id, side) in done:
                continue
            if balances[f"{side}_wanted"] + balances[f"{side}_unwanted"] < 0.01:
                continue
            market_id = entry[f"{side}_market_id"]
            try:
                condition_id = self.service.condition_id(market_id)
            except Exception as e:
                notes.append(f"{side}: failed to fetch market info: {e}")
                continue
            if not condition_id:
                notes.append(f"{side}: could not fetch market condition ID")
                continue
            if self.service.leg_payout_rates(entry, side) is None:
                notes.append(f"{side}: market not resolved yet")
                continue

            credits = [
                c
                for c in self._market_credits(market_id, entry, balances)
                if (c[0], c[1]) not in done
            ]
            if sum(c[2] for c in credits) > 0:
                try:
                    client = self._client()
                    # The relayer takes seconds to confirm: wait in a thread
                    # so the server keeps answering other requests.
                    outcome = await asyncio.to_thread(
                        lambda: client.redeem_positions(
                            condition_id=condition_id
                        ).wait()
                    )
                    tx_hashes.append(outcome.transaction_hash)
                except Exception as e:
                    logger.error(f"Redeem error: {e}")
                    notes.append(f"{side}: redeem failed: {e}")
                    continue
                for pid, leg, paid in credits:
                    stored = self.storage.get(pid) or {}
                    estimate = 0.0
                    if stored.get(f"{leg}_sell_proceeds") is None:
                        # Older position: the cost estimate shown until now.
                        kept = share_claim(stored, leg, "unwanted")[0]
                        estimate = legacy_sale_estimate(stored, leg, kept)
                    self.storage.settle_redeemed_leg(pid, leg, paid, estimate)
                    done.add((pid, leg))
                    if pid == position_id:
                        payout += paid
            else:
                notes.append(
                    f"{side}: resolved against this position, nothing to redeem"
                )
            settled = True

        if not settled and not notes:
            notes.append("Nothing to redeem: no tokens held")
        return RedeemResult(
            success=settled,
            payout=round(payout, 6),
            tx_hashes=tx_hashes,
            error="; ".join(notes) or None,
        )

"""Position manager - mutate positions via sell/merge operations."""

from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger
from web3 import Web3

from core.wallet.contracts import CONTRACTS, CTF_ABI
from core.wallet.manager import WalletManager
from core.positions.storage import PositionStorage
from core.positions.service import PositionService


@dataclass
class SellResult:
    """Result of selling tokens via CLOB."""

    success: bool
    token_id: str
    amount: float
    order_id: Optional[str]
    filled: bool
    recovered_value: float  # Approximate USDC recovered
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

    merged: float = 0.0  # Tokens merged back to USDC.e
    merge_tx: Optional[str] = None
    sold_wanted: float = 0.0  # Wanted tokens sold via CLOB
    sold_unwanted: float = 0.0  # Unwanted tokens sold via CLOB
    recovered: float = 0.0  # Estimated total USDC.e recovered
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
        self._w3: Optional[Web3] = None

    def _get_web3(self) -> Web3:
        """Get or create Web3 instance."""
        if self._w3 is None:
            self._w3 = Web3(
                Web3.HTTPProvider(self.wallet.rpc_url, request_kwargs={"timeout": 60})
            )
        return self._w3

    async def _get_market_info(self, market_id: str) -> dict:
        """Fetch market info from Polymarket API."""
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                f"https://gamma-api.polymarket.com/markets/{market_id}"
            )
            resp.raise_for_status()
            return resp.json()

    def _merge_tokens(
        self,
        condition_id: str,
        amount: float,
        neg_risk: bool = False,
    ) -> tuple[Optional[str], Optional[str]]:
        """Merge YES+NO tokens back to USDC. Returns (tx_hash, error).

        For binary markets, merges via CTF directly.
        For NegRisk (multi-outcome) markets, merges via the NegRisk adapter.
        """
        w3 = self._get_web3()
        address = Web3.to_checksum_address(self.wallet.address)
        account = w3.eth.account.from_key(self.wallet.get_unlocked_key())

        # NegRisk adapter has the same mergePositions ABI as CTF
        merge_contract_addr = (
            CONTRACTS["NEG_RISK_ADAPTER"] if neg_risk else CONTRACTS["CTF"]
        )
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(merge_contract_addr),
            abi=CTF_ABI,
        )
        logger.info(
            f"Merge via {'NegRisk adapter' if neg_risk else 'CTF'}: "
            f"{merge_contract_addr[:10]}..."
        )

        amount_wei = int(amount * 1e6)
        condition_bytes = bytes.fromhex(
            condition_id[2:] if condition_id.startswith("0x") else condition_id
        )

        try:
            base_gas_price = w3.eth.gas_price
            gas_price = int(base_gas_price * 1.2)

            tx = contract.functions.mergePositions(
                Web3.to_checksum_address(CONTRACTS["USDC_E"]),
                bytes(32),  # parentCollectionId
                condition_bytes,
                [1, 2],  # partition for YES, NO
                amount_wei,
            ).build_transaction(
                {
                    "from": address,
                    "nonce": w3.eth.get_transaction_count(address),
                    "gas": 300000,
                    "gasPrice": gas_price,
                    "chainId": 137,
                }
            )

            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"Merge TX: {tx_hash.hex()} (gasPrice={gas_price})")

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            if receipt["status"] != 1:
                return None, f"Merge transaction failed: {tx_hash.hex()}"

            return tx_hash.hex(), None
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Merge error: {error_msg}")
            return None, error_msg

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

        # Execute sell
        from core.trading.clob_client import get_clob_client

        client = get_clob_client(self.wallet)
        if not client:
            return SellResult(
                success=False,
                token_id=token_id,
                amount=balance,
                order_id=None,
                filled=False,
                recovered_value=0,
                error="CLOB client initialization failed",
            )

        from core.trading.clob import sell_via_clob

        order_id, filled_size, error = sell_via_clob(
            client, token_id, balance, price, slippage=slippage
        )

        filled = filled_size > 0

        # Estimate recovered value: use market price (not slippage floor)
        # FAK fills at best available prices, typically near market price
        recovered = filled_size * price

        # Update storage if selling unwanted tokens
        if filled and token_type == "unwanted":
            self.storage.update_clob_status(position_id, side, order_id, True)

        return SellResult(
            success=filled,
            token_id=token_id,
            amount=filled_size,
            order_id=order_id,
            filled=filled,
            recovered_value=round(recovered, 2),
            error=error,
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

        # Execute merge — use NegRisk adapter for multi-outcome markets
        neg_risk = bool(market_data.get("negRisk", False))
        tx_hash, error = self._merge_tokens(condition_id, mergeable, neg_risk=neg_risk)

        if error:
            return MergeResult(
                success=False,
                market_id=market_id,
                merged_amount=0,
                tx_hash=None,
                error=error,
            )

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

        # Step 1: Merge YES+NO → USDC.e (on-chain, no CLOB needed)
        if mergeable >= THRESHOLD:
            merge = await self.merge_position_tokens(position_id, side)
            if merge.success:
                result.merged = merge.merged_amount
                result.merge_tx = merge.tx_hash
                result.recovered = merge.merged_amount
                logger.info(f"Exit {side}: merged {merge.merged_amount:.2f} → USDC.e")
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

        total = round(target_result.recovered + cover_result.recovered, 4)
        any_success = target_result.recovered > 0 or cover_result.recovered > 0

        # Record realized proceeds for accurate P&L
        if total > 0:
            self.storage.add_realized_proceeds(position_id, total)

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

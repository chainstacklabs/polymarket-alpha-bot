"""Execute trades via the deposit-wallet (sigtype-3) SDK: split + CLOB sell.

Split/merge run as gasless relayer batches and orders are POLY_1271-signed by
the deposit wallet — all through the polymarket-client SecureClient. The EOA is
only the signing key; pUSD + CTF tokens live in the deposit wallet.
"""

import json
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from core.http_retry import fetch_json_with_retry
from core.wallet.manager import WalletManager

# Polymarket enforces a $1 minimum CLOB order size.
MIN_ORDER_USD = 1.0


@dataclass
class MarketInfo:
    market_id: str
    question: str
    condition_id: str
    yes_token_id: str
    no_token_id: Optional[str]
    yes_price: float
    no_price: float
    neg_risk: bool = False
    fees_enabled: bool = False
    fee_schedule: Optional[dict] = None


@dataclass
class TradeResult:
    success: bool
    market_id: str
    position: str
    amount: float
    split_tx: Optional[str]
    clob_order_id: Optional[str]
    clob_filled: bool
    error: Optional[str] = None
    # Market info captured during trade (for position recording)
    question: str = ""
    wanted_token_id: str = ""
    unwanted_token_id: str = ""
    ctf_token_ids: Optional[list[str]] = None
    entry_price: float = 0.0


@dataclass
class BuyPairResult:
    success: bool
    pair_id: str
    target: TradeResult
    cover: TradeResult
    total_spent: float
    final_balances: dict


class TradingExecutor:
    """Executes trades via deposit-wallet split + sigtype-3 CLOB sell."""

    def __init__(self, wallet_manager: WalletManager):
        self.wallet = wallet_manager

    async def get_market_info(self, market_id: str) -> MarketInfo:
        """Fetch market info from Polymarket API."""
        async with httpx.AsyncClient(timeout=30.0) as http:
            data = await fetch_json_with_retry(
                http, f"https://gamma-api.polymarket.com/markets/{market_id}"
            )

        clob_tokens = json.loads(data.get("clobTokenIds", "[]"))
        prices = json.loads(data.get("outcomePrices", "[0.5, 0.5]"))

        return MarketInfo(
            market_id=market_id,
            question=data.get("question", ""),
            condition_id=data.get("conditionId", ""),
            yes_token_id=clob_tokens[0] if clob_tokens else "",
            no_token_id=clob_tokens[1] if len(clob_tokens) > 1 else None,
            yes_price=float(prices[0]) if prices else 0.5,
            no_price=float(prices[1]) if len(prices) > 1 else 0.5,
            neg_risk=bool(data.get("negRisk", False)),
            fees_enabled=bool(data.get("feesEnabled", False)),
            fee_schedule=data.get("feeSchedule"),
        )

    def _split_position(self, client, condition_id: str, amount_usd: float) -> str:
        """Split pUSD into YES + NO tokens via a gasless relayer batch.

        `client` is a SecureClient acting for the deposit wallet. Returns the
        on-chain tx hash. The SDK resolves NegRisk routing internally.
        """
        handle = client.split_position(
            condition_id=condition_id,
            amount=int(amount_usd * 1e6),  # pUSD base units (6 decimals)
        )
        outcome = handle.wait()
        tx_hash = outcome.transaction_hash
        logger.info(f"Split via deposit wallet: {tx_hash}")
        return tx_hash

    async def buy_single_position(
        self,
        market_id: str,
        position: str,  # "YES" or "NO"
        amount: float,
        skip_clob_sell: bool = False,
        slippage: float = 10,
        client=None,
    ) -> TradeResult:
        """Buy a single position: split, then sell the unwanted side via CLOB."""
        position = position.upper()
        if position not in ["YES", "NO"]:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error="Position must be YES or NO",
            )

        if client is None:
            from core.trading.secure_client import get_secure_client

            client = get_secure_client(self.wallet)
            if client is None:
                return TradeResult(
                    success=False,
                    market_id=market_id,
                    position=position,
                    amount=amount,
                    split_tx=None,
                    clob_order_id=None,
                    clob_filled=False,
                    error="Trading client init failed (unlock + relayer key required)",
                )

        market = await self.get_market_info(market_id)

        unwanted_token = (
            market.no_token_id if position == "YES" else market.yes_token_id
        )
        unwanted_price = market.no_price if position == "YES" else market.yes_price

        # Split position (gasless relayer batch)
        try:
            split_tx = self._split_position(client, market.condition_id, amount)
        except Exception as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                position=position,
                amount=amount,
                split_tx=None,
                clob_order_id=None,
                clob_filled=False,
                error=f"Split failed: {e}",
            )

        # Sell unwanted side via sigtype-3 CLOB
        clob_order_id = None
        clob_filled = False
        clob_error = None

        if not skip_clob_sell and unwanted_token:
            if amount * unwanted_price < MIN_ORDER_USD:
                clob_error = (
                    f"Unwanted side value ${amount * unwanted_price:.2f} below "
                    f"${MIN_ORDER_USD} CLOB minimum — skipping sell"
                )
                logger.warning(clob_error)
            else:
                from core.trading.clob import sell_via_clob

                clob_order_id, clob_filled_size, clob_error = sell_via_clob(
                    client, unwanted_token, amount, unwanted_price, slippage=slippage
                )
                clob_filled = clob_filled_size > 0

        wanted_token_id = (
            market.yes_token_id if position == "YES" else (market.no_token_id or "")
        )
        unwanted_token_id = (
            (market.no_token_id or "") if position == "YES" else market.yes_token_id
        )
        entry_price = market.yes_price if position == "YES" else market.no_price

        return TradeResult(
            success=True,  # Split succeeded
            market_id=market_id,
            position=position,
            amount=amount,
            split_tx=split_tx,
            clob_order_id=clob_order_id,
            clob_filled=clob_filled,
            error=clob_error,
            question=market.question,
            wanted_token_id=wanted_token_id,
            unwanted_token_id=unwanted_token_id,
            ctf_token_ids=[t for t in (market.yes_token_id, market.no_token_id) if t],
            entry_price=entry_price,
        )

    async def buy_pair(
        self,
        pair_id: str,
        target_market_id: str,
        target_position: str,
        cover_market_id: str,
        cover_position: str,
        amount_per_position: float,
        skip_clob_sell: bool = False,
        slippage: float = 10,
    ) -> BuyPairResult:
        """Buy both positions in a portfolio pair."""
        if not self.wallet.is_unlocked:
            raise ValueError("Wallet not unlocked")

        from core.fees import compute_fee
        from core.trading.secure_client import get_secure_client

        target_info = await self.get_market_info(target_market_id)
        cover_info = await self.get_market_info(cover_market_id)

        target_price = (
            target_info.yes_price if target_position == "YES" else target_info.no_price
        )
        cover_price = (
            cover_info.yes_price if cover_position == "YES" else cover_info.no_price
        )

        def _fee_stub(info: MarketInfo) -> dict:
            stub: dict = {"id": info.market_id, "feesEnabled": info.fees_enabled}
            if info.fee_schedule:
                stub["feeSchedule"] = info.fee_schedule
            return stub

        entry_fees = compute_fee(
            amount_per_position, target_price, _fee_stub(target_info)
        ) + compute_fee(amount_per_position, cover_price, _fee_stub(cover_info))

        balances = self.wallet.get_balances()
        required = amount_per_position * 2 + entry_fees
        if balances.pusd < required:
            raise ValueError(
                f"Insufficient deposit-wallet pUSD: need {required:.2f} "
                f"(includes ${entry_fees:.2f} taker fees), have {balances.pusd:.2f}"
            )

        # One client for the whole pair. SecureClient.create() auto-deploys the
        # deposit wallet; split/sell recover allowances on demand, so no
        # explicit approvals step is needed here.
        client = get_secure_client(self.wallet)
        if client is None:
            raise ValueError(
                "Trading client init failed (unlock + relayer key required)"
            )

        logger.info(f"Buying target: {target_position} on {target_market_id}")
        target_result = await self.buy_single_position(
            target_market_id,
            target_position,
            amount_per_position,
            skip_clob_sell,
            slippage=slippage,
            client=client,
        )

        logger.info(f"Buying cover: {cover_position} on {cover_market_id}")
        cover_result = await self.buy_single_position(
            cover_market_id,
            cover_position,
            amount_per_position,
            skip_clob_sell,
            slippage=slippage,
            client=client,
        )

        final_balances = self.wallet.get_balances()

        return BuyPairResult(
            success=target_result.success and cover_result.success,
            pair_id=pair_id,
            target=target_result,
            cover=cover_result,
            total_spent=amount_per_position * 2,
            final_balances={"pol": final_balances.pol, "pusd": final_balances.pusd},
        )

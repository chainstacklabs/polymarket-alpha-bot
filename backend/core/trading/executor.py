"""Execute on-chain trades: split + CLOB sell."""

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from web3 import Web3
from loguru import logger

from core.wallet.contracts import CONTRACTS, CTF_ABI
from core.wallet.manager import WalletManager


@dataclass
class MarketInfo:
    market_id: str
    question: str
    condition_id: str
    yes_token_id: str
    no_token_id: Optional[str]
    yes_price: float
    no_price: float


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
    """Executes on-chain trades via split + CLOB sell."""

    def __init__(self, wallet_manager: WalletManager):
        self.wallet = wallet_manager
        self.rpc_url = wallet_manager.rpc_url

    def _get_web3(self) -> Web3:
        return Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60}))

    async def get_market_info(self, market_id: str) -> MarketInfo:
        """Fetch market info from Polymarket API."""
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                f"https://gamma-api.polymarket.com/markets/{market_id}"
            )
            data = resp.json()

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
        )

    def _get_clob_client(self):
        """Initialize CLOB client with optional proxy support."""
        try:
            from py_clob_client.client import ClobClient
            import py_clob_client.http_helpers.helpers as clob_helpers
        except ImportError:
            logger.error("py-clob-client not installed")
            return None

        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy:
            logger.info(f"Using proxy: {proxy[:30]}...")
            clob_helpers._http_client = httpx.Client(
                http2=True, proxy=proxy, timeout=30.0
            )

        private_key = self.wallet.get_unlocked_key()
        address = self.wallet.address

        try:
            client = ClobClient(
                "https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                signature_type=0,
                funder=address,
            )
            creds = client.create_or_derive_api_creds()
            client.set_api_creds(creds)
            return client
        except Exception as e:
            logger.error(f"CLOB API error: {e}")
            return None

    def _split_position(
        self,
        condition_id: str,
        amount_usd: float,
    ) -> str:
        """Split USDC into YES + NO tokens. Returns tx hash."""
        w3 = self._get_web3()
        address = Web3.to_checksum_address(self.wallet.address)
        account = w3.eth.account.from_key(self.wallet.get_unlocked_key())

        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        amount_wei = int(amount_usd * 1e6)
        condition_bytes = bytes.fromhex(
            condition_id[2:] if condition_id.startswith("0x") else condition_id
        )

        tx = ctf.functions.splitPosition(
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
                "gasPrice": w3.eth.gas_price,
                "chainId": 137,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"Split TX: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise ValueError(f"Split failed: {tx_hash.hex()}")

        return tx_hash.hex()

    def _sell_via_clob(
        self,
        token_id: str,
        amount: float,
        price: float,
    ) -> tuple[Optional[str], bool, Optional[str]]:
        """Sell tokens via CLOB using IOC market order. Returns (order_id, filled, error_message)."""
        client = self._get_clob_client()
        if not client:
            return None, False, "CLOB client initialization failed"

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import SELL

            # IOC (Immediate-or-Cancel): fills whatever liquidity is available, cancels rest
            # 40% slippage below market price
            sell_price = round(max(price * 0.60, 0.01), 2)

            order = client.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=sell_price,
                    size=amount,
                    side=SELL,
                )
            )
            result = client.post_order(order, OrderType.FAK)
            order_id = result.get("orderID", str(result)[:40])
            logger.info(f"CLOB FAK order executed: {order_id}")
            return order_id, True, None
        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg and (
                "blocked" in error_msg.lower() or "restricted" in error_msg.lower()
            ):
                error_msg = "Trading restricted in your region — enable proxy"
            logger.error(f"CLOB sell error: {error_msg}")
            return None, False, error_msg

    async def buy_single_position(
        self,
        market_id: str,
        position: str,  # "YES" or "NO"
        amount: float,
        skip_clob_sell: bool = False,
    ) -> TradeResult:
        """Buy a single position on a market."""
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

        # Get market info
        market = await self.get_market_info(market_id)

        # Determine unwanted side
        unwanted_token = (
            market.no_token_id if position == "YES" else market.yes_token_id
        )
        unwanted_price = market.no_price if position == "YES" else market.yes_price

        # Split position
        try:
            split_tx = self._split_position(market.condition_id, amount)
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

        time.sleep(2)  # Wait for chain confirmation

        # Sell unwanted side
        clob_order_id = None
        clob_filled = False
        clob_error = None

        if not skip_clob_sell and unwanted_token:
            clob_order_id, clob_filled, clob_error = self._sell_via_clob(
                unwanted_token,
                amount,
                unwanted_price,
            )

        # Determine wanted token info for position recording
        wanted_token_id = (
            market.yes_token_id if position == "YES" else (market.no_token_id or "")
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
            error=clob_error,  # CLOB error if sell failed
            question=market.question,
            wanted_token_id=wanted_token_id,
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
    ) -> BuyPairResult:
        """Buy both positions in a portfolio pair."""

        # Check wallet status
        if not self.wallet.is_unlocked:
            raise ValueError("Wallet not unlocked")

        balances = self.wallet.get_balances()
        required = amount_per_position * 2

        if balances.usdc_e < required:
            raise ValueError(
                f"Insufficient USDC.e: need {required:.2f}, have {balances.usdc_e:.2f}"
            )

        # Buy target position
        logger.info(f"Buying target: {target_position} on {target_market_id}")
        target_result = await self.buy_single_position(
            target_market_id,
            target_position,
            amount_per_position,
            skip_clob_sell,
        )

        # Buy cover position
        logger.info(f"Buying cover: {cover_position} on {cover_market_id}")
        cover_result = await self.buy_single_position(
            cover_market_id,
            cover_position,
            amount_per_position,
            skip_clob_sell,
        )

        # Get final balances
        final_balances = self.wallet.get_balances()

        return BuyPairResult(
            success=target_result.success and cover_result.success,
            pair_id=pair_id,
            target=target_result,
            cover=cover_result,
            total_spent=amount_per_position * 2,
            final_balances={
                "pol": final_balances.pol,
                "usdc_e": final_balances.usdc_e,
            },
        )

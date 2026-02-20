"""Position manager - mutate positions via sell/merge operations."""

import os
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

    async def _get_market_info(self, market_id: str) -> dict:
        """Fetch market info from Polymarket API."""
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                f"https://gamma-api.polymarket.com/markets/{market_id}"
            )
            resp.raise_for_status()
            return resp.json()

    def _sell_via_clob(
        self,
        token_id: str,
        amount: float,
        price: float,
    ) -> tuple[Optional[str], bool, Optional[str]]:
        """Sell tokens via CLOB using IOC market order. Returns (order_id, filled, error)."""
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

    def _merge_tokens(
        self,
        condition_id: str,
        amount: float,
    ) -> tuple[Optional[str], Optional[str]]:
        """Merge YES+NO tokens back to USDC via CTF contract. Returns (tx_hash, error)."""
        w3 = self._get_web3()
        address = Web3.to_checksum_address(self.wallet.address)
        account = w3.eth.account.from_key(self.wallet.get_unlocked_key())

        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        amount_wei = int(amount * 1e6)
        condition_bytes = bytes.fromhex(
            condition_id[2:] if condition_id.startswith("0x") else condition_id
        )

        try:
            tx = ctf.functions.mergePositions(
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
            logger.info(f"Merge TX: {tx_hash.hex()}")

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
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
        order_id, filled, error = self._sell_via_clob(token_id, balance, price)

        # Calculate recovered value (approximate)
        sell_price = round(max(price * 0.90, 0.01), 2)
        recovered = balance * sell_price if filled else 0

        # Update storage if selling unwanted tokens
        if filled and token_type == "unwanted":
            self.storage.update_clob_status(position_id, side, order_id, True)

        return SellResult(
            success=filled,
            token_id=token_id,
            amount=balance,
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

        # Execute merge
        tx_hash, error = self._merge_tokens(condition_id, mergeable)

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

    async def retry_pending_sells(self, position_id: str) -> dict:
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
            result = await self.sell_position_tokens(position_id, "target", "unwanted")
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
            result = await self.sell_position_tokens(position_id, "cover", "unwanted")
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

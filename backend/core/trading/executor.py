"""Execute on-chain trades: split + CLOB sell."""

import json
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
    neg_risk: bool = False


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
    ctf_token_ids: Optional[list[str]] = (
        None  # Actual on-chain CTF token IDs from split
    )
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
            neg_risk=bool(data.get("negRisk", False)),
        )

    @staticmethod
    def parse_ctf_token_ids_from_receipt(receipt: dict, ctf_address: str) -> list[str]:
        """Extract minted CTF token IDs from a split TX receipt's TransferBatch event."""
        try:
            from eth_abi import decode as abi_decode

            ctf_lower = ctf_address.lower()
            # TransferBatch topic: keccak256("TransferBatch(address,address,address,uint256[],uint256[])")
            batch_topic = (
                "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
            )

            for log in receipt.get("logs", []):
                if (
                    log["address"].lower() == ctf_lower
                    and log["topics"][0].hex() == batch_topic[2:]
                ):
                    ids, _ = abi_decode(["uint256[]", "uint256[]"], log["data"])
                    return [str(tid) for tid in ids]
        except Exception as e:
            logger.warning(f"Failed to parse CTF token IDs from receipt: {e}")
        return []

    def _split_position(
        self,
        condition_id: str,
        amount_usd: float,
        neg_risk: bool = False,
    ) -> tuple[str, list[str]]:
        """Split USDC into YES + NO tokens. Returns (tx_hash, [ctf_token_ids]).

        For binary markets, splits via CTF directly.
        For NegRisk (multi-outcome) markets, splits via the NegRisk adapter
        so minted tokens match CLOB token IDs.
        """
        w3 = self._get_web3()
        address = Web3.to_checksum_address(self.wallet.address)
        account = w3.eth.account.from_key(self.wallet.get_unlocked_key())

        # NegRisk adapter has the same splitPosition(5-param) ABI as CTF
        split_contract_addr = (
            CONTRACTS["NEG_RISK_ADAPTER"] if neg_risk else CONTRACTS["CTF"]
        )
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(split_contract_addr),
            abi=CTF_ABI,
        )
        logger.info(
            f"Split via {'NegRisk adapter' if neg_risk else 'CTF'}: {split_contract_addr[:10]}..."
        )

        amount_wei = int(amount_usd * 1e6)
        condition_bytes = bytes.fromhex(
            condition_id[2:] if condition_id.startswith("0x") else condition_id
        )

        # Use 20% gas price bump to avoid TX being dropped during congestion
        base_gas_price = w3.eth.gas_price
        gas_price = int(base_gas_price * 1.2)

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
                "gasPrice": gas_price,
                "chainId": 137,
            }
        )

        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info(f"Split TX: {tx_hash.hex()} (gasPrice={gas_price})")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt["status"] != 1:
            raise ValueError(f"Split reverted on-chain: {tx_hash.hex()}")

        ctf_token_ids = self.parse_ctf_token_ids_from_receipt(receipt, CONTRACTS["CTF"])
        logger.info(f"Split minted CTF tokens: {ctf_token_ids}")

        return tx_hash.hex(), ctf_token_ids

    async def buy_single_position(
        self,
        market_id: str,
        position: str,  # "YES" or "NO"
        amount: float,
        skip_clob_sell: bool = False,
        slippage: float = 10,
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
            split_tx, ctf_token_ids = self._split_position(
                market.condition_id, amount, neg_risk=market.neg_risk
            )
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
            from core.trading.clob_client import get_clob_client

            client = get_clob_client(self.wallet)
            if client:
                from core.trading.clob import sell_via_clob

                clob_order_id, clob_filled_size, clob_error = sell_via_clob(
                    client,
                    unwanted_token,
                    amount,
                    unwanted_price,
                    slippage=slippage,
                )
                clob_filled = clob_filled_size > 0
            else:
                clob_error = "CLOB client initialization failed"

        # Determine wanted/unwanted token info for position recording
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
            error=clob_error,  # CLOB error if sell failed
            question=market.question,
            wanted_token_id=wanted_token_id,
            unwanted_token_id=unwanted_token_id,
            ctf_token_ids=ctf_token_ids,
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
            slippage=slippage,
        )

        # Buy cover position
        logger.info(f"Buying cover: {cover_position} on {cover_market_id}")
        cover_result = await self.buy_single_position(
            cover_market_id,
            cover_position,
            amount_per_position,
            skip_clob_sell,
            slippage=slippage,
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

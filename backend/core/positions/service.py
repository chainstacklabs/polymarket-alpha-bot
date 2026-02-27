"""Position service - enrich positions with live balances and prices."""

import json
from dataclasses import dataclass
from typing import Optional

import httpx
from web3 import Web3
from loguru import logger

from core.wallet.manager import WalletManager
from core.wallet.contracts import CONTRACTS, CTF_ABI
from server.price_aggregation import price_aggregation
from .storage import PositionStorage


# Multicall3 contract on Polygon
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "callData", "type": "bytes"},
                ],
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate",
        "outputs": [
            {"name": "blockNumber", "type": "uint256"},
            {"name": "returnData", "type": "bytes[]"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass
class LivePosition:
    """Position enriched with live data."""

    # Entry metadata
    position_id: str
    pair_id: str
    entry_time: str
    entry_amount_per_side: float
    entry_total_cost: float

    # Target market
    target_market_id: str
    target_position: str
    target_token_id: str
    target_question: str
    target_entry_price: float
    target_group_slug: str
    target_split_tx: str
    target_clob_order_id: Optional[str]
    target_clob_filled: bool

    # Cover market
    cover_market_id: str
    cover_position: str
    cover_token_id: str
    cover_question: str
    cover_entry_price: float
    cover_group_slug: str
    cover_split_tx: str
    cover_clob_order_id: Optional[str]
    cover_clob_filled: bool

    # Notes
    notes: Optional[str]

    # Live data - wanted token balances
    target_balance: float
    cover_balance: float
    target_current_price: float
    cover_current_price: float

    # Live data - unwanted token balances (for pending detection)
    target_unwanted_balance: float
    cover_unwanted_balance: float

    # Derived state
    state: str  # active, pending, partial, complete
    entry_net_cost: float  # Actual cost after selling unwanted tokens
    realized_proceeds: float  # USDC recovered from exit sells/merges
    current_value: float
    pnl: float
    pnl_pct: float

    # Selling in progress flags (persisted across page refresh)
    selling_target: bool
    selling_cover: bool


class PositionService:
    """Enrich positions with live blockchain and price data."""

    # TTL cache for enriched positions (avoid repeated RPC/API calls)
    CACHE_TTL_SECONDS = 2.0

    def __init__(self, storage: PositionStorage, wallet: WalletManager):
        self.storage = storage
        self.wallet = wallet
        self._token_cache: dict[str, tuple[str, str]] = {}  # market_id -> (yes, no)

        # Lazy-init Web3 and contracts
        self._w3: Optional[Web3] = None
        self._ctf = None
        self._multicall = None

        # Positions cache with TTL
        self._positions_cache: list[LivePosition] = []
        self._positions_cache_time: float = 0

    def _get_web3(self) -> Web3:
        """Get or create Web3 instance."""
        if self._w3 is None:
            self._w3 = Web3(
                Web3.HTTPProvider(self.wallet.rpc_url, request_kwargs={"timeout": 30})
            )
        return self._w3

    def _get_ctf_contract(self):
        """Get or create CTF contract instance."""
        if self._ctf is None:
            w3 = self._get_web3()
            self._ctf = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACTS["CTF"]),
                abi=CTF_ABI,
            )
        return self._ctf

    def _get_multicall_contract(self):
        """Get or create Multicall3 contract instance."""
        if self._multicall is None:
            w3 = self._get_web3()
            self._multicall = w3.eth.contract(
                address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
                abi=MULTICALL3_ABI,
            )
        return self._multicall

    def _get_balances_batch(self, token_ids: list[str]) -> list[float]:
        """Query multiple token balances in a single RPC call using Multicall3."""
        if not self.wallet.address or not token_ids:
            return [0.0] * len(token_ids)

        # Filter out empty token IDs but track their positions
        valid_indices = []
        valid_token_ids = []
        for i, tid in enumerate(token_ids):
            if tid:
                valid_indices.append(i)
                valid_token_ids.append(tid)

        if not valid_token_ids:
            return [0.0] * len(token_ids)

        try:
            ctf = self._get_ctf_contract()
            multicall = self._get_multicall_contract()
            owner = Web3.to_checksum_address(self.wallet.address)

            # Build calldata for each balanceOf call
            calls = []
            ctf_address = Web3.to_checksum_address(CONTRACTS["CTF"])
            for token_id in valid_token_ids:
                # Use the contract function to encode calldata
                calldata = ctf.functions.balanceOf(
                    owner, int(token_id)
                )._encode_transaction_data()
                calls.append((ctf_address, calldata))

            # Execute multicall
            _, return_data = multicall.functions.aggregate(calls).call()

            # Decode results
            results = [0.0] * len(token_ids)
            for i, data in enumerate(return_data):
                balance_wei = int.from_bytes(data, "big")
                results[valid_indices[i]] = balance_wei / 1e6

            return results

        except Exception as e:
            logger.error(f"Multicall failed, falling back to individual calls: {e}")
            # Fallback to individual calls
            return [self._get_token_balance_single(tid) for tid in token_ids]

    def _get_token_balance_single(self, token_id: str) -> float:
        """Query single token balance (fallback)."""
        if not self.wallet.address or not token_id:
            return 0.0

        try:
            ctf = self._get_ctf_contract()
            balance_wei = ctf.functions.balanceOf(
                Web3.to_checksum_address(self.wallet.address),
                int(token_id),
            ).call()
            return balance_wei / 1e6
        except Exception as e:
            logger.error(f"Failed to query token balance for {token_id}: {e}")
            return 0.0

    def get_market_token_ids(self, market_id: str) -> tuple[str, str]:
        """Get YES and NO token IDs for a market. Returns (yes_token_id, no_token_id)."""
        if market_id in self._token_cache:
            return self._token_cache[market_id]

        # Try TokenResolver first (fast, pre-cached on startup)
        try:
            from server.token_resolver import token_resolver

            tokens = token_resolver.get_tokens_for_market(market_id)
            if len(tokens) >= 2:
                self._token_cache[market_id] = (tokens[0], tokens[1])
                return (tokens[0], tokens[1])
        except Exception:
            pass

        # Fallback to HTTP call if not in resolver
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"https://gamma-api.polymarket.com/markets/{market_id}"
                )
                data = resp.json()
                clob_tokens = json.loads(data.get("clobTokenIds", "[]"))
                if len(clob_tokens) >= 2:
                    self._token_cache[market_id] = (clob_tokens[0], clob_tokens[1])
                    return (clob_tokens[0], clob_tokens[1])
        except Exception as e:
            logger.error(f"Failed to fetch token IDs for market {market_id}: {e}")

        return ("", "")

    @staticmethod
    def _parse_ctf_ids(raw: Optional[str]) -> list[str]:
        """Parse stored CTF token IDs JSON. Returns [YES_id, NO_id] or empty list.

        Requires exactly 2 IDs matching the splitPosition partition [1, 2].
        """
        if not raw:
            return []
        try:
            ids = json.loads(raw)
            if not isinstance(ids, list) or len(ids) != 2:
                if isinstance(ids, list) and len(ids) > 2:
                    logger.warning(f"Unexpected CTF token ID count: {len(ids)}")
                return []
            return ids
        except (ValueError, TypeError):
            return []

    def _get_unwanted_token_id(self, market_id: str, wanted_position: str) -> str:
        """Get the unwanted token ID (opposite of what we wanted)."""
        yes_token, no_token = self.get_market_token_ids(market_id)
        if wanted_position == "YES":
            return no_token
        return yes_token

    def _get_current_price(self, market_id: str, position: str) -> Optional[float]:
        """Get current price for a market position from price aggregation."""
        market_prices = price_aggregation.get_market_prices()
        market_data = market_prices.get(market_id)

        if not market_data:
            return None

        yes_price = market_data.get("yes")
        if yes_price is None:
            return None

        if position == "NO":
            return round(1 - yes_price, 4)
        return round(yes_price, 4)

    def _calculate_state(
        self,
        target_wanted: float,
        target_unwanted: float,
        cover_wanted: float,
        cover_unwanted: float,
    ) -> str:
        """Derive position state from token balances."""
        THRESHOLD = 0.01

        total_balance = target_wanted + target_unwanted + cover_wanted + cover_unwanted
        if total_balance < THRESHOLD:
            return "complete"

        if target_unwanted >= THRESHOLD or cover_unwanted >= THRESHOLD:
            return "pending"

        if target_wanted >= THRESHOLD and cover_wanted >= THRESHOLD:
            return "active"

        return "partial"

    def _calculate_pnl(
        self,
        gross_cost: float,
        net_cost: float,
        realized_proceeds: float,
        target_wanted: float,
        target_unwanted: float,
        cover_wanted: float,
        cover_unwanted: float,
        target_price: float,
        cover_price: float,
    ) -> tuple[float, float, float]:
        """Calculate current value, P&L, and P&L percentage.

        current_value = token_holdings_value + realized_proceeds (from exit sells/merges).
        effective_cost = gross_cost if unwanted tokens remain, else net_cost.
        """
        target_mergeable = min(target_wanted, target_unwanted)
        cover_mergeable = min(cover_wanted, cover_unwanted)

        target_excess_wanted = target_wanted - target_mergeable
        target_excess_unwanted = target_unwanted - target_mergeable
        cover_excess_wanted = cover_wanted - cover_mergeable
        cover_excess_unwanted = cover_unwanted - cover_mergeable

        merge_value = target_mergeable + cover_mergeable

        target_unwanted_price = 1 - target_price
        cover_unwanted_price = 1 - cover_price

        market_value = (
            (target_excess_wanted * target_price)
            + (target_excess_unwanted * target_unwanted_price)
            + (cover_excess_wanted * cover_price)
            + (cover_excess_unwanted * cover_unwanted_price)
        )

        current_value = merge_value + market_value + realized_proceeds

        has_significant_unwanted = (target_unwanted > 0.01) or (cover_unwanted > 0.01)
        effective_cost = gross_cost if has_significant_unwanted else net_cost

        pnl = current_value - effective_cost
        pnl_pct = (pnl / effective_cost * 100) if effective_cost > 0 else 0

        return round(current_value, 4), round(pnl, 4), round(pnl_pct, 2)

    def get_position(self, position_id: str) -> Optional[LivePosition]:
        """Get single position with live data."""
        entry = self.storage.get(position_id)
        if not entry:
            return None
        return self._enrich_positions([entry])[0]

    def get_all_live(self) -> list[LivePosition]:
        """Get all positions with live balances and prices (cached)."""
        import time

        now = time.time()
        if (
            self._positions_cache
            and (now - self._positions_cache_time) < self.CACHE_TTL_SECONDS
        ):
            return self._positions_cache

        entries = self.storage.load_all()
        if not entries:
            self._positions_cache = []
            self._positions_cache_time = now
            return []

        positions = self._enrich_positions(entries)
        self._positions_cache = positions
        self._positions_cache_time = now
        return positions

    def invalidate_cache(self) -> None:
        """Invalidate the positions cache (call after mutations)."""
        self._positions_cache_time = 0

    def _backfill_ctf_ids(self, entries: list[dict]) -> None:
        """Backfill missing CTF token IDs by parsing split TX receipts."""

        from core.trading.executor import TradingExecutor

        needs_save = False
        for entry in entries:
            for side in ("target", "cover"):
                ctf_key = f"{side}_ctf_token_ids"
                tx_key = f"{side}_split_tx"
                if self._parse_ctf_ids(entry.get(ctf_key, "")) or not entry.get(tx_key):
                    continue
                # Parse TX receipt for actual CTF token IDs
                try:
                    tx_hash = entry[tx_key]
                    if not tx_hash.startswith("0x"):
                        tx_hash = f"0x{tx_hash}"
                    w3 = self._get_web3()
                    receipt = w3.eth.get_transaction_receipt(tx_hash)
                    if receipt is None:
                        logger.warning(f"No receipt for TX {tx_hash}")
                        continue
                    ids = TradingExecutor.parse_ctf_token_ids_from_receipt(
                        receipt, CONTRACTS["CTF"]
                    )
                    if ids:
                        entry[ctf_key] = json.dumps(ids)
                        needs_save = True
                        logger.info(
                            f"Backfilled {ctf_key} for {entry['position_id']}: {ids}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to backfill {ctf_key}: {e}")

        if needs_save:
            self._merge_backfill(entries)

    def _merge_backfill(self, enriched_entries: list[dict]) -> list[dict]:
        """Merge backfilled CTF IDs into stored positions."""
        stored = self.storage.load_all()
        backfill_map = {
            e["position_id"]: e for e in enriched_entries if e.get("position_id")
        }
        for pos in stored:
            updated = backfill_map.get(pos.get("position_id"))
            if updated:
                for key in (
                    "target_ctf_token_ids",
                    "cover_ctf_token_ids",
                ):
                    if self._parse_ctf_ids(
                        updated.get(key, "")
                    ) and not self._parse_ctf_ids(pos.get(key, "")):
                        pos[key] = updated[key]
        self.storage.save_all(stored)
        return stored

    def _enrich_positions(self, entries: list[dict]) -> list[LivePosition]:
        """Enrich multiple positions with live data using batched queries."""
        if not entries:
            return []

        # Backfill any missing CTF token IDs from TX receipts (one-time per position)
        self._backfill_ctf_ids(entries)

        # Collect all token IDs we need to query
        token_ids = []
        token_id_map = []  # Track which position/field each token belongs to

        for i, entry in enumerate(entries):
            # Resolve token IDs: prefer on-chain CTF IDs over CLOB IDs
            # CTF IDs from split TX are [YES, NO] matching partition [1, 2]
            target_ctf = self._parse_ctf_ids(entry.get("target_ctf_token_ids", ""))
            cover_ctf = self._parse_ctf_ids(entry.get("cover_ctf_token_ids", ""))

            # Wanted tokens
            if target_ctf:
                target_wanted_id = (
                    target_ctf[0]
                    if entry["target_position"] == "YES"
                    else target_ctf[1]
                )
            else:
                target_wanted_id = entry["target_token_id"]
            token_ids.append(target_wanted_id)
            token_id_map.append((i, "target_wanted"))

            if cover_ctf:
                cover_wanted_id = (
                    cover_ctf[0] if entry["cover_position"] == "YES" else cover_ctf[1]
                )
            else:
                cover_wanted_id = entry["cover_token_id"]
            token_ids.append(cover_wanted_id)
            token_id_map.append((i, "cover_wanted"))

            # Unwanted tokens
            if target_ctf:
                target_unwanted_id = (
                    target_ctf[1]
                    if entry["target_position"] == "YES"
                    else target_ctf[0]
                )
            else:
                target_unwanted_id = entry.get(
                    "target_unwanted_token_id", ""
                ) or self._get_unwanted_token_id(
                    entry["target_market_id"], entry["target_position"]
                )
            token_ids.append(target_unwanted_id)
            token_id_map.append((i, "target_unwanted"))

            if cover_ctf:
                cover_unwanted_id = (
                    cover_ctf[1] if entry["cover_position"] == "YES" else cover_ctf[0]
                )
            else:
                cover_unwanted_id = entry.get(
                    "cover_unwanted_token_id", ""
                ) or self._get_unwanted_token_id(
                    entry["cover_market_id"], entry["cover_position"]
                )
            token_ids.append(cover_unwanted_id)
            token_id_map.append((i, "cover_unwanted"))

        # Batch query all balances in one RPC call
        balances = self._get_balances_batch(token_ids)

        # Organize balances by position
        position_balances = [{} for _ in entries]
        for (pos_idx, field), balance in zip(token_id_map, balances):
            position_balances[pos_idx][field] = balance

        # Build LivePosition objects
        results = []
        for entry, bal in zip(entries, position_balances):
            target_wanted = bal.get("target_wanted", 0.0)
            cover_wanted = bal.get("cover_wanted", 0.0)
            target_unwanted = bal.get("target_unwanted", 0.0)
            cover_unwanted = bal.get("cover_unwanted", 0.0)

            # Get current prices
            target_price = self._get_current_price(
                entry["target_market_id"], entry["target_position"]
            )
            cover_price = self._get_current_price(
                entry["cover_market_id"], entry["cover_position"]
            )

            if target_price is None:
                target_price = entry["target_entry_price"]
            if cover_price is None:
                cover_price = entry["cover_entry_price"]

            state = self._calculate_state(
                target_wanted, target_unwanted, cover_wanted, cover_unwanted
            )

            gross_cost = entry["entry_total_cost"]
            amount = entry["entry_amount_per_side"]
            net_cost = amount * (
                entry["target_entry_price"] + entry["cover_entry_price"]
            )
            realized_proceeds = entry.get("realized_proceeds", 0.0)

            current_value, pnl, pnl_pct = self._calculate_pnl(
                gross_cost,
                net_cost,
                realized_proceeds,
                target_wanted,
                target_unwanted,
                cover_wanted,
                cover_unwanted,
                target_price,
                cover_price,
            )

            results.append(
                LivePosition(
                    position_id=entry["position_id"],
                    pair_id=entry["pair_id"],
                    entry_time=entry["entry_time"],
                    entry_amount_per_side=entry["entry_amount_per_side"],
                    entry_total_cost=entry["entry_total_cost"],
                    target_market_id=entry["target_market_id"],
                    target_position=entry["target_position"],
                    target_token_id=entry["target_token_id"],
                    target_question=entry["target_question"],
                    target_entry_price=entry["target_entry_price"],
                    target_group_slug=entry.get("target_group_slug", ""),
                    target_split_tx=entry["target_split_tx"],
                    target_clob_order_id=entry.get("target_clob_order_id"),
                    target_clob_filled=entry.get("target_clob_filled", False),
                    cover_market_id=entry["cover_market_id"],
                    cover_position=entry["cover_position"],
                    cover_token_id=entry["cover_token_id"],
                    cover_question=entry["cover_question"],
                    cover_entry_price=entry["cover_entry_price"],
                    cover_group_slug=entry.get("cover_group_slug", ""),
                    cover_split_tx=entry["cover_split_tx"],
                    cover_clob_order_id=entry.get("cover_clob_order_id"),
                    cover_clob_filled=entry.get("cover_clob_filled", False),
                    notes=entry.get("notes"),
                    target_balance=round(target_wanted, 4),
                    cover_balance=round(cover_wanted, 4),
                    target_current_price=target_price,
                    cover_current_price=cover_price,
                    target_unwanted_balance=round(target_unwanted, 4),
                    cover_unwanted_balance=round(cover_unwanted, 4),
                    state=state,
                    entry_net_cost=round(net_cost, 4),
                    realized_proceeds=realized_proceeds,
                    current_value=current_value,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    selling_target=entry.get("selling_target", False),
                    selling_cover=entry.get("selling_cover", False),
                )
            )

        return results

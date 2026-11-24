"""Position service - enrich positions with live balances and prices."""

import json
from dataclasses import dataclass
from typing import Optional

import httpx
from web3 import Web3
from loguru import logger

from core.markets import outcome_token_ids
from core.paths import GAMMA_API_BASE_URL
from core.positions.redeem import (
    CTF_PAYOUT_ABI,
    OUTCOME_INDEX,
    POSITION_MANAGER_PAYOUT_ABI,
    v1_payout_rates,
    v2_payout_rate,
)
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


LEG_ROLES = (
    ("target", "wanted"),
    ("target", "unwanted"),
    ("cover", "wanted"),
    ("cover", "unwanted"),
)


def share_claim(entry: dict, side: str, role: str) -> tuple[float, bool]:
    """(shares this position leg holds, whether that's tracked exactly).

    Tracked from the buy on (`{side}_{role}_shares`). Older positions get an
    estimate from the buy: the split amount for the wanted side, and for the
    unwanted side the split amount unless its sale filled.
    """
    tracked = entry.get(f"{side}_{role}_shares")
    if tracked is not None:
        return max(float(tracked), 0.0), True
    if not entry.get(f"{side}_split_tx"):
        return 0.0, False
    amount = float(entry.get("entry_amount_per_side") or 0.0)
    if role == "unwanted" and entry.get(f"{side}_clob_filled"):
        return 0.0, False
    return amount, False


def allocate_shared(balance: float, claimants: list[tuple[float, bool]]) -> list[float]:
    """Split one token balance between the positions that hold it.

    Tracked claims are served first (scaled down if the wallet holds less),
    then older positions share what's left by their estimates, or equally if
    they have none. Never hands out more than the balance; shares nobody
    claims (e.g. bought outside the bot) stay unassigned.
    """
    if len(claimants) <= 1:
        return [balance] * len(claimants)
    out = [0.0] * len(claimants)
    tracked = [i for i, (_, t) in enumerate(claimants) if t]
    legacy = [i for i, (_, t) in enumerate(claimants) if not t]

    tracked_total = sum(claimants[i][0] for i in tracked)
    if tracked_total > 0:
        scale = min(1.0, balance / tracked_total)
        for i in tracked:
            out[i] = claimants[i][0] * scale

    rest = max(balance - sum(out), 0.0)
    if legacy and rest > 0:
        legacy_total = sum(claimants[i][0] for i in legacy)
        if legacy_total > 0:
            scale = min(1.0, rest / legacy_total)
            for i in legacy:
                out[i] = claimants[i][0] * scale
        else:
            for i in legacy:
                out[i] = rest / len(legacy)
    return out


def legacy_sale_estimate(entry: dict, side: str, unwanted_balance: float) -> float:
    """Estimated pUSD a leg's unwanted side sold for, for positions recorded
    before sale proceeds were tracked: whatever unwanted tokens are gone are
    assumed sold at (1 - entry price). No split, no sale.
    """
    if not entry.get(f"{side}_split_tx"):
        return 0.0
    gone = max(entry["entry_amount_per_side"] - unwanted_balance, 0.0)
    return round(gone * (1 - entry[f"{side}_entry_price"]), 6)


def entry_cost(
    entry: dict, target_unwanted: float, cover_unwanted: float
) -> tuple[float, bool]:
    """(cost, estimated): split pUSD minus what the unwanted sides sold for."""
    sold = {
        "target": entry.get("target_sell_proceeds"),
        "cover": entry.get("cover_sell_proceeds"),
    }
    unwanted = {"target": target_unwanted, "cover": cover_unwanted}
    estimated = bool(entry.get("cost_estimated"))
    for side, value in sold.items():
        if value is None:
            sold[side] = legacy_sale_estimate(entry, side, unwanted[side])
            estimated = True
    return entry["entry_total_cost"] - sold["target"] - sold["cover"], estimated


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
    entry_net_cost: float  # Cost after selling unwanted tokens
    realized_proceeds: float  # pUSD back from selling wanted tokens / merges
    current_value: float
    pnl: float
    pnl_pct: float

    # Selling in progress flags (persisted across page refresh)
    selling_target: bool
    selling_cover: bool

    # True for positions recorded before sale proceeds were tracked: their
    # entry_net_cost is estimated from entry prices.
    cost_estimated: bool = False

    # Market resolved on-chain: the leg is valued at its payout (Polymarket's
    # auto-redeem may already have paid it into the wallet).
    target_resolved: bool = False
    cover_resolved: bool = False


class PositionService:
    """Enrich positions with live blockchain and price data."""

    # TTL cache for enriched positions (avoid repeated RPC/API calls)
    CACHE_TTL_SECONDS = 2.0

    def __init__(self, storage: PositionStorage, wallet: WalletManager):
        self.storage = storage
        self.wallet = wallet
        self._token_cache: dict[str, tuple[str, str]] = {}  # market_id -> (yes, no)
        self._condition_cache: dict[str, str] = {}  # market_id -> conditionId
        # leg key -> (checked_at, rates); resolved rates never change
        self._payout_cache: dict[str, tuple[float, Optional[tuple[float, float]]]] = {}
        self._fee_cache: dict[str, dict] = {}  # market_id -> fee market stub

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

    @property
    def _token_holder(self) -> Optional[str]:
        """Address that holds the outcome tokens.

        Trades run through the sigtype-3 deposit wallet, so splits land the
        tokens there — never on the signing EOA (``wallet.address``).
        """
        return self.wallet.deposit_wallet_address

    @staticmethod
    def _ledger_address(protocol: str) -> str:
        """ERC-1155 contract holding a market's outcome balances.

        v1 (CTF markets) -> Conditional Tokens; v2 (Protocol V2) ->
        PositionManager. Both expose balanceOf(address,uint256).
        """
        key = "POSITION_MANAGER" if protocol == "v2" else "CTF"
        return Web3.to_checksum_address(CONTRACTS[key])

    def _get_balances_batch(
        self, token_ids: list[str], protocols: Optional[list[str]] = None
    ) -> list[float]:
        """Query multiple token balances in a single RPC call using Multicall3.

        `protocols[i]` ("v1"/"v2", default "v1") picks the ledger for token i.
        """
        protocols = protocols or ["v1"] * len(token_ids)
        if not self._token_holder or not token_ids:
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
            owner = Web3.to_checksum_address(self._token_holder)

            # Build calldata for each balanceOf call
            calls = []
            for idx, token_id in zip(valid_indices, valid_token_ids):
                # Same balanceOf(address,uint256) ABI on both ledgers
                calldata = ctf.functions.balanceOf(
                    owner, int(token_id)
                )._encode_transaction_data()
                calls.append((self._ledger_address(protocols[idx]), calldata))

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
            return [
                self._get_token_balance_single(tid, proto)
                for tid, proto in zip(token_ids, protocols)
            ]

    def read_token_balance(self, token_id: str, protocol: str = "v1") -> float:
        """Token balance of the deposit wallet. Raises if it can't be read."""
        if not self._token_holder:
            raise ValueError("No deposit wallet to read balances from")
        if not token_id:
            raise ValueError("Unknown token ID")
        ledger = self._get_web3().eth.contract(
            address=self._ledger_address(protocol), abi=CTF_ABI
        )
        balance_wei = ledger.functions.balanceOf(
            Web3.to_checksum_address(self._token_holder),
            int(token_id),
        ).call()
        return balance_wei / 1e6

    def _get_token_balance_single(self, token_id: str, protocol: str = "v1") -> float:
        """Query single token balance (fallback). 0.0 on any failure."""
        if not self._token_holder or not token_id:
            return 0.0

        try:
            return self.read_token_balance(token_id, protocol)
        except Exception as e:
            logger.error(f"Failed to query token balance for {token_id}: {e}")
            return 0.0

    def wanted_token_id(self, entry: dict, side: str) -> str:
        """Token ID of a leg's wanted side (on-chain IDs from the split first)."""
        ctf = self._parse_ctf_ids(entry.get(f"{side}_ctf_token_ids", ""))
        if ctf:
            return ctf[0] if entry[f"{side}_position"] == "YES" else ctf[1]
        return entry[f"{side}_token_id"]

    def leg_token_id(self, entry: dict, side: str, role: str) -> str:
        if role == "wanted":
            return self.wanted_token_id(entry, side)
        return self.unwanted_token_id(entry, side)

    def _token_refs(self) -> dict[str, list[tuple[str, str, str, float, bool]]]:
        """token_id -> every stored position leg that holds it:
        (position_id, side, role, claim, tracked)."""
        refs: dict[str, list] = {}
        for entry in self.storage.load_all():
            for side, role in LEG_ROLES:
                token_id = self.leg_token_id(entry, side, role)
                if token_id:
                    claim, tracked = share_claim(entry, side, role)
                    refs.setdefault(token_id, []).append(
                        (entry["position_id"], side, role, claim, tracked)
                    )
        return refs

    def _allocate(
        self, entry: dict, raw: dict[str, float], refs: dict
    ) -> dict[str, float]:
        """This position's part of each token balance it holds."""
        out = {}
        for side, role in LEG_ROLES:
            field = f"{side}_{role}"
            claimants = refs.get(self.leg_token_id(entry, side, role), [])
            key = (entry["position_id"], side, role)
            index = next((i for i, c in enumerate(claimants) if c[:3] == key), None)
            if index is None or len(claimants) <= 1:
                out[field] = raw.get(field, 0.0)
                continue
            parts = allocate_shared(
                raw.get(field, 0.0), [(c[3], c[4]) for c in claimants]
            )
            out[field] = parts[index]
        return out

    def allocated_balances(self, entry: dict) -> dict[str, float]:
        """This position's balances, read strictly (raises if a read fails)."""
        raw = {
            f"{side}_{role}": self.read_token_balance(
                self.leg_token_id(entry, side, role),
                entry.get(f"{side}_protocol") or "v1",
            )
            if entry.get(f"{side}_split_tx")
            else 0.0
            for side, role in LEG_ROLES
        }
        return self._allocate(entry, raw, self._token_refs())

    def unwanted_token_id(self, entry: dict, side: str) -> str:
        """Token ID of a leg's unwanted side (on-chain IDs from the split first)."""
        ctf = self._parse_ctf_ids(entry.get(f"{side}_ctf_token_ids", ""))
        position = entry[f"{side}_position"]
        if ctf:
            return ctf[1] if position == "YES" else ctf[0]
        stored = entry.get(f"{side}_unwanted_token_id", "")
        return stored or self._get_unwanted_token_id(
            entry[f"{side}_market_id"], position
        )

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
                    f"{GAMMA_API_BASE_URL}/markets/{market_id}"
                )
                data = resp.json()
                clob_tokens = outcome_token_ids(data)
                if len(clob_tokens) >= 2:
                    self._token_cache[market_id] = (clob_tokens[0], clob_tokens[1])
                    return (clob_tokens[0], clob_tokens[1])
        except Exception as e:
            logger.error(f"Failed to fetch token IDs for market {market_id}: {e}")

        return ("", "")

    def condition_id(self, market_id: str) -> str:
        """A market's conditionId from Gamma (cached; it never changes)."""
        if market_id not in self._condition_cache:
            with httpx.Client(timeout=10.0) as client:
                data = client.get(f"{GAMMA_API_BASE_URL}/markets/{market_id}").json()
            cid = data.get("conditionId") or ""
            if not cid:
                return ""
            self._condition_cache[market_id] = cid
        return self._condition_cache[market_id]

    def leg_payout_rates(self, entry: dict, side: str) -> Optional[tuple[float, float]]:
        """(wanted, unwanted) pUSD per share once the leg's market has resolved
        on-chain; None while unresolved. Gamma's `closed` lags resolution, so
        this reads the chain: the CTF for v1 legs, the PositionManager for v2.
        Unresolved results are re-checked after a minute.
        """
        import time

        protocol = entry.get(f"{side}_protocol") or "v1"
        wanted_id = self.leg_token_id(entry, side, "wanted")
        key = f"{protocol}:{entry[f'{side}_market_id']}:{wanted_id}"
        cached = self._payout_cache.get(key)
        if cached and (cached[1] is not None or time.time() - cached[0] < 60):
            return cached[1]

        w3 = self._get_web3()
        rates: Optional[tuple[float, float]]
        if protocol == "v2":
            pm = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACTS["POSITION_MANAGER"]),
                abi=POSITION_MANAGER_PAYOUT_ABI,
            )
            wanted = v2_payout_rate(pm, wanted_id)
            unwanted = v2_payout_rate(pm, self.leg_token_id(entry, side, "unwanted"))
            rates = None if wanted is None or unwanted is None else (wanted, unwanted)
        else:
            cid = self.condition_id(entry[f"{side}_market_id"])
            ctf = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACTS["CTF"]), abi=CTF_PAYOUT_ABI
            )
            yes_no = v1_payout_rates(ctf, cid) if cid else None
            if yes_no is None:
                rates = None
            else:
                i = OUTCOME_INDEX[entry[f"{side}_position"]]
                rates = (yes_no[i], yes_no[1 - i])
        self._payout_cache[key] = (time.time(), rates)
        return rates

    def leg_holders(self, market_id: str) -> list[tuple[dict, str]]:
        """(entry, side) of every position leg on this market that takes part
        in sharing wallet balances (see `_token_refs`)."""
        ids = {c[0] for claims in self._token_refs().values() for c in claims}
        return [
            (entry, side)
            for entry in self.storage.load_all()
            if entry["position_id"] in ids
            for side in ("target", "cover")
            if entry.get(f"{side}_market_id") == market_id
            and entry.get(f"{side}_split_tx")
        ]

    def _resolved_rates(self, entry: dict, side: str) -> Optional[tuple[float, float]]:
        """leg_payout_rates, treating a failed read as unresolved."""
        try:
            return self.leg_payout_rates(entry, side)
        except Exception as e:
            logger.debug(f"Payout read failed for {entry['position_id']} {side}: {e}")
            return None

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

    def _bulk_fetch_fee_stubs(self, market_ids: list[str]) -> None:
        """Populate self._fee_cache for the given market IDs in one batched call.

        Uses /markets/keyset?id=A&id=B&... which accepts multi-value id filter
        (verified 2026-04-15). Missing markets default to fee-exempt stubs.
        """
        uncached = [mid for mid in market_ids if mid and mid not in self._fee_cache]
        if not uncached:
            return

        # Pre-seed cache with fee-exempt stubs so partial failures still return something
        for mid in uncached:
            self._fee_cache[mid] = {"id": mid, "feesEnabled": False}

        with httpx.Client(
            base_url=GAMMA_API_BASE_URL, timeout=10.0
        ) as client:
            for i in range(0, len(uncached), 50):
                batch = uncached[i : i + 50]
                params = [("id", mid) for mid in batch]
                # Inline retry: 3 attempts with exponential backoff.
                # Sync helper — can't use fetch_json_with_retry (async).
                # Permanent 4xx (except 429) breaks early so we don't waste time.
                payload = None
                last_exc: Exception | None = None
                for attempt in range(3):
                    try:
                        resp = client.get("/markets/keyset", params=params)
                        status = resp.status_code
                        if 400 <= status < 500 and status != 429:
                            last_exc = httpx.HTTPStatusError(
                                f"HTTP {status}", request=resp.request, response=resp
                            )
                            break
                        resp.raise_for_status()
                        payload = resp.json()
                        break
                    except Exception as e:
                        last_exc = e
                        if attempt < 2:
                            import time as _time

                            _time.sleep(2**attempt)
                if payload is None:
                    logger.debug(
                        f"Bulk fee fetch failed for batch {batch[:3]}...: {last_exc}"
                    )
                    continue
                for m in payload.get("markets", []):
                    mid = str(m.get("id", ""))
                    if m.get("feesEnabled"):
                        stub: dict = {"id": mid, "feesEnabled": True}
                        if "feeSchedule" in m:
                            stub["feeSchedule"] = m["feeSchedule"]
                        self._fee_cache[mid] = stub

    def fee_stub(self, market_id: str) -> dict:
        """A market's `feesEnabled` / `feeSchedule` (fee-free if unknown)."""
        self._bulk_fetch_fee_stubs([market_id])
        return self._fee_cache.get(market_id, {"feesEnabled": False})

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
        cost: float,
        realized_proceeds: float,
        target_wanted: float,
        target_unwanted: float,
        cover_wanted: float,
        cover_unwanted: float,
        target_price: float,
        cover_price: float,
        exit_fees: float = 0.0,
    ) -> tuple[float, float, float]:
        """Calculate current value, P&L, and P&L percentage.

        current_value = token holdings at current prices + realized_proceeds
            (pUSD from selling wanted tokens and merging pairs) - exit_fees.
        cost: split pUSD - what the unwanted sides sold for (see
            `entry_cost`). Leftover unwanted tokens are valued at market.
        exit_fees: estimated taker fees if the holdings were sold at current
            prices (unrealized side only). Fees are computed at match time
            from the market's ``feeSchedule``; realized amounts are what the
            order response reported.
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

        current_value = merge_value + market_value + realized_proceeds - exit_fees

        pnl = current_value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0

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
                # Receipt parsing reads CTF TransferBatch logs; Protocol V2
                # legs always store their IDs at buy time.
                if entry.get(f"{side}_protocol") == "v2":
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
            for side, role in LEG_ROLES:
                token_ids.append(self.leg_token_id(entry, side, role))
                token_id_map.append((i, f"{side}_{role}"))

        # Ledger per token: target/cover legs can be on different protocols
        protocols = []
        for pos_idx, field in token_id_map:
            side = "target" if field.startswith("target") else "cover"
            protocols.append(entries[pos_idx].get(f"{side}_protocol") or "v1")

        # Batch query all balances in one RPC call
        balances = self._get_balances_batch(token_ids, protocols)

        # Organize balances by position
        position_balances = [{} for _ in entries]
        for (pos_idx, field), balance in zip(token_id_map, balances):
            position_balances[pos_idx][field] = balance

        # Positions on the same market share one token balance: split it
        # instead of giving each the whole wallet balance.
        refs = self._token_refs()
        position_balances = [
            self._allocate(entry, raw, refs)
            for entry, raw in zip(entries, position_balances)
        ]

        # Bulk-fetch fee stubs for all markets (single batched HTTP call)
        all_market_ids = list(
            {
                mid
                for entry in entries
                for mid in (entry["target_market_id"], entry["cover_market_id"])
                if mid
            }
        )
        self._bulk_fetch_fee_stubs(all_market_ids)

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

            # A resolved leg is worth its payout on the shares the position
            # still claims, whether they're in the wallet or were already paid
            # out by Polymarket's auto-redeem. Our own redeem clears the claim
            # and books the payout in realized_proceeds.
            target_rates = self._resolved_rates(entry, "target")
            cover_rates = self._resolved_rates(entry, "cover")

            # Worthless tokens left on a resolved leg don't keep it open.
            def _open(balance: float, rates, role: int) -> float:
                return 0.0 if rates and rates[role] == 0 else balance

            state = self._calculate_state(
                _open(target_wanted, target_rates, 0),
                _open(target_unwanted, target_rates, 1),
                _open(cover_wanted, cover_rates, 0),
                _open(cover_unwanted, cover_rates, 1),
            )

            value_tw, value_tu, value_cw, value_cu = (
                target_wanted,
                target_unwanted,
                cover_wanted,
                cover_unwanted,
            )
            if target_rates:
                value_tw = share_claim(entry, "target", "wanted")[0]
                value_tu = share_claim(entry, "target", "unwanted")[0]
                target_price = target_rates[0]
            if cover_rates:
                value_cw = share_claim(entry, "cover", "wanted")[0]
                value_cu = share_claim(entry, "cover", "unwanted")[0]
                cover_price = cover_rates[0]

            realized_proceeds = entry.get("realized_proceeds", 0.0)
            # Same holdings for cost as for value: on a resolved leg an empty
            # wallet doesn't mean the unwanted side was sold (auto-redeem).
            cost, cost_estimated = entry_cost(entry, value_tu, value_cu)

            # Estimate exit fees at current prices (unrealized P&L only --
            # realized P&L uses actual CLOB proceeds which already net fees).
            from core.fees import compute_fee

            target_fee_stub = self._fee_cache.get(
                entry["target_market_id"], {"feesEnabled": False}
            )
            cover_fee_stub = self._fee_cache.get(
                entry["cover_market_id"], {"feesEnabled": False}
            )
            # Redeeming has no fees: only unresolved legs pay one to exit.
            target_value = 0.0 if target_rates else target_wanted * target_price
            cover_value = 0.0 if cover_rates else cover_wanted * cover_price
            exit_fees = compute_fee(
                target_value, target_price, target_fee_stub
            ) + compute_fee(cover_value, cover_price, cover_fee_stub)

            current_value, pnl, pnl_pct = self._calculate_pnl(
                cost,
                realized_proceeds,
                value_tw,
                value_tu,
                value_cw,
                value_cu,
                target_price,
                cover_price,
                exit_fees=exit_fees,
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
                    entry_net_cost=round(cost, 4),
                    realized_proceeds=realized_proceeds,
                    current_value=current_value,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    selling_target=entry.get("selling_target", False),
                    selling_cover=entry.get("selling_cover", False),
                    cost_estimated=cost_estimated,
                    target_resolved=bool(target_rates),
                    cover_resolved=bool(cover_rates),
                )
            )

        return results

"""Positions on the same market share one wallet balance.

Every position used to be shown (and sold) as if it held the whole balance
of its tokens, so two positions on one market double-counted value and a
sale could take another position's shares. Each position now gets its part,
split by the shares it holds.
"""

import asyncio
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from polymarket import SecureClient

from core.positions.manager import PositionManager
from core.positions.service import PositionService, allocate_shared, share_claim
from core.positions.storage import PositionEntry, PositionStorage
from server.routers.trading import _leg_shares

# ── splitting one balance ────────────────────────────────────────────────


def test_single_holder_keeps_the_whole_balance():
    assert allocate_shared(12.0, [(10.0, True)]) == [12.0]


def test_tracked_holders_get_what_they_hold():
    assert allocate_shared(40.0, [(10.0, True), (30.0, True)]) == [10.0, 30.0]


def test_shortfall_is_shared_in_proportion():
    # Wallet holds less than recorded (e.g. sold outside the bot)
    assert allocate_shared(20.0, [(10.0, True), (30.0, True)]) == [5.0, 15.0]


def test_unclaimed_extra_stays_unassigned():
    # 5 extra shares bought outside the bot aren't any position's
    assert allocate_shared(45.0, [(10.0, True), (30.0, True)]) == [10.0, 30.0]


def test_older_positions_share_what_tracked_ones_leave():
    parts = allocate_shared(40.0, [(10.0, True), (20.0, False), (20.0, False)])
    assert parts == [10.0, 15.0, 15.0]


def test_older_positions_without_estimates_split_equally():
    assert allocate_shared(9.0, [(0.0, False), (0.0, False), (0.0, False)]) == [3.0] * 3


def test_never_hands_out_more_than_the_balance():
    parts = allocate_shared(7.0, [(10.0, True), (5.0, False), (1.0, True)])
    assert sum(parts) == pytest.approx(7.0)


# ── claims ───────────────────────────────────────────────────────────────


def test_claims_tracked_and_estimated():
    tracked = {"target_wanted_shares": 4.0, "target_split_tx": "0x1"}
    assert share_claim(tracked, "target", "wanted") == (4.0, True)

    legacy = {"entry_amount_per_side": 10.0, "target_split_tx": "0x1"}
    assert share_claim(legacy, "target", "wanted") == (10.0, False)
    assert share_claim(legacy, "target", "unwanted") == (10.0, False)
    sold = {**legacy, "target_clob_filled": True}
    assert share_claim(sold, "target", "unwanted") == (0.0, False)
    assert share_claim({"entry_amount_per_side": 10.0}, "target", "wanted") == (
        0.0,
        False,
    )


def test_buy_records_shares_per_leg():
    leg = MagicMock(split_tx="0xs", clob_filled_size=7.5)
    assert _leg_shares("target", leg, 10.0) == {
        "target_wanted_shares": 10.0,
        "target_unwanted_shares": 2.5,
    }
    failed = MagicMock(split_tx=None, clob_filled_size=0.0)
    assert _leg_shares("cover", failed, 10.0) == {
        "cover_wanted_shares": 0.0,
        "cover_unwanted_shares": 0.0,
    }


# ── live positions: no double counting ──────────────────────────────────


def _entry(position_id, target_shares, **overrides) -> PositionEntry:
    base = dict(
        position_id=position_id,
        pair_id=f"pair_{position_id}",
        entry_time="2026-11-20T00:00:00Z",
        entry_amount_per_side=target_shares,
        entry_total_cost=2 * target_shares,
        target_market_id="shared",
        target_position="YES",
        target_token_id="T_YES",
        target_unwanted_token_id="T_NO",
        target_question="Shared?",
        target_entry_price=0.5,
        cover_market_id=f"cover_{position_id}",
        cover_position="NO",
        cover_token_id=f"C_NO_{position_id}",
        cover_unwanted_token_id=f"C_YES_{position_id}",
        cover_question="Cover?",
        cover_entry_price=0.4,
        target_split_tx="0xt",
        cover_split_tx="0xc",
        target_sell_proceeds=0.0,
        cover_sell_proceeds=0.0,
        target_wanted_shares=target_shares,
        target_unwanted_shares=0.0,
        cover_wanted_shares=target_shares,
        cover_unwanted_shares=0.0,
    )
    base.update(overrides)
    return PositionEntry(**base)


WALLET = {"T_YES": 40.0, "C_NO_a": 10.0, "C_NO_b": 30.0}


def _service(tmp_path) -> PositionService:
    storage = PositionStorage(tmp_path / "positions.json")
    storage.add(_entry("a", 10.0))
    storage.add(_entry("b", 30.0))
    svc = PositionService(storage=storage, wallet=MagicMock())
    svc._backfill_ctf_ids = lambda entries: None
    svc._bulk_fetch_fee_stubs = lambda ids: None
    svc._get_current_price = lambda market_id, position: None
    svc.leg_payout_rates = lambda entry, side: None  # markets unresolved
    svc._get_balances_batch = lambda ids, protocols=None: [
        WALLET.get(t, 0.0) for t in ids
    ]
    svc.read_token_balance = lambda token_id, protocol="v1": WALLET.get(token_id, 0.0)
    return svc


def test_shared_balance_is_split_not_double_counted(tmp_path):
    svc = _service(tmp_path)
    live = {p.position_id: p for p in svc._enrich_positions(svc.storage.load_all())}
    assert (live["a"].target_balance, live["b"].target_balance) == (10.0, 30.0)
    # Unshared cover tokens are unchanged
    assert (live["a"].cover_balance, live["b"].cover_balance) == (10.0, 30.0)


def test_single_position_lookup_still_splits(tmp_path):
    svc = _service(tmp_path)
    assert svc._enrich_positions([svc.storage.get("a")])[0].target_balance == 10.0
    assert svc.allocated_balances(svc.storage.get("b"))["target_wanted"] == 30.0


# ── selling takes only this position's shares ───────────────────────────


def test_sell_takes_only_this_positions_shares(tmp_path):
    svc = _service(tmp_path)
    pm = PositionManager(MagicMock(), svc.storage, svc)
    client = create_autospec(SecureClient, instance=True)
    client.place_market_order.return_value = MagicMock(
        ok=True, order_id="0xo", making_amount=10.0, taking_amount=5.0
    )
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        asyncio.run(pm.sell_position_tokens("a", "target", "wanted"))

    assert client.place_market_order.call_args.kwargs["shares"] == 10.0  # not 40
    assert svc.storage.get("a")["target_wanted_shares"] == 0.0
    assert svc.storage.get("b")["target_wanted_shares"] == 30.0


def test_older_position_shares_are_seeded_from_its_part(tmp_path):
    storage = PositionStorage(tmp_path / "p.json")
    storage.add(_entry("a", 10.0))
    legacy = {
        f"{s}_{r}_shares": None
        for s in ("target", "cover")
        for r in ("wanted", "unwanted")
    }
    storage.add(
        _entry("b", 30.0, target_clob_filled=True, cover_clob_filled=True, **legacy)
    )
    svc = PositionService(storage=storage, wallet=MagicMock())
    svc.read_token_balance = lambda token_id, protocol="v1": WALLET.get(token_id, 0.0)

    assert PositionManager(MagicMock(), storage, svc)._start_tracking("b") is None
    saved = storage.get("b")
    # 40 shared: "a" tracks 10, so the older "b" gets the other 30
    assert saved["target_wanted_shares"] == 30.0
    assert saved["cover_wanted_shares"] == 30.0

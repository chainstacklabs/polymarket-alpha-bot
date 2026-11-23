"""P&L uses the pUSD the exchange actually paid, recorded by every sell/merge path.

- Buy: the unwanted side's sale proceeds lower the leg's cost.
- Later unwanted sales (retry / exit) add to those proceeds.
- Wanted-token sales and merges are realized proceeds.
- Positions recorded before proceeds were tracked keep the entry-price estimate.
"""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from polymarket import SecureClient

from core.positions.manager import PositionManager
from core.positions.service import PositionService, entry_cost
from core.positions.storage import PositionEntry, PositionStorage


def _entry(**overrides) -> PositionEntry:
    base = dict(
        position_id="pos1",
        pair_id="pair1",
        entry_time="2026-11-20T00:00:00Z",
        entry_amount_per_side=10.0,
        entry_total_cost=20.0,
        target_market_id="t",
        target_position="YES",
        target_token_id="111",
        target_question="T?",
        target_entry_price=0.6,
        cover_market_id="c",
        cover_position="NO",
        cover_token_id="222",
        cover_question="C?",
        cover_entry_price=0.3,
        target_split_tx="0xs1",
        cover_split_tx="0xs2",
        target_sell_proceeds=3.8,
        cover_sell_proceeds=6.9,
    )
    base.update(overrides)
    return PositionEntry(**base)


@pytest.fixture
def storage(tmp_path) -> PositionStorage:
    s = PositionStorage(tmp_path / "positions.json")
    s.add(_entry())
    return s


# ── storage ──────────────────────────────────────────────────────────────


def test_sell_proceeds_add_up_on_tracked_legs(storage):
    assert storage.add_sell_proceeds("pos1", "target", 1.2)
    assert storage.get("pos1")["target_sell_proceeds"] == 5.0


def test_sell_proceeds_skip_legacy_positions(tmp_path):
    s = PositionStorage(tmp_path / "p.json")
    s.add(_entry(target_sell_proceeds=None, cover_sell_proceeds=None))
    assert not s.add_sell_proceeds("pos1", "target", 1.2)
    assert s.get("pos1")["target_sell_proceeds"] is None


# ── manager: every sell / merge records what came back ──────────────────


def _live(**overrides):
    base = dict(
        target_market_id="t",
        target_position="YES",
        target_token_id="111",
        target_balance=10.0,
        target_unwanted_balance=0.0,
        target_current_price=0.5,
        cover_market_id="c",
        cover_position="NO",
        cover_token_id="222",
        cover_balance=10.0,
        cover_unwanted_balance=0.0,
        cover_current_price=0.4,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _manager(storage, live) -> tuple[PositionManager, MagicMock]:
    service = MagicMock()
    service.get_position.return_value = live
    service.get_market_token_ids.return_value = ("111", "999")
    pm = PositionManager(MagicMock(), storage, service)
    client = create_autospec(SecureClient, instance=True)
    resp = MagicMock(ok=True, order_id="0xo", making_amount=10.0, taking_amount=4.6)
    client.place_market_order.return_value = resp
    return pm, client


def _run(coro, client):
    with patch("core.trading.secure_client.get_secure_client", return_value=client):
        return asyncio.run(coro)


def test_wanted_sale_is_realized_at_exchange_price(storage):
    pm, client = _manager(storage, _live())
    result = _run(pm.sell_position_tokens("pos1", "target", "wanted"), client)
    # 10 shares quoted at 0.50 would be $5.00; the fill paid $4.60
    assert result.recovered_value == 4.6
    assert storage.get("pos1")["realized_proceeds"] == 4.6
    assert storage.get("pos1")["target_sell_proceeds"] == 3.8


def test_unwanted_sale_lowers_the_leg_cost(storage):
    pm, client = _manager(storage, _live(target_unwanted_balance=10.0))
    _run(pm.sell_position_tokens("pos1", "target", "unwanted"), client)
    saved = storage.get("pos1")
    assert saved["target_sell_proceeds"] == 8.4
    assert saved["realized_proceeds"] == 0.0
    assert saved["target_clob_filled"] is True


def test_merge_is_realized_at_one_pusd_per_pair(storage):
    pm, client = _manager(storage, _live(target_unwanted_balance=4.0))
    pm._get_market_info = MagicMock(return_value=_async({"conditionId": "0xc"}))
    pm._merge_tokens = MagicMock(return_value=("0xm", None))
    result = _run(pm.merge_position_tokens("pos1", "target"), client)
    assert result.success
    assert storage.get("pos1")["realized_proceeds"] == 4.0


def test_exit_records_each_step_once(storage):
    # Target: 10 wanted + 4 unwanted -> merge 4, sell 6 wanted.
    # Cover: 10 wanted -> sell 10.
    pm, client = _manager(storage, _live(target_unwanted_balance=4.0))
    pm._get_market_info = MagicMock(
        side_effect=lambda _: _async({"conditionId": "0xc"})
    )
    pm._merge_tokens = MagicMock(return_value=("0xm", None))
    client.place_market_order.side_effect = [
        MagicMock(ok=True, order_id="0x1", making_amount=6.0, taking_amount=2.9),
        MagicMock(ok=True, order_id="0x2", making_amount=10.0, taking_amount=3.7),
    ]
    result = _run(pm.exit_position("pos1"), client)
    assert result.total_recovered == round(4 + 2.9 + 3.7, 4)
    assert storage.get("pos1")["realized_proceeds"] == round(4 + 2.9 + 3.7, 4)


async def _coro(value):
    return value


def _async(value):
    return _coro(value)


# ── P&L cost ─────────────────────────────────────────────────────────────


def _pnl(cost: float, **kwargs):
    svc = PositionService(storage=MagicMock(), wallet=MagicMock())
    args = dict(
        cost=cost,
        realized_proceeds=0.0,
        target_wanted=10.0,
        target_unwanted=0.0,
        cover_wanted=10.0,
        cover_unwanted=0.0,
        target_price=0.6,
        cover_price=0.3,
    )
    args.update(kwargs)
    return svc._calculate_pnl(**args)


def test_cost_is_split_minus_recorded_sales():
    # Paid 20, unwanted sides brought back 3.8 + 6.9
    cost, estimated = entry_cost(asdict(_entry()), 0.0, 0.0)
    assert (round(cost, 6), estimated) == (9.3, False)
    value, pnl, _ = _pnl(cost)
    assert value == 9.0
    assert pnl == round(9.0 - cost, 4)


def test_leftover_unwanted_tokens_are_valued_not_charged():
    # Old logic jumped to the gross 20.00 here; cost stays what was paid.
    value, pnl, _ = _pnl(13.1, target_unwanted=10.0)
    assert value == round(9.0 + 10 * 0.4, 4)
    assert pnl == round(value - 13.1, 4)


def _legacy(**overrides) -> dict:
    return asdict(
        _entry(target_sell_proceeds=None, cover_sell_proceeds=None, **overrides)
    )


def test_legacy_cost_matches_the_old_net_estimate_when_unwanted_are_gone():
    # Old: net cost = 10 x (0.6 + 0.3) = 9
    cost, estimated = entry_cost(_legacy(), 0.0, 0.0)
    assert (round(cost, 6), estimated) == (9.0, True)


def test_legacy_cost_matches_the_old_gross_when_nothing_sold():
    cost, _ = entry_cost(_legacy(), 10.0, 10.0)
    assert cost == 20.0


def test_legacy_leg_without_split_sold_nothing():
    # Target split failed: only the cover leg (10) was paid for.
    cost, _ = entry_cost(_legacy(target_split_tx="", entry_total_cost=10.0), 0.0, 0.0)
    assert round(cost, 6) == round(10 - 10 * (1 - 0.3), 6)


# ── older positions: a fair-value sale leaves P&L unchanged ─────────────


def test_legacy_unwanted_sale_keeps_pnl(tmp_path):
    s = PositionStorage(tmp_path / "p.json")
    s.add(_entry(target_sell_proceeds=None, cover_sell_proceeds=None))
    # 10 target-unwanted left (worth 0.4 each), cover's all sold at buy time
    live = _live(target_unwanted_balance=10.0)
    before, _ = entry_cost(s.get("pos1"), 10.0, 0.0)

    pm, client = _manager(s, live)
    pm.service.unwanted_token_id.side_effect = lambda entry, side: side
    pm.service.read_token_balance.side_effect = lambda token, protocol: {
        "target": 10.0,
        "cover": 0.0,
    }[token]
    client.place_market_order.return_value = MagicMock(
        ok=True, order_id="0xo", making_amount=5.0, taking_amount=2.0
    )
    _run(pm.sell_position_tokens("pos1", "target", "unwanted"), client)

    saved = s.get("pos1")
    assert saved["cost_estimated"] is True
    after, estimated = entry_cost(saved, 5.0, 0.0)
    assert estimated
    # Cost fell by the 2.00 received; the 5 tokens sold were worth 5 x 0.4 = 2.00
    assert round(before - after, 6) == 2.0


def test_failed_balance_read_changes_nothing(tmp_path):
    # A failed read must not pass for "all unwanted sold" and seed a wrong cost.
    s = PositionStorage(tmp_path / "p.json")
    s.add(_entry(target_sell_proceeds=None, cover_sell_proceeds=None))
    pm, client = _manager(s, _live(target_unwanted_balance=10.0))
    pm.service.read_token_balance.side_effect = RuntimeError("rpc down")

    result = _run(pm.sell_position_tokens("pos1", "target", "unwanted"), client)

    assert not result.success
    assert "Nothing was changed" in result.error
    client.place_market_order.assert_not_called()
    saved = s.get("pos1")
    assert saved["target_sell_proceeds"] is None
    assert saved["cost_estimated"] is False

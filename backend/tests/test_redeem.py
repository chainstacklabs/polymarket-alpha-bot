"""Redeem of resolved positions: payout reading and the redeem flow.

The SDK client, the chain and Gamma are mocked; storage is real (tmp file).
"""

import asyncio
from unittest.mock import MagicMock, create_autospec

import pytest
from polymarket import SecureClient
from web3.exceptions import ContractLogicError

from core.positions import redeem
from core.positions.manager import PositionManager
from core.positions.storage import PositionStorage


# ── payout readers ───────────────────────────────────────────────────────


def _ctf(denominator: int, numerators: list[int]) -> MagicMock:
    ctf = MagicMock()
    ctf.functions.payoutDenominator.return_value.call.return_value = denominator
    ctf.functions.payoutNumerators.side_effect = lambda cid, i: MagicMock(
        call=MagicMock(return_value=numerators[i])
    )
    return ctf


def test_v1_rates_yes_wins():
    assert redeem.v1_payout_rates(_ctf(1, [1, 0]), "0x" + "ab" * 32) == (1.0, 0.0)


def test_v1_rates_unresolved_is_none():
    assert redeem.v1_payout_rates(_ctf(0, [0, 0]), "0x" + "ab" * 32) is None


def test_v2_rate_from_position_manager():
    pm = MagicMock()
    pm.functions.getPayout.return_value.call.return_value = 1_000_000
    assert redeem.v2_payout_rate(pm, "123") == 1.0
    pm.functions.getPayout.assert_called_with(123, 1_000_000)


def test_v2_rate_unresolved_revert_is_none():
    pm = MagicMock()
    pm.functions.getPayout.return_value.call.side_effect = ContractLogicError("x")
    assert redeem.v2_payout_rate(pm, "123") is None


# ── redeem flow ──────────────────────────────────────────────────────────


def _entry(position_id: str, market: str = "m1", protocol: str = "v1") -> dict:
    return {
        "position_id": position_id,
        "pair_id": "p",
        "target_market_id": market,
        "target_position": "YES",
        "target_token_id": "111",
        "target_unwanted_token_id": "222",
        "target_split_tx": "0xsplit",
        "target_protocol": protocol,
        "cover_market_id": "m2",
        "cover_position": "YES",
        "cover_token_id": "333",
        "cover_unwanted_token_id": "444",
        "cover_split_tx": "",
        "cover_protocol": "v1",
        "realized_proceeds": 0.0,
        "entry_amount_per_side": 5.0,
        "target_entry_price": 0.5,
        "cover_entry_price": 0.5,
        "target_sell_proceeds": 0.0,
        "cover_sell_proceeds": 0.0,
        "target_wanted_shares": 5.0,
        "target_unwanted_shares": 5.0,
    }


def _manager(tmp_path, entries, balances, rates):
    """balances: position_id -> allocated balances; rates: market -> (w, u) or None."""
    storage = PositionStorage(tmp_path / "positions.json")
    storage.save_all(entries)
    service = MagicMock()
    service.allocated_balances.side_effect = lambda e: balances[e["position_id"]]
    service.condition_id.side_effect = lambda market_id: f"0xcond-{market_id}"
    service.leg_holders.side_effect = lambda market_id: [
        (e, side)
        for e in storage.load_all()
        for side in ("target", "cover")
        if e.get(f"{side}_market_id") == market_id and e.get(f"{side}_split_tx")
    ]
    service.leg_payout_rates.side_effect = lambda entry, side: rates[
        entry[f"{side}_market_id"]
    ]
    pm = PositionManager(MagicMock(), storage, service)
    client = create_autospec(SecureClient, instance=True)
    client.redeem_positions.return_value.wait.return_value.transaction_hash = "0xr"
    pm._client = lambda: client
    return pm, storage, client


def _bal(tw=0.0, tu=0.0):
    return {
        "target_wanted": tw,
        "target_unwanted": tu,
        "cover_wanted": 0.0,
        "cover_unwanted": 0.0,
    }


def test_v1_redeem_yes_wins(tmp_path):
    pm, storage, client = _manager(
        tmp_path, [_entry("a")], {"a": _bal(5, 5)}, {"m1": (1.0, 0.0)}
    )
    result = asyncio.run(pm.redeem_position("a"))
    assert result.success is True
    assert result.payout == pytest.approx(5.0)
    assert result.tx_hashes == ["0xr"]
    client.redeem_positions.assert_called_once_with(condition_id="0xcond-m1")
    stored = storage.get("a")
    assert stored["realized_proceeds"] == pytest.approx(5.0)
    assert stored["target_wanted_shares"] == 0.0
    assert stored["target_unwanted_shares"] == 0.0


def test_unresolved_no_tx(tmp_path):
    pm, storage, client = _manager(
        tmp_path, [_entry("a")], {"a": _bal(5, 5)}, {"m1": None}
    )
    result = asyncio.run(pm.redeem_position("a"))
    assert result.success is False
    assert "not resolved" in result.error
    client.redeem_positions.assert_not_called()
    assert storage.get("a")["realized_proceeds"] == 0.0


def test_lost_leg_no_tx(tmp_path):
    pm, storage, client = _manager(
        tmp_path, [_entry("a")], {"a": _bal(5, 0)}, {"m1": (0.0, 1.0)}
    )
    result = asyncio.run(pm.redeem_position("a"))
    assert result.success is True
    assert result.payout == 0.0
    client.redeem_positions.assert_not_called()


def test_nothing_held_is_an_error(tmp_path):
    pm, _, client = _manager(tmp_path, [_entry("a")], {"a": _bal()}, {"m1": (1, 0)})
    result = asyncio.run(pm.redeem_position("a"))
    assert result.success is False
    client.redeem_positions.assert_not_called()


def test_unknown_position(tmp_path):
    pm, _, _ = _manager(tmp_path, [], {}, {})
    result = asyncio.run(pm.redeem_position("nope"))
    assert result.success is False
    assert result.error == "Position not found"


def test_shared_market_credits_each_position_its_part(tmp_path):
    # Both positions hold YES on m1; one redeem tx pays out the whole wallet
    # balance, so each position is credited only its own allocated part.
    pm, storage, client = _manager(
        tmp_path,
        [_entry("a"), _entry("b")],
        {"a": _bal(3, 0), "b": _bal(2, 0)},
        {"m1": (1.0, 0.0)},
    )
    result = asyncio.run(pm.redeem_position("a"))
    assert result.payout == pytest.approx(3.0)
    client.redeem_positions.assert_called_once()
    assert storage.get("a")["realized_proceeds"] == pytest.approx(3.0)
    assert storage.get("b")["realized_proceeds"] == pytest.approx(2.0)
    assert storage.get("b")["target_wanted_shares"] == 0.0


def _rates_service(contract):
    from core.positions.service import PositionService

    svc = PositionService(storage=MagicMock(), wallet=MagicMock())
    w3 = MagicMock()
    w3.eth.contract.return_value = contract
    svc._get_web3 = lambda: w3
    svc.condition_id = lambda market_id: "0x" + "ab" * 32
    return svc


def test_leg_rates_v1_maps_no_position():
    # A NO leg's wanted rate is the NO numerator (index 1).
    svc = _rates_service(_ctf(1, [1, 0]))
    entry = _entry("a") | {"target_position": "NO"}
    assert svc.leg_payout_rates(entry, "target") == (0.0, 1.0)


def test_leg_rates_v2_unresolved():
    pm = MagicMock()
    pm.functions.getPayout.return_value.call.side_effect = ContractLogicError("x")
    svc = _rates_service(pm)
    assert svc.leg_payout_rates(_entry("a", protocol="v2"), "target") is None


def test_resolved_rates_are_cached():
    ctf = _ctf(1, [1, 0])
    svc = _rates_service(ctf)
    for _ in range(3):
        svc.leg_payout_rates(_entry("a"), "target")
    assert ctf.functions.payoutDenominator.call_count == 1


# ── valuation: resolved legs are worth their payout ──────────────────────


def _pair(**overrides) -> dict:
    """A $1-per-side pair that kept both sides (skip_clob_sell)."""
    base = dict(
        position_id="pos",
        pair_id="pair",
        entry_time="2026-11-23T20:20:00Z",
        entry_amount_per_side=1.0,
        entry_total_cost=2.0,
        target_market_id="t",
        target_position="YES",
        target_token_id="111",
        target_unwanted_token_id="112",
        target_question="T?",
        target_entry_price=0.5,
        cover_market_id="c",
        cover_position="YES",
        cover_token_id="221",
        cover_unwanted_token_id="222",
        cover_question="C?",
        cover_entry_price=0.5,
        target_split_tx="0xs1",
        cover_split_tx="0xs2",
        target_sell_proceeds=0.0,
        cover_sell_proceeds=0.0,
        target_wanted_shares=1.0,
        target_unwanted_shares=1.0,
        cover_wanted_shares=1.0,
        cover_unwanted_shares=1.0,
        realized_proceeds=0.0,
    )
    base.update(overrides)
    return base


def _service(tmp_path, entry: dict, chain: list[float], rates: dict):
    from core.positions.service import PositionService

    storage = PositionStorage(tmp_path / "positions.json")
    storage.save_all([entry])
    svc = PositionService(storage=storage, wallet=MagicMock())
    svc._backfill_ctf_ids = lambda entries: None
    svc._bulk_fetch_fee_stubs = lambda ids: None
    svc._get_balances_batch = lambda tokens, protocols=None: list(chain)
    svc.leg_payout_rates = lambda e, side: rates[side]
    return svc


def test_auto_redeemed_legs_are_valued_at_payout(tmp_path):
    # Polymarket's auto-redeem operator emptied the wallet: chain balances are
    # 0, but each leg's YES+NO pair paid $1. P&L must be 0, not -$2.
    svc = _service(
        tmp_path, _pair(), [0.0] * 4, {"target": (0.0, 1.0), "cover": (1.0, 0.0)}
    )
    p = svc.get_position("pos")
    assert p.current_value == pytest.approx(2.0)
    assert p.pnl == pytest.approx(0.0)
    assert p.target_resolved and p.cover_resolved
    assert p.state == "complete"


def test_resolved_loser_is_worth_zero_not_last_price(tmp_path):
    # Only the losing YES is left on the cover leg (its NO was auto-redeemed):
    # value is the $1 the pair paid, not 1 YES at the last ~0.5 price.
    entry = _pair(target_wanted_shares=0.0, target_unwanted_shares=0.0)
    svc = _service(
        tmp_path, entry, [0.0, 0.0, 1.0, 0.0], {"target": None, "cover": (0.0, 1.0)}
    )
    p = svc.get_position("pos")
    assert p.current_value == pytest.approx(1.0)
    assert not p.target_resolved and p.cover_resolved


def test_app_redeem_is_not_counted_twice(tmp_path):
    # After our own redeem: shares cleared, payout in realized_proceeds.
    entry = _pair(
        target_wanted_shares=0.0,
        target_unwanted_shares=0.0,
        cover_wanted_shares=0.0,
        cover_unwanted_shares=0.0,
        realized_proceeds=2.0,
    )
    svc = _service(
        tmp_path, entry, [0.0] * 4, {"target": (0.0, 1.0), "cover": (1.0, 0.0)}
    )
    assert svc.get_position("pos").current_value == pytest.approx(2.0)


def test_worthless_leftover_on_resolved_leg_is_not_open(tmp_path):
    # Live 2026-11-23: after auto-redeem only the losing YES (1 share) was
    # left; the position stayed "partial" forever.
    entry = _pair(target_wanted_shares=0.0, target_unwanted_shares=0.0)
    svc = _service(
        tmp_path,
        entry,
        [0.0, 0.0, 1.0, 0.0],
        {"target": (0.0, 1.0), "cover": (0.0, 1.0)},
    )
    assert svc.get_position("pos").state == "complete"


def test_unredeemed_winner_keeps_position_open(tmp_path):
    svc = _service(
        tmp_path,
        _pair(),
        [0.0, 0.0, 1.0, 0.0],
        {"target": (0.0, 1.0), "cover": (1.0, 0.0)},
    )
    assert svc.get_position("pos").state != "complete"


def test_redeem_credits_only_listed_positions(tmp_path):
    # Another wallet's position on the same market (not listed by the
    # service) must not be credited from this wallet's redeem.
    pm, storage, _ = _manager(
        tmp_path,
        [_entry("a"), _entry("other")],
        {"a": _bal(5, 0), "other": _bal(5, 0)},
        {"m1": (1.0, 0.0)},
    )
    pm.service.leg_holders.side_effect = lambda market_id: [
        (storage.get("a"), "target")
    ]
    asyncio.run(pm.redeem_position("a"))
    assert storage.get("a")["realized_proceeds"] == pytest.approx(5.0)
    assert storage.get("other")["realized_proceeds"] == 0.0
    assert storage.get("other")["target_wanted_shares"] == 5.0


def test_leg_holders_follow_token_refs(tmp_path):
    from core.positions.service import PositionService

    storage = PositionStorage(tmp_path / "positions.json")
    storage.save_all([_entry("a"), _entry("b")])
    svc = PositionService(storage=storage, wallet=MagicMock())
    svc._token_refs = lambda: {"111": [("a", "target", "wanted", 5.0, True)]}
    assert [(e["position_id"], s) for e, s in svc.leg_holders("m1")] == [
        ("a", "target")
    ]


def test_legacy_unsold_leftover_is_not_counted_as_sold(tmp_path):
    # Older position (no tracked shares or sale proceeds); the unwanted sale
    # never filled. NO won and auto-redeem emptied the wallet: true P&L is 0.
    entry = _pair(
        entry_amount_per_side=10.0,
        entry_total_cost=20.0,
        target_sell_proceeds=None,
        cover_sell_proceeds=None,
        target_wanted_shares=None,
        target_unwanted_shares=None,
        cover_wanted_shares=None,
        cover_unwanted_shares=None,
        target_clob_filled=False,
        cover_clob_filled=False,
    )
    svc = _service(
        tmp_path, entry, [0.0] * 4, {"target": (0.0, 1.0), "cover": (0.0, 1.0)}
    )
    p = svc.get_position("pos")
    assert p.current_value == pytest.approx(20.0)
    assert p.pnl == pytest.approx(0.0)


# ── CodeRabbit review on #84 ─────────────────────────────────────────────


def test_same_market_legs_are_redeemed_and_credited_once(tmp_path):
    # Both legs on m1 (POST /positions doesn't reject that): one redeem pays
    # both, and the second pass must not credit them again.
    entry = _entry("a")
    entry.update(
        cover_market_id="m1",
        cover_split_tx="0xsplit2",
        cover_wanted_shares=5.0,
        cover_unwanted_shares=0.0,
    )
    bal = _bal(5, 0) | {"cover_wanted": 5.0}
    pm, storage, client = _manager(tmp_path, [entry], {"a": bal}, {"m1": (1.0, 0.0)})
    result = asyncio.run(pm.redeem_position("a"))
    client.redeem_positions.assert_called_once()
    assert result.payout == pytest.approx(10.0)
    assert storage.get("a")["realized_proceeds"] == pytest.approx(10.0)


def _legacy(position_id: str) -> dict:
    """Recorded before shares and sale proceeds were tracked; kept both sides."""
    entry = _entry(position_id)
    entry.update(
        entry_amount_per_side=5.0,
        entry_total_cost=5.0,
        target_entry_price=0.5,
        target_clob_filled=False,
        target_sell_proceeds=None,
        target_wanted_shares=None,
        target_unwanted_shares=None,
    )
    return entry


def test_redeem_freezes_legacy_sale_estimate(tmp_path):
    # Before the redeem the kept NO side counts as unsold (estimate 0). Once
    # the shares are cleared, the estimate must not turn into a phantom sale.
    pm, storage, _ = _manager(
        tmp_path, [_legacy("a")], {"a": _bal(5, 5)}, {"m1": (1.0, 0.0)}
    )
    asyncio.run(pm.redeem_position("a"))
    stored = storage.get("a")
    assert stored["target_sell_proceeds"] == 0.0
    assert stored["realized_proceeds"] == pytest.approx(5.0)


def test_redeem_books_payout_and_clears_claim_in_one_write(tmp_path):
    # A failed write must not leave the payout booked with the claim kept
    # (that would count the payout twice).
    pm, storage, _ = _manager(
        tmp_path, [_entry("a")], {"a": _bal(5, 0)}, {"m1": (1.0, 0.0)}
    )
    real_save, writes = storage.save_all, []

    def save_once(positions):
        if writes:
            raise OSError("disk full")
        writes.append(1)
        real_save(positions)

    storage.save_all = save_once
    try:
        asyncio.run(pm.redeem_position("a"))
    except OSError:
        pass
    stored = storage.get("a")
    booked = stored["realized_proceeds"] > 0
    cleared = stored["target_wanted_shares"] == 0.0
    assert booked == cleared


def test_start_tracking_keeps_resolved_claims(tmp_path):
    # Target resolved and auto-redeemed (wallet empty); seeding from wallet
    # balances would zero its claim and record the kept side as sold.
    pm, storage, _ = _manager(
        tmp_path,
        [_legacy("a")],
        {"a": _bal(0, 0) | {"cover_wanted": 3.0}},
        {"m1": (0.0, 1.0), "m2": None},
    )
    assert pm._start_tracking("a") is None
    stored = storage.get("a")
    assert stored["target_wanted_shares"] == 5.0
    assert stored["target_unwanted_shares"] == 5.0
    assert stored["target_sell_proceeds"] == 0.0
    assert stored["cover_wanted_shares"] == 3.0  # unresolved: from balances


def test_relayer_wait_runs_off_the_event_loop(tmp_path):
    """The relayer confirms in seconds; waiting on the event loop would stall
    every other request (positions polling included) until then."""
    import threading

    pm, _, client = _manager(
        tmp_path, [_entry("a")], {"a": _bal(5, 5)}, {"m1": (1.0, 0.0)}
    )
    threads = []

    def wait():
        threads.append(threading.current_thread())
        return MagicMock(transaction_hash="0xr")

    client.redeem_positions.return_value.wait.side_effect = wait
    result = asyncio.run(pm.redeem_position("a"))
    assert result.tx_hashes == ["0xr"]
    assert threads and threads[0] is not threading.main_thread()

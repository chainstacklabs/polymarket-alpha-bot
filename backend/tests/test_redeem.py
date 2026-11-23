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
        "target_wanted_shares": 5.0,
        "target_unwanted_shares": 5.0,
    }


def _manager(tmp_path, entries, balances, rates):
    """balances: position_id -> allocated balances; rates: market -> (w, u) or None."""
    storage = PositionStorage(tmp_path / "positions.json")
    storage.save_all(entries)
    service = MagicMock()
    service.allocated_balances.side_effect = lambda e: balances[e["position_id"]]
    pm = PositionManager(MagicMock(), storage, service)

    async def market_info(market_id):
        return {"conditionId": f"0xcond-{market_id}"}

    pm._get_market_info = market_info
    pm._leg_rates = lambda entry, side, condition_id: rates[entry[f"{side}_market_id"]]
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


def test_leg_rates_v1_maps_no_position(tmp_path):
    # A NO leg's wanted rate is the NO numerator (index 1).
    service = MagicMock()
    service._get_web3.return_value.eth.contract.return_value = _ctf(1, [1, 0])
    pm = PositionManager(MagicMock(), MagicMock(), service)
    entry = _entry("a") | {"target_position": "NO"}
    assert pm._leg_rates(entry, "target", "0x" + "ab" * 32) == (0.0, 1.0)


def test_leg_rates_v2_unresolved(tmp_path):
    service = MagicMock()
    contract = service._get_web3.return_value.eth.contract.return_value
    contract.functions.getPayout.return_value.call.side_effect = ContractLogicError("x")
    service.leg_token_id.return_value = "123"
    pm = PositionManager(MagicMock(), MagicMock(), service)
    assert pm._leg_rates(_entry("a", protocol="v2"), "target", "0xc") is None

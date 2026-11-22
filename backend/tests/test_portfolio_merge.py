"""Incremental runs must add portfolios, not replace the whole set.

Incremental expansion/validation only returns NEW pairs, and cached
validated pairs carry no prices. Rebuilding from either alone wiped every
portfolio found by earlier runs.
"""

from core.state import PipelineState
from core.steps.portfolios import build_and_save_portfolios, merge_and_save_portfolios


def _pair(pair_id: str, target_price: float = 0.6, cover_yes: float = 0.38) -> dict:
    # coverage = P(target) + P(not target) * cover_probability = 0.6 + 0.4*0.98
    return {
        "pair_id": pair_id,
        "target_group_id": f"g_{pair_id}_t",
        "target_market_id": f"m_{pair_id}_t",
        "target_position": "YES",
        "target_price": target_price,
        "cover_group_id": f"g_{pair_id}_c",
        "cover_market_id": f"m_{pair_id}_c",
        "cover_position": "YES",
        "cover_price_yes": cover_yes,
        "cover_probability": 0.98,
        "_validation": {"viability_score": 0.95},
    }


def _state(tmp_path) -> PipelineState:
    return PipelineState(db_path=tmp_path / "state.db")


def test_incremental_keeps_earlier_portfolios(tmp_path):
    state = _state(tmp_path)
    build_and_save_portfolios([_pair("a"), _pair("b")], state)
    assert {p["pair_id"] for p in state.get_portfolios()} == {"a", "b"}

    portfolios, summary = merge_and_save_portfolios([_pair("c")], state)

    ids = {p["pair_id"] for p in state.get_portfolios()}
    assert ids == {"a", "b", "c"}
    assert {p["pair_id"] for p in portfolios} == ids
    assert summary["total_portfolios"] == 3


def test_new_pair_replaces_same_pair_id(tmp_path):
    state = _state(tmp_path)
    build_and_save_portfolios([_pair("a", target_price=0.6)], state)

    merge_and_save_portfolios([_pair("a", target_price=0.55)], state)

    saved = state.get_portfolios()
    assert len(saved) == 1
    assert saved[0]["target_price"] == 0.55


def test_merge_with_nothing_buildable_keeps_existing(tmp_path):
    state = _state(tmp_path)
    build_and_save_portfolios([_pair("a")], state)

    # A pair with no prices (like a cached validated pair) builds nothing
    unpriced = {**_pair("x"), "target_price": 0, "cover_price_yes": 0}
    merge_and_save_portfolios([unpriced], state)

    assert {p["pair_id"] for p in state.get_portfolios()} == {"a"}

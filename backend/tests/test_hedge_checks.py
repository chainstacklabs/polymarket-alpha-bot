"""Deterministic hedge checks (issue #62): numeric ladders, cost, prompt versioning.

A pair is a valid cover only if, whenever the target position loses, the
cover position wins. These checks catch pairs where some outcome makes
both legs lose, without an LLM.
"""

import asyncio

import pytest

from core.state import PipelineState
from core.steps import validate
from core.steps.expand import (
    expand_implication_to_pairs,
    get_cover_markets,
    is_negrisk_exclusive,
)
from core.steps.hedge_checks import (
    numeric_both_lose,
    pair_total_cost,
    parse_numeric_outcome,
)
from core.steps.portfolios import build_and_save_portfolios, merge_and_save_portfolios

# ── parsing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("question", "lo", "hi", "integer", "percent"),
    [
        (
            "Will Trump deport less than 200k people?",
            float("-inf"),
            200_000,
            True,
            False,
        ),
        ("Will Trump deport 200-300k people?", 200_000, 300_000, True, False),
        ("Will Trump sell 101-1k Gold Cards in 2026?", 101, 1000, True, False),
        ("Will Trump sell 1k-2.5k Gold Cards in 2026?", 1000, 2500, True, False),
        (
            "Will the Republican Party hold between 195 and 199 House seats after the 2026 midterm elections?",
            195,
            199,
            True,
            False,
        ),
        (
            "Will the Republican Party hold 47 or fewer Senate seats after the 2026 midterm elections?",
            float("-inf"),
            47,
            True,
            False,
        ),
        (
            "Will the Republican Party hold exactly 49 Senate seats?",
            49,
            49,
            True,
            False,
        ),
        (
            "Will the 10-year Treasury yield dip below 3.0% before 2027?",
            float("-inf"),
            3.0,
            False,
            True,
        ),
        (
            "Will there be ≥12,500 measles cases in the US in 2026?",
            12_500,
            float("inf"),
            True,
            False,
        ),
        ("10.0+ earthquake by June 30?", 10.0, float("inf"), False, False),
    ],
)
def test_parses_real_question_styles(question, lo, hi, integer, percent):
    outcome = parse_numeric_outcome(question)
    assert (outcome.interval.lo, outcome.interval.hi) == (lo, hi)
    assert (outcome.integer, outcome.percent) == (integer, percent)


@pytest.mark.parametrize(
    "question",
    [
        "Will Trump's approval rating hit 35% in 2026?",  # direction unclear
        "Will Trump attend 2 World Cup matches?",  # bare number
        "NATO x Russia military clash by June 30, 2026?",  # only a date
        "Will the 2025-26 season end early?",  # season label, not a range
        "Will Trump deport less than 200k people and more than 50k?",  # two comparisons
    ],
)
def test_skips_questions_that_arent_clearly_one_number(question):
    assert parse_numeric_outcome(question) is None


def test_template_ignores_how_the_number_is_written():
    a = parse_numeric_outcome(
        "Will the Republican Party hold 47 or fewer Senate seats?"
    )
    b = parse_numeric_outcome("Will the Republican Party hold exactly 49 Senate seats?")
    assert a.template == b.template


# ── both-lose ────────────────────────────────────────────────────────────


def _pair(target, t_pos, cover, c_pos, **extra):
    return dict(
        target_question=target,
        target_position=t_pos,
        cover_question=cover,
        cover_position=c_pos,
        **extra,
    )


MEASLES_HI = "Will there be ≥12,500 measles cases in the US in 2026?"
MEASLES_LO = "Will there be ≥2,300 measles cases in the US in 2026?"


def test_issue_threshold_ladder_is_rejected():
    # Issue #62 example 1: 5,000 cases -> both lose
    result = numeric_both_lose(_pair(MEASLES_HI, "YES", MEASLES_LO, "NO"))
    assert result and "both lose" in result


def test_issue_inverted_magnitude_is_rejected():
    # Issue #62 example 2: a 9.5 quake -> both lose
    pair = _pair(
        "10.0+ earthquake by June 30?", "YES", "9.0+ earthquake by June 30?", "NO"
    )
    assert numeric_both_lose(pair)


@pytest.mark.parametrize(
    ("t_q", "t_pos", "c_q", "c_pos"),
    [
        (MEASLES_HI, "NO", MEASLES_LO, "YES"),  # >=12,500 implies >=2,300
        (MEASLES_LO, "YES", MEASLES_HI, "NO"),  # <2,300 implies <12,500
    ],
)
def test_valid_ladders_pass(t_q, t_pos, c_q, c_pos):
    assert numeric_both_lose(_pair(t_q, t_pos, c_q, c_pos)) is None


def test_adjacent_bins_leave_a_gap_at_the_boundary():
    # Exactly 200k: "less than 200k" YES loses and "200-300k" NO loses.
    result = numeric_both_lose(
        _pair(
            "Will Trump deport less than 200k people?",
            "YES",
            "Will Trump deport 200-300k people?",
            "NO",
        )
    )
    assert result.startswith("at 200,000 ")


def test_counts_only_test_whole_values():
    # Seats <= 47 vs exactly 49: target NO loses at 47 or below; cover only wins
    # at 49. Both lose at 47 - a whole number, not 47.5.
    result = numeric_both_lose(
        _pair(
            "Will the Republican Party hold 47 or fewer Senate seats?",
            "NO",
            "Will the Republican Party hold exactly 49 Senate seats?",
            "YES",
        )
    )
    assert result.startswith("at 47 ")


def test_different_questions_are_left_to_the_llm():
    pair = _pair(
        "Will there be ≥12,500 measles cases in the US in 2026?",
        "YES",
        "Will there be ≥2,300 measles cases in Canada in 2026?",
        "NO",
    )
    assert numeric_both_lose(pair) is None


# ── pre-filter and cost ──────────────────────────────────────────────────


def _candidate(pair_id, target_price=0.4, cover_price_no=0.5, **questions):
    base = dict(
        pair_id=pair_id,
        target_group_id=f"tg_{pair_id}",
        target_market_id=f"tm_{pair_id}",
        target_position="YES",
        target_price=target_price,
        target_question="Will X happen by June 30?",
        cover_group_id=f"cg_{pair_id}",
        cover_market_id=f"cm_{pair_id}",
        cover_position="NO",
        cover_price_yes=1 - cover_price_no,
        cover_price_no=cover_price_no,
        cover_question="Will Y happen by June 30?",
        cover_probability=0.98,
        relationship="X implies Y",
    )
    base.update(questions)
    return base


def test_pre_filter_rejects_numeric_both_lose():
    bad = _candidate("bad", target_question=MEASLES_HI, cover_question=MEASLES_LO)
    kept, stats = validate.pre_filter_pairs([bad, _candidate("ok")])
    assert [p["pair_id"] for p in kept] == ["ok"]
    assert stats["numeric_both_lose"] == 1


def test_cost_uses_the_cover_position_price():
    assert pair_total_cost(_candidate("a", 0.4, 0.5)) == pytest.approx(0.9)
    assert pair_total_cost({"target_price": 0.4}) is None


def test_costly_pairs_skip_the_llm():
    kept, skipped = validate.skip_costly_pairs(
        [_candidate("cheap", 0.4, 0.5), _candidate("dear", 0.6, 0.5)]
    )
    assert [p["pair_id"] for p in kept] == ["cheap"]
    assert skipped == 1


# ── cache: prompt version ────────────────────────────────────────────────


def _verdict(pair):
    return {
        "pair_id": pair["pair_id"],
        "target_group_id": pair["target_group_id"],
        "target_market_id": pair["target_market_id"],
        "target_position": pair["target_position"],
        "cover_group_id": pair["cover_group_id"],
        "cover_market_id": pair["cover_market_id"],
        "cover_position": pair["cover_position"],
        "cover_probability": 0.98,
        "viability_score": 0.95,
        "is_valid": True,
        "validation_reason": "ok",
    }


def test_prompt_version_round_trips(tmp_path):
    state = PipelineState(db_path=tmp_path / "state.db")
    pair = _candidate("a")
    state.add_validated_pairs([_verdict(pair)], "model", prompt_version=7)
    assert state.get_validated_pair("a")["prompt_version"] == 7


def test_older_verdicts_are_revalidated_and_costly_ones_skip_the_llm(
    tmp_path, monkeypatch
):
    state = PipelineState(db_path=tmp_path / "state.db")
    current = validate.VALIDATION_PROMPT_VERSION
    fresh, stale, new, dear = (
        _candidate("fresh"),
        _candidate("stale"),
        _candidate("new"),
        _candidate("dear", 0.6, 0.5),
    )
    state.add_validated_pairs([_verdict(fresh)], "m", prompt_version=current)
    state.add_validated_pairs([_verdict(stale)], "m", prompt_version=None)

    sent = []

    async def fake_batch(batch, model, batch_num):
        sent.extend(p["pair_id"] for p in batch)
        return {
            p["pair_id"]: {
                "is_valid": True,
                "viability_score": 0.95,
                "brief_analysis": "ok",
            }
            for p in batch
        }

    monkeypatch.setattr(validate, "validate_batch", fake_batch)
    validated, summary = asyncio.run(
        validate.validate_pairs([fresh, stale, new, dear], state, llm_model="m")
    )

    assert sorted(sent) == ["new", "stale"]  # fresh reused, dear never sent
    assert {p["pair_id"] for p in validated} == {"fresh", "stale", "new"}
    assert summary["pre_filter_reasons"]["cost_not_below_one"] == 1
    assert summary["total_candidates"] == 4
    assert state.get_validated_pair("stale")["prompt_version"] == current


def test_structural_checks_apply_to_cached_verdicts(tmp_path, monkeypatch):
    state = PipelineState(db_path=tmp_path / "state.db")
    bad = _candidate("bad", target_question=MEASLES_HI, cover_question=MEASLES_LO)
    state.add_validated_pairs(
        [_verdict(bad)], "m", prompt_version=validate.VALIDATION_PROMPT_VERSION
    )
    validated, summary = asyncio.run(
        validate.validate_pairs([bad], state, llm_model="m")
    )
    assert validated == []
    assert summary["pre_filter_reasons"]["numeric_both_lose"] == 1


# ── stored portfolios ────────────────────────────────────────────────────


def _validated(pair_id, **questions):
    pair = _candidate(pair_id, 0.4, 0.5, **questions)
    pair["_validation"] = {"viability_score": 0.95}
    return pair


def test_incremental_merge_drops_earlier_both_lose_portfolios(tmp_path):
    state = PipelineState(db_path=tmp_path / "state.db")
    build_and_save_portfolios(
        [
            _validated("bad", target_question=MEASLES_HI, cover_question=MEASLES_LO),
            _validated("ok"),
        ],
        state,
    )
    merge_and_save_portfolios([_validated("new")], state)
    assert {p["pair_id"] for p in state.get_portfolios()} == {"ok", "new"}


# ── negRisk groups (exactly one outcome resolves YES) ────────────────────


def _group(group_id, prices, partition="candidate"):
    return {
        "group_id": group_id,
        "title": group_id,
        "partition_type": partition,
        "markets": [
            {
                "id": f"{group_id}_{i}",
                "question": f"{group_id} {i}?",
                "price_yes": p,
                "price_no": 1 - p,
            }
            for i, p in enumerate(prices)
        ],
    }


def test_negrisk_detection():
    assert is_negrisk_exclusive(_group("meet", [0.5, 0.3, 0.2]))  # sums to 1
    assert not is_negrisk_exclusive(_group("cities", [0.6, 0.5, 0.4]))  # multi-select
    assert not is_negrisk_exclusive(_group("ladder", [0.5, 0.3, 0.2], "threshold"))
    assert not is_negrisk_exclusive(_group("one", [1.0]))


def test_negrisk_outcomes_are_not_used_cross_group():
    meet = _group("meet", [0.5, 0.3, 0.2])
    other = _group("other", [0.4], partition="single")
    groups = {"meet": meet, "other": other}
    cover_info = {"group_id": "meet", "probability": 0.98, "cover_position": "YES"}
    assert get_cover_markets(cover_info, groups, "other") == []

    impl = {"group_id": "meet", "yes_covered_by": [{**cover_info, "group_id": "other"}]}
    assert expand_implication_to_pairs(impl, groups) == []

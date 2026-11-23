"""
06: Calculate Portfolio Metrics and Classify Hedges.

WHAT IT DOES
    Turns validated pairs into portfolios with trading numbers: total cost,
    coverage (chance of getting $1 back), expected profit, taker fees and
    a quality tier. Mirrors backend/core/steps/portfolios.py (build_portfolios)
    and core/fees.py (logic copied, not imported).

WHY WE NEED THIS
    Step 05 found pairs that work logically. This step answers "is it worth
    buying at current prices?"

HOW A HEDGE WORKS
    Buy two positions at once:
        TARGET "Region NOT captured" @ $0.80
        COVER  "City captured"       @ $0.15   (pays when the target loses)
        Total cost: $0.95. One of them pays $1 unless the cover fails.

    Coverage = P(target wins) + P(target loses) x P(cover fires | target loses)
             = 0.80 + 0.20 x 0.98 = 99.6%
    Expected profit = coverage - cost = 0.996 - 0.95 = +$0.046
    Profit (if covered) = $1 - cost - taker fees on both legs

    Fees: fee = notional x rate x price x (1 - price) per fee-bearing leg,
    using the feesEnabled/feeSchedule fields that step 04 copies onto pairs.

HOW IT WORKS
    1. cost = target price + cover price (YES or NO side); skip if not in
       (0, MAX_COST]
    2. coverage, loss probability, expected profit; drop coverage < 85%
    3. tier: 1 >= 95% (near-arbitrage), 2 >= 90%, 3 >= 85%
    4. fees, profit, profit_pct; sort by tier, then coverage
    5. Sanity check: passes_hedge_checks (no number where both legs lose).
       Production applies it to portfolios kept from earlier runs; here it
       only counts failures, which should be 0 after step 05's pre-filter.

INPUT
    data/05_validate_pairs/<run_timestamp>/validated_pairs.json (latest, or --run)

OUTPUT
    data/06_find_portfolios/<run_timestamp>/
        - portfolios.json : what production build_portfolios returns
        - summary.json    : production's summary (by tier, averages, ...)

USAGE
    uv run --frozen --project backend experiments/06_find_portfolios.py
    uv run --frozen --project backend experiments/06_find_portfolios.py --run 20260101_120000
"""

import argparse
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_DIR = DATA_DIR / "05_validate_pairs"
OUTPUT_DIR = DATA_DIR / "06_find_portfolios"

MAX_COST = 10.00  # drop outliers
MIN_COVERAGE = 0.85  # drop tier 4

# (coverage_threshold, tier, label, description)
TIER_THRESHOLDS = [
    (0.95, 1, "HIGH_COVERAGE", "near-arbitrage"),
    (0.90, 2, "GOOD_COVERAGE", "strong hedge"),
    (0.85, 3, "MODERATE_COVERAGE", "decent hedge"),
    (0.00, 4, "LOW_COVERAGE", "speculative"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# =============================================================================
# FEES (copy of core/fees.py)
# =============================================================================


def compute_fee(notional: float, price: float, market: dict[str, Any]) -> float:
    """Taker fee on `notional` USDC at `price`; 0 if the market has no fees."""
    if not market.get("feesEnabled"):
        return 0.0
    schedule = market.get("feeSchedule")
    if not schedule:
        log.warning(f"Market {market.get('id', '?')}: feesEnabled but no feeSchedule; fee=0")
        return 0.0
    rate = float(schedule.get("rate", 0.0))
    exponent = int(schedule.get("exponent", 1))
    if price <= 0.0 or price >= 1.0:
        return 0.0
    return notional * rate * (price**exponent) * ((1.0 - price) ** exponent)


def _pair_to_market_stub(pair: dict, side: str) -> dict:
    """Minimal market dict for compute_fee from the pair's fee fields."""
    return {
        "id": pair.get(f"{side}_market_id", ""),
        "feesEnabled": pair.get(f"{side}_feesEnabled", False),
        "feeSchedule": pair.get(f"{side}_feeSchedule"),
    }


# =============================================================================
# METRICS
# =============================================================================


def calculate_coverage_metrics(
    target_price: float, cover_probability: float, total_cost: float
) -> dict:
    """Target price = P(target pays); cover_probability = P(cover | target loses)."""
    p_not_target = 1 - target_price
    coverage = target_price + p_not_target * cover_probability
    loss_probability = p_not_target * (1 - cover_probability)
    expected_profit = coverage - total_cost  # each payout is $1
    return {
        "coverage": round(coverage, 4),
        "loss_probability": round(loss_probability, 4),
        "expected_profit": round(expected_profit, 4),
    }


def classify_tier(coverage: float) -> tuple[int, str]:
    for threshold, tier, label, _ in TIER_THRESHOLDS:
        if coverage >= threshold:
            return tier, label
    return 4, "LOW_COVERAGE"


# =============================================================================
# PORTFOLIO BUILDING (production build_portfolios)
# =============================================================================


def build_portfolios(
    validated_pairs: list[dict], max_cost: float = MAX_COST
) -> tuple[list[dict], dict]:
    portfolios = []

    for pair in validated_pairs:
        target_price = pair.get("target_price", 0)
        cover_probability = pair.get("cover_probability", 0)
        if pair["cover_position"] == "YES":
            cover_price = pair.get("cover_price_yes", 0)
        else:
            cover_price = pair.get("cover_price_no", 0)
        total_cost = target_price + cover_price

        if not (0 < total_cost <= max_cost):
            continue

        metrics = calculate_coverage_metrics(target_price, cover_probability, total_cost)
        if metrics["coverage"] < MIN_COVERAGE:
            continue
        tier, tier_label = classify_tier(metrics["coverage"])

        # Entry fees on both legs (the winner pays $1, the loser $0)
        target_stub = _pair_to_market_stub(pair, "target")
        cover_stub = _pair_to_market_stub(pair, "cover")
        expected_fees = compute_fee(target_price, target_price, target_stub) + compute_fee(
            cover_price, cover_price, cover_stub
        )
        profit = 1.0 - total_cost - expected_fees
        profit_pct = (profit / total_cost * 100) if total_cost > 0 else 0.0
        validation = pair.get("_validation", {})

        portfolios.append(
            {
                "pair_id": pair["pair_id"],
                "target_group_id": pair["target_group_id"],
                "target_group_title": pair.get("target_group_title", ""),
                "target_group_slug": pair.get("target_group_slug", ""),
                "target_market_id": pair["target_market_id"],
                "target_market_slug": pair.get("target_market_slug", ""),
                "target_question": pair.get("target_question", ""),
                "target_position": pair["target_position"],
                "target_price": target_price,
                "target_bracket": pair.get("target_bracket"),
                "target_resolution": pair.get("target_resolution"),
                "cover_group_id": pair["cover_group_id"],
                "cover_group_title": pair.get("cover_group_title", ""),
                "cover_group_slug": pair.get("cover_group_slug", ""),
                "cover_market_id": pair["cover_market_id"],
                "cover_market_slug": pair.get("cover_market_slug", ""),
                "cover_question": pair.get("cover_question", ""),
                "cover_position": pair["cover_position"],
                "cover_price": cover_price,
                "cover_bracket": pair.get("cover_bracket"),
                "cover_resolution": pair.get("cover_resolution"),
                "cover_probability": cover_probability,
                "relationship": pair.get("relationship", ""),
                "relationship_type": pair.get("relationship_type", ""),
                "total_cost": round(total_cost, 4),
                "expected_fees": round(expected_fees, 4),
                "profit": round(profit, 4),
                "profit_pct": round(profit_pct, 2),
                **metrics,
                "tier": tier,
                "tier_label": tier_label,
                "viability_score": validation.get("viability_score", 0),
                "validation_analysis": validation.get("brief_analysis", ""),
                # Production keeps these to recompute fees when prices move
                "_target_fee_market": target_stub,
                "_cover_fee_market": cover_stub,
            }
        )

    portfolios.sort(key=lambda p: (p["tier"], -p["coverage"]))
    return portfolios, compute_summary(portfolios)


def compute_summary(portfolios: list[dict]) -> dict:
    if not portfolios:
        return {
            "total_portfolios": 0,
            "by_tier": {},
            "by_position": {},
            "avg_coverage": 0,
            "avg_expected_profit": 0,
            "avg_cost": 0,
            "profitable_count": 0,
        }

    by_tier: dict[int, dict] = {}
    by_position: dict[str, int] = {}
    for p in portfolios:
        tier = by_tier.setdefault(p["tier"], {"count": 0, "coverages": [], "profits": []})
        tier["count"] += 1
        tier["coverages"].append(p["coverage"])
        tier["profits"].append(p["expected_profit"])
        pos_type = f"target_{p['target_position']}"
        by_position[pos_type] = by_position.get(pos_type, 0) + 1

    tier_summary = {
        f"tier_{t}": {
            "count": d["count"],
            "avg_coverage": round(sum(d["coverages"]) / len(d["coverages"]), 4),
            "avg_profit": round(sum(d["profits"]) / len(d["profits"]), 4),
        }
        for t, d in by_tier.items()
    }
    n = len(portfolios)
    return {
        "total_portfolios": n,
        "by_tier": tier_summary,
        "by_position": by_position,
        "avg_coverage": round(sum(p["coverage"] for p in portfolios) / n, 4),
        "avg_expected_profit": round(sum(p["expected_profit"] for p in portfolios) / n, 4),
        "avg_cost": round(sum(p["total_cost"] for p in portfolios) / n, 4),
        "profitable_count": sum(1 for p in portfolios if p["expected_profit"] > 0.001),
        "best_coverage": portfolios[0]["coverage"],
    }


# =============================================================================
# HEDGE CHECK (copy of core/steps/hedge_checks.py numeric parsing)
# =============================================================================

_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kmb]\b|%)?"


# =============================================================================
# NUMERIC OUTCOME PARSING
# =============================================================================


@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float
    lo_closed: bool = True
    hi_closed: bool = True

    def contains(self, x: float) -> bool:
        above = x > self.lo or (self.lo_closed and x == self.lo)
        below = x < self.hi or (self.hi_closed and x == self.hi)
        return above and below


@dataclass(frozen=True)
class NumericOutcome:
    """The YES outcome of a question about one number."""

    template: str  # question with the comparison replaced by "{x}"
    interval: Interval
    integer: bool  # counts: only whole values can happen
    percent: bool  # 0-100


def _value(num: str, unit: Optional[str]) -> float:
    value = float(num.replace(",", ""))
    if unit and unit.lower() in _MULT:
        value *= _MULT[unit.lower()]
    return value


# (regex, builder(values) -> Interval). Ranges first so "200-300k" isn't
# read as "300k". Each pattern must match the whole comparison.
_PATTERNS: list[tuple[re.Pattern, Callable[..., Interval]]] = [
    (
        re.compile(rf"between\s+{_NUM}\s*(?:and|-|–|to)\s*{_NUM}", re.I),
        lambda a, b: Interval(a, b),
    ),
    (
        re.compile(rf"(?<![\w.,]){_NUM}\s*(?:-|–)\s*{_NUM}", re.I),
        lambda a, b: Interval(a, b),
    ),
    (
        re.compile(rf"(?:less than|fewer than|lower than|below|under|<)\s*{_NUM}", re.I),
        lambda a: Interval(-math.inf, a, hi_closed=False),
    ),
    (
        re.compile(rf"(?:at most|no more than|≤|<=)\s*{_NUM}", re.I),
        lambda a: Interval(-math.inf, a),
    ),
    (
        re.compile(rf"{_NUM}\s*or\s+(?:fewer|less|lower|below)", re.I),
        lambda a: Interval(-math.inf, a),
    ),
    (
        re.compile(
            rf"(?:more than|greater than|higher than|above|over|exceeds?|>)\s*{_NUM}",
            re.I,
        ),
        lambda a: Interval(a, math.inf, lo_closed=False),
    ),
    (
        re.compile(rf"(?:at least|no less than|≥|>=)\s*{_NUM}", re.I),
        lambda a: Interval(a, math.inf),
    ),
    (
        re.compile(rf"{_NUM}\s*(?:or\s+(?:more|higher|above|greater)|\+)", re.I),
        lambda a: Interval(a, math.inf),
    ),
    (re.compile(rf"exactly\s+{_NUM}", re.I), lambda a: Interval(a, a)),
]


def _is_year(value: float, unit: Optional[str]) -> bool:
    return unit is None and value.is_integer() and 1900 <= value <= 2100


def parse_numeric_outcome(question: str) -> Optional[NumericOutcome]:
    """Parse a question about one number, or None if it isn't clearly one."""
    text = " ".join(question.strip().rstrip("?").split())
    matches = []
    for pattern, build in _PATTERNS:
        for m in pattern.finditer(text):
            groups = m.groups()
            pairs = [(groups[i], groups[i + 1]) for i in range(0, len(groups), 2)]
            # In "200-300k" the unit on the second number applies to the first
            # too, unless that would make the range run backwards ("101-1k").
            if len(pairs) == 2 and pairs[0][1] is None and pairs[1][1]:
                widened = _value(pairs[0][0], pairs[1][1])
                if widened <= _value(*pairs[1]):
                    pairs[0] = (pairs[0][0], pairs[1][1])
            values = [_value(n, u) for n, u in pairs]
            if all(_is_year(v, u) for v, (_, u) in zip(values, pairs)):
                continue  # "2025-2026", "before 2027"
            if any(m.start() < e and s < m.end() for s, e, *_ in matches):
                continue  # overlaps a comparison already found
            interval = build(*values)
            if interval.lo > interval.hi:
                continue  # not a range ("2025-26 season")
            percent = any(u == "%" for _, u in pairs)
            # Counts are whole numbers; "10.0+" or "5.5" (a decimal without a
            # k/m/b multiplier) is a measurement like a magnitude.
            integer = (
                not percent
                and all(v.is_integer() for v in values)
                and not any("." in n and u is None for n, u in pairs)
            )
            matches.append((m.start(), m.end(), interval, integer, percent))
    if len(matches) != 1:
        return None
    start, end, interval, integer, percent = matches[0]
    template = " ".join(f"{text[:start]} {{x}} {text[end:]}".lower().split())
    return NumericOutcome(template, interval, integer, percent)


# =============================================================================
# BOTH-LOSE CHECK
# =============================================================================


def _wins(outcome: NumericOutcome, position: str, x: float) -> bool:
    inside = outcome.interval.contains(x)
    return inside if position == "YES" else not inside


def _candidates(a: NumericOutcome, b: NumericOutcome) -> list[float]:
    """Values worth testing, most telling first: interval edges and just either
    side of them, then the gaps between edges, then the ends of the range."""
    edges = sorted(
        {v for iv in (a.interval, b.interval) for v in (iv.lo, iv.hi) if math.isfinite(v)}
    )
    lo = 0.0
    hi = 100.0 if (a.percent or b.percent) else (edges[-1] * 2 + 10 if edges else 10.0)
    whole = a.integer and b.integer
    step = 1.0 if whole else 1e-6
    ordered = [p for e in edges for p in (e, e - step, e + step)]
    ordered += [(left + right) / 2 for left, right in zip(edges, edges[1:])]
    ordered += [lo, hi]
    if whole:
        ordered = [float(f(p)) for p in ordered for f in (math.floor, math.ceil)]
    seen, out = set(), []
    for p in ordered:
        if lo <= p <= hi and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _fmt(x: float, percent: bool) -> str:
    text = f"{x:,.0f}" if x.is_integer() else f"{x:,.6g}"
    return text + ("%" if percent else "")


def numeric_both_lose(pair: dict) -> Optional[str]:
    """A value where both legs lose, for same-question numeric pairs; else None."""
    target = parse_numeric_outcome(pair.get("target_question", ""))
    cover = parse_numeric_outcome(pair.get("cover_question", ""))
    if not target or not cover or target.template != cover.template:
        return None
    t_pos, c_pos = pair.get("target_position"), pair.get("cover_position")
    if t_pos not in ("YES", "NO") or c_pos not in ("YES", "NO"):
        return None
    for x in _candidates(target, cover):
        if not _wins(target, t_pos, x) and not _wins(cover, c_pos, x):
            percent = target.percent or cover.percent
            return (
                f"at {_fmt(x, percent)} both lose: target {t_pos} on "
                f"{pair.get('target_question')!r} and cover {c_pos} on "
                f"{pair.get('cover_question')!r}"
            )
    return None


def passes_hedge_checks(portfolio: dict) -> bool:
    """No numeric outcome where both legs lose (cost isn't checked: prices move)."""
    return numeric_both_lose(portfolio) is None


# =============================================================================
# MAIN
# =============================================================================


def find_run(base_dir: Path, run: str | None) -> Path:
    """The requested run folder, or the latest one (timestamps sort by name)."""
    if run:
        folder = base_dir / run
        if not folder.is_dir():
            raise SystemExit(f"Run not found: {folder}")
        return folder
    runs = sorted(f for f in base_dir.glob("*") if f.is_dir()) if base_dir.exists() else []
    if not runs:
        raise SystemExit(f"No runs in {base_dir}; run 05_validate_pairs.py first")
    return runs[-1]


def save_json(data: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main(run: str | None) -> None:
    input_dir = find_run(INPUT_DIR, run)
    pairs = json.loads((input_dir / "validated_pairs.json").read_text(encoding="utf-8"))
    log.info(f"Loaded {len(pairs)} validated pairs from {input_dir}")

    portfolios, summary = build_portfolios(pairs)
    failing = [p["pair_id"] for p in portfolios if not passes_hedge_checks(p)]
    if failing:
        log.warning(f"{len(failing)} portfolio(s) have a number where both legs lose")

    output_dir = OUTPUT_DIR / input_dir.name
    save_json(portfolios, output_dir / "portfolios.json")
    save_json(
        {
            "run_timestamp": input_dir.name,
            "input": str(input_dir / "validated_pairs.json"),
            "statistics": summary,
            "failing_hedge_checks": failing,
            "configuration": {
                "max_cost": MAX_COST,
                "min_coverage": MIN_COVERAGE,
                "tier_thresholds": {f"tier_{t}": c for c, t, _, _ in TIER_THRESHOLDS},
            },
        },
        output_dir / "summary.json",
    )

    log.info(f"Built {len(portfolios)} portfolios from {len(pairs)} pairs")
    for name, data in summary["by_tier"].items():
        log.info(f"  {name}: {data['count']} (avg coverage {data['avg_coverage']:.1%})")
    for i, p in enumerate(portfolios[:3], 1):
        log.info(
            f"  #{i} [{p['tier_label']}] coverage {p['coverage']:.1%}, "
            f"cost ${p['total_cost']:.2f}, profit ${p['profit']:.4f}\n"
            f"      target {p['target_position']}: {p['target_question'][:60]}\n"
            f"      cover  {p['cover_position']}: {p['cover_question'][:60]}"
        )
    log.info(f"Output: {output_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build portfolios from validated pairs")
    parser.add_argument("--run", help="Run timestamp to read (default: latest)")
    main(parser.parse_args().run)

"""Deterministic hedge checks that run before LLM validation (no LLM, no env).

A pair is a valid cover only if, whenever the target position loses, the
cover position wins. These checks reject pairs that can't be valid or can't
make money, so they never reach (or stay in) the LLM-validated set:

- Economics: a valid cover pays at least $1, so a pair costing $1 or more
  has no guaranteed profit. It only gains if both legs win, which is a bet,
  not a hedge.
- Numeric ladders: when both markets ask the same question about the same
  number ("deport less than 200k people" / "deport 200-300k people"), the
  outcome sets are compared directly. If some value makes both legs lose,
  the pair is rejected with that value as the example.

Numeric parsing is deliberately conservative: a question is compared only
when it has exactly one recognisable comparison ("less than 200k",
"10.0+", "between 195 and 199", "47 or fewer", "exactly 49") and both
questions read the same once that comparison is removed. Anything else
("hit 35%", "attend 2 matches", different deadlines) is left to the LLM.
"""

import math
import re
from dataclasses import dataclass
from typing import Callable, Optional

# A valid cover pays $1 when either leg wins; at or above this cost there's
# no guaranteed profit.
MAX_TOTAL_COST = 1.0

_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kmb]\b|%)?"


def pair_total_cost(pair: dict) -> Optional[float]:
    """Target price + cover price for the pair's positions, or None if unknown."""
    target = pair.get("target_price")
    cover_key = (
        "cover_price_yes" if pair.get("cover_position") == "YES" else "cover_price_no"
    )
    cover = pair.get(cover_key)
    if target is None or cover is None:
        return None
    return float(target) + float(cover)


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
        re.compile(
            rf"(?:less than|fewer than|lower than|below|under|<)\s*{_NUM}", re.I
        ),
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
        {
            v
            for iv in (a.interval, b.interval)
            for v in (iv.lo, iv.hi)
            if math.isfinite(v)
        }
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

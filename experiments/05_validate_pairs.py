"""
05: Validate Market Pairs (rule checks + LLM).

WHAT IT DOES
    Takes the target/cover pairs from step 04 and keeps only those that
    really work as a hedge: whenever the target position loses, the cover
    position must win. Mirrors backend/core/steps/validate.py and
    backend/core/steps/hedge_checks.py (logic copied, not imported).

WHY WE NEED THIS
    Expansion pairs every market of a target group with every market of a
    cover group, so most pairs are broken:
        Target YES "Election held by December"
        Cover  YES "Election called by March"
    If the election is held in November, the cover already resolved in
    March. And on number ladders some value can make both legs lose:
        Target YES ">=12,500 measles cases", cover NO ">=2,300 cases"
    At 5,000 cases both lose.

HOW IT WORKS
    1. Rule pre-filters, no LLM (pre_filter_pairs):
       - same market, or same group (intra-event, Polymarket handles those)
       - deadlines: "(direct)" relationships need deadlines within 7 days;
         YES target / NO cover needs cover deadline <= target deadline
       - numeric ladders: if both questions ask about the same number,
         test edge values; reject if some value makes both legs lose
    2. Skip pairs costing $1 or more (skip_costly_pairs): a valid cover pays
       $1, so they can't make money and aren't worth LLM spend
    3. LLM validation in batches of 32 (10 batches in parallel). The prompt
       asks the model to construct an outcome where both legs lose first,
       then checks timing, logic and entity (same person in both markets)
    4. Keyword auto-reject: is_valid=true is flipped to false when the
       analysis says "invalid", "mismatch", "different person", ...
    5. Keep pairs with viability_score >= 0.90 and is_valid=true

    Production caches verdicts in SQLite by pair_id and re-checks them when
    VALIDATION_PROMPT_VERSION changes. This script has no cache: every pair
    that passes the rules is sent to the LLM, so no prompt-version logic.

INPUT
    data/04_expand_pairs/<run_timestamp>/pairs.json (latest run, or --run)
    .env: OPENROUTER_API_KEY, VALIDATION_MODEL (not needed with --dry-run)

OUTPUT
    data/05_validate_pairs/<run_timestamp>/
        - validated_pairs.json : kept pairs, each with a `_validation` dict
        - summary.json         : production's stats (pre_filter_reasons, ...)

USAGE
    uv run --frozen --project backend experiments/05_validate_pairs.py --dry-run
    uv run --frozen --project backend experiments/05_validate_pairs.py --run 20260101_120000

    --dry-run runs the rule filters, prints their stats and the exact prompt
    of the first batch, and stops: no LLM call, no API key, no output files.
    Without it the script calls OpenRouter and costs money.
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_DIR = DATA_DIR / "04_expand_pairs"
OUTPUT_DIR = DATA_DIR / "05_validate_pairs"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_TIMEOUT = 60.0
LLM_MAX_RETRIES = 3

BATCH_SIZE = 32  # pairs per LLM call
MAX_CONCURRENT_BATCHES = 10
MIN_VIABILITY_SCORE = 0.90  # keep only pairs the LLM is very sure about

# Max days between deadlines for "(direct)" prerequisite relationships
DIRECT_RELATIONSHIP_MAX_DAYS_DIFF = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


class LLMAuthError(RuntimeError):
    """OpenRouter rejected the key (HTTP 401/403): abort instead of
    marking every pair invalid."""


# =============================================================================
# DETERMINISTIC HEDGE CHECKS (copy of core/steps/hedge_checks.py)
# =============================================================================

# A valid cover pays $1 when either leg wins; at or above this cost there's
# no guaranteed profit.
MAX_TOTAL_COST = 1.0

_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*([kmb]\b|%)?"


def pair_total_cost(pair: dict) -> Optional[float]:
    """Target price + cover price for the pair's positions, or None if unknown."""
    target = pair.get("target_price")
    cover_key = "cover_price_yes" if pair.get("cover_position") == "YES" else "cover_price_no"
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


# =============================================================================
# PROMPT (identical to core/steps/validate.py)
# =============================================================================

VALIDATION_PROMPT = """Validate prediction market hedging pairs for temporal and logical coherence.

## CONTEXT
A "hedge" consists of:
- TARGET position: The market we want exposure to
- COVER position: Should pay out when target position loses

For the hedge to work:
1. Cover must resolve in time to provide coverage
2. The implication direction must actually apply to these specific deadlines
3. The relationship must be logically valid (not just correlated)

## INPUT FIELDS EXPLAINED
- PROBABILITY: Pre-assigned confidence (0.98 = logically necessary relationship)
  - 0.98 means upstream analysis found this to be a NECESSARY implication
  - Your job is to verify if this holds for THESE SPECIFIC markets and deadlines
  - High probability doesn't mean auto-valid - you must verify temporal and logical coherence

## PAIRS TO VALIDATE

{pairs_text}

## FIRST: TRY TO MAKE BOTH POSITIONS LOSE (MOST IMPORTANT)

For each pair, actively try to construct ONE concrete, plausible outcome in
which the TARGET position loses AND the COVER position also loses.

- If you can describe such an outcome, the pair is INVALID. Put the outcome
  in rejection_reason (e.g. "If 5,000 cases: target YES (>=12,500) loses and
  cover NO (>=2,300) loses").
- The pair is valid ONLY if no such outcome exists.
- A true implication is not enough: the cover must be on the side that WINS
  when the target loses. Check the positions, not just the relationship.

Classic traps:
- Threshold ladders: target YES ">=12,500 cases", cover NO ">=2,300 cases".
  5,000 cases: target loses, cover loses -> INVALID.
- Wrong direction: target YES "10.0+ earthquake", cover NO "9.0+ earthquake".
  A 9.5 quake: target loses, cover loses -> INVALID.
- Wrong side: target NO "Trump attends the World Cup final", cover YES
  "Trump attends 0 World Cup matches". If he attends the final, target NO
  loses and "0 matches" loses -> INVALID.
- Broader vs narrower: target YES "new coronavirus pandemic", cover NO "new
  pandemic". A flu pandemic: target loses, cover (NO pandemic) loses -> INVALID.

## VALIDATION CRITERIA

For each pair, also assess:

### 1. TEMPORAL COHERENCE
- Does the cover resolve at or AFTER when coverage is needed?
- If target is "X by March" and cover is "Y by December", can the cover provide coverage?
- KEY: The cover must be able to PAY OUT when the target LOSES

### 2. LOGICAL COHERENCE
- Does the stated relationship actually apply to THESE SPECIFIC deadlines?
- "City captured → Region captured" only works if city deadline <= region deadline
- "Election held → Election called" only works if held deadline >= called deadline

### 3. PRACTICAL VALIDITY
- Is the hedge direction correct?
- Would buying these positions actually provide coverage?

### 4. ENTITY COHERENCE (CRITICAL FOR MULTI-CANDIDATE MARKETS)
- Check if TARGET and COVER brackets refer to the SAME entity (person, team, etc.)
- For markets about specific individuals (elections, nominations, winners):
  - The hedge ONLY works if BOTH markets are about the SAME person
  - "Person A wins nomination" CANNOT be hedged by "Person B wins election"
  - Different people = INVALID hedge, even if the group-level relationship seems logical
- Compare the Bracket fields: if they name different entities, the hedge is INVALID
- Minor spelling variations are OK ("J.D. Vance" = "JD Vance"), but different people are NOT

## ENTITY COHERENCE EXAMPLES

INVALID: Target="Will Sarah Sanders win Republican nomination?", Cover="Will Michelle Obama win presidency?"
- Target Bracket: "Sarah Huckabee Sanders"
- Cover Bracket: "Michelle Obama"
- These are DIFFERENT PEOPLE → hedge is INVALID regardless of group relationship

INVALID: Target="Will Josh Hawley win Republican nomination?", Cover="Will Donald Trump win presidency?"
- Different people → INVALID

VALID: Target="Will Kim Kardashian win Republican nomination?", Cover="Will Kim Kardashian win presidency?"
- SAME person in both markets → hedge can be valid (if temporal/logical criteria also pass)

VALID: Target="Will J.D. Vance win nomination?", Cover="Will JD Vance win presidency?"
- Same person (minor spelling difference) → valid

## TEMPORAL LOGIC EXAMPLES

VALID: Target="X by June", Cover="Y by December"
- If target loses in June, cover has until December to pay

INVALID: Target="X by December", Cover="Y by March"
- If target event happens in October, cover already resolved in March → no coverage!

INVALID: Target="Election called by March", Cover="Election held by June 30" with position NO
- Relationship: "held → called" (if held, then was called)
- Cover=NO means "not held by June"
- If election IS called in February, cover (not held by June) might still lose → no coverage

## OUTPUT FORMAT (JSON only)

```json
{{
  "validations": [
    {{
      "pair_id": "pair_id_here",
      "viability_score": 0.0-1.0,
      "is_valid": true/false,
      "temporal_valid": true/false,
      "logical_valid": true/false,
      "entity_valid": true/false,
      "rejection_reason": "null if valid, else the both-lose outcome or other explanation",
      "brief_analysis": "1-2 sentence reasoning"
    }}
  ]
}}
```

## CRITICAL: is_valid MUST BE CONSISTENT

**is_valid determines whether this pair is cached and used. Your analysis MUST match this boolean.**

- If your analysis concludes the hedge is INVALID, BROKEN, or WON'T WORK → set `is_valid: false`
- If temporal_valid=false OR logical_valid=false OR entity_valid=false → set `is_valid: false`
- If TARGET and COVER brackets name DIFFERENT entities (different people) → set `entity_valid: false` AND `is_valid: false`
- If you describe the hedge as "invalid", "incorrect", "wrong", "flawed", "doesn't work", "won't work", "logical mismatch", "bad hedge", "different person", "different entity" → set `is_valid: false`
- A high viability_score does NOT override is_valid. You can have high confidence that something is INVALID.

**NEVER** set is_valid=true if your brief_analysis describes ANY problem, issue, or flaw with the hedge.
**If you write a negative word about the hedge in brief_analysis, is_valid MUST be false.**

## Score meanings:
- 1.0: Perfect hedge, logically necessary
- 0.8-0.9: Strong hedge, minor concerns
- 0.6-0.7: Questionable, temporal or logical issues
- <0.5: Invalid hedge

BE STRICT. False positives cost money. When in doubt, set is_valid=false.
"""

# =============================================================================
# PRE-FILTERING
# =============================================================================


def skip_costly_pairs(pairs: list[dict]) -> tuple[list[dict], int]:
    """Drop pairs costing $1 or more: a valid cover pays $1, so there's no profit."""
    kept = []
    for pair in pairs:
        cost = pair_total_cost(pair)
        if cost is None or cost < MAX_TOTAL_COST:
            kept.append(pair)
    return kept, len(pairs) - len(kept)


def empty_pre_filter_stats() -> dict:
    return {
        "same_market": 0,
        "same_group": 0,
        "deadline_mismatch": 0,
        "cost_not_below_one": 0,
        "numeric_both_lose": 0,
    }


def pre_filter_pairs(pairs: list[dict]) -> tuple[list[dict], dict]:
    """Reject pairs that can never be valid, without the LLM."""
    filtered = []
    rejections = empty_pre_filter_stats()

    for pair in pairs:
        if pair.get("target_market_id") == pair.get("cover_market_id"):
            rejections["same_market"] += 1
            continue

        if pair.get("target_group_id") == pair.get("cover_group_id"):
            rejections["same_group"] += 1
            continue

        is_valid, reason = check_deadline_coherence(pair)
        if not is_valid:
            log.debug(f"Pre-filter rejecting pair {pair.get('pair_id')}: {reason}")
            rejections["deadline_mismatch"] += 1
            continue

        both_lose = numeric_both_lose(pair)
        if both_lose:
            log.debug(f"Pre-filter rejecting pair: {both_lose}")
            rejections["numeric_both_lose"] += 1
            continue

        filtered.append(pair)

    return filtered, rejections


def parse_resolution_date(resolution: str | None) -> datetime | None:
    """ISO date ("2026-06-30T12:00:00Z") to datetime, or None."""
    if not resolution:
        return None
    try:
        if resolution.endswith("Z"):
            resolution = resolution[:-1] + "+00:00"
        return datetime.fromisoformat(resolution.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_direct_relationship(relationship: str | None) -> bool:
    """Step 03 tags prerequisite chains ("held by X" -> "called by X") "(direct)"."""
    if not relationship:
        return False
    return "(direct)" in relationship.lower()


def check_deadline_coherence(pair: dict) -> tuple[bool, str | None]:
    """
    Direct (prerequisite) relationships: deadlines must roughly match.
        "Held by March" -> "Called by March"
    Otherwise, for a YES target and NO cover: "NOT X by target date" must
    imply "NOT Y by cover date", which needs cover date <= target date, or
    the cover event could still happen between the two dates.
    """
    relationship = pair.get("relationship", "")
    target_res = pair.get("target_resolution")
    cover_res = pair.get("cover_resolution")
    target_date = parse_resolution_date(target_res)
    cover_date = parse_resolution_date(cover_res)

    if not target_date or not cover_date:
        return True, None

    if is_direct_relationship(relationship):
        days_diff = abs((cover_date - target_date).days)
        if days_diff > DIRECT_RELATIONSHIP_MAX_DAYS_DIFF:
            return False, (
                f"direct_deadline_mismatch: {days_diff} days between "
                f"target ({target_res[:10]}) and cover ({cover_res[:10]})"
            )
    elif pair.get("target_position") == "YES" and pair.get("cover_position") == "NO":
        if cover_date > target_date:
            days_diff = (cover_date - target_date).days
            return False, (
                f"contrapositive_deadline_invalid: cover ({cover_res[:10]}) "
                f"is {days_diff} days after target ({target_res[:10]}). "
                f"For YES/NO hedge, cover must resolve at or before target."
            )

    return True, None


# =============================================================================
# KEYWORD AUTO-REJECTION
# =============================================================================

# The LLM sometimes says is_valid=true while its analysis describes a flaw.
REJECTION_KEYWORDS = [
    "invalid",
    "incorrect",
    "wrong",
    "flawed",
    "does not guarantee",
    "doesn't guarantee",
    "not guarantee",
    "no guarantee",
    "logical mismatch",
    "mismatch",
    "won't work",
    "will not work",
    "cannot provide coverage",
    "no coverage",
    "hedge is broken",
    "not a valid hedge",
    "bad hedge",
    "different person",
    "different people",
    "different entity",
    "different entities",
    "not the same person",
    "not the same entity",
]


def should_auto_reject(analysis: str) -> bool:
    if not analysis:
        return False
    analysis_lower = analysis.lower()
    return any(keyword in analysis_lower for keyword in REJECTION_KEYWORDS)


# =============================================================================
# PROMPT FORMATTING
# =============================================================================


def format_pair_for_validation(pair: dict) -> str:
    """Format a candidate pair for LLM validation."""
    return f"""### {pair["pair_id"]}
TARGET: "{pair.get("target_question", "unknown")}"
  - Position: {pair["target_position"]}
  - Bracket: {pair.get("target_bracket", "unknown")}
  - Resolution: {pair.get("target_resolution", "unknown")}

COVER: "{pair.get("cover_question", "unknown")}"
  - Position: {pair["cover_position"]}
  - Bracket: {pair.get("cover_bracket", "unknown")}
  - Resolution: {pair.get("cover_resolution", "unknown")}

RELATIONSHIP: {pair.get("relationship", "unknown")}
RELATIONSHIP TYPE: {pair.get("relationship_type", "unknown")}
PROBABILITY: {pair.get("cover_probability", 0)}

HEDGE LOGIC: When target_{pair["target_position"]} loses, cover should pay out.
"""


def build_prompt(batch: list[dict]) -> str:
    pairs_text = "\n".join(format_pair_for_validation(p) for p in batch)
    return VALIDATION_PROMPT.format(pairs_text=pairs_text)


# =============================================================================
# LLM (OpenRouter)
# =============================================================================


def extract_json_from_response(text: str) -> dict | None:
    """Parse JSON from an LLM reply (handles ```json fences and extra text)."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1]
    if "```" in text:
        text = text.split("```")[0]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


async def complete(client: httpx.AsyncClient, model: str, prompt: str) -> str:
    """One chat completion with production's retry rules."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    for attempt in range(LLM_MAX_RETRIES):
        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status == 429:
                await asyncio.sleep(2**attempt)
                continue
            if status in (401, 403):
                raise LLMAuthError(f"OpenRouter rejected the key (HTTP {status})") from e
            raise
        except httpx.RequestError:
            if attempt < LLM_MAX_RETRIES - 1:
                await asyncio.sleep(1)
                continue
            raise
    raise RuntimeError(f"Failed after {LLM_MAX_RETRIES} attempts")


async def validate_batch(
    client: httpx.AsyncClient, model: str, pairs: list[dict], batch_num: int
) -> dict[str, dict]:
    """Validate one batch; on failure every pair in it is marked invalid."""
    try:
        result = extract_json_from_response(await complete(client, model, build_prompt(pairs)))
        if not result or "validations" not in result:
            log.warning(f"Batch {batch_num}: Failed to parse LLM response")
            return {
                p["pair_id"]: {
                    "viability_score": 0,
                    "is_valid": False,
                    "temporal_valid": False,
                    "logical_valid": False,
                    "rejection_reason": "LLM validation failed",
                }
                for p in pairs
            }
        return {v["pair_id"]: v for v in result.get("validations", [])}
    except LLMAuthError:
        raise
    except Exception as e:
        log.error(f"Batch {batch_num} error: {e}")
        failed = {"viability_score": 0, "is_valid": False, "rejection_reason": f"Error: {e}"}
        return {p["pair_id"]: dict(failed) for p in pairs}


# =============================================================================
# MAIN VALIDATION (production validate_pairs without the cache)
# =============================================================================


async def validate_pairs(
    candidate_pairs: list[dict],
    model: str,
    min_viability: float = MIN_VIABILITY_SCORE,
    batch_size: int = BATCH_SIZE,
) -> tuple[list[dict], dict]:
    total_candidates = len(candidate_pairs)
    candidate_pairs, pre_filter_rejections = pre_filter_pairs(candidate_pairs)

    pairs_to_validate, too_costly = skip_costly_pairs(candidate_pairs)
    pre_filter_rejections["cost_not_below_one"] = too_costly
    pre_filtered_count = sum(pre_filter_rejections.values())
    log.info(f"Validating {len(pairs_to_validate)} pairs ({pre_filtered_count} pre-filtered)")

    if not pairs_to_validate:
        return [], {
            "total_candidates": total_candidates,
            "from_cache": 0,
            "pre_filtered": pre_filtered_count,
            "pre_filter_reasons": pre_filter_rejections,
            "validated_count": 0,
            "new_validated": 0,
        }

    batches = [
        (pairs_to_validate[i : i + batch_size], i // batch_size + 1)
        for i in range(0, len(pairs_to_validate), batch_size)
    ]
    all_validations: dict[str, dict] = {}
    new_validated = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

    async def process_batch(client: httpx.AsyncClient, batch: list[dict], num: int) -> None:
        nonlocal new_validated
        async with semaphore:
            validations = await validate_batch(client, model, batch, num)
        for pair in batch:
            validation = validations.get(pair["pair_id"], {})
            llm_valid = validation.get("is_valid", False)
            if llm_valid and should_auto_reject(validation.get("brief_analysis", "")):
                validation["is_valid"] = False
                validation["rejection_reason"] = "Auto-rejected: analysis contains invalid keywords"
                llm_valid = False
            if llm_valid:
                new_validated += 1  # production caches these (score not yet checked)
        all_validations.update(validations)
        log.info(f"  Batch {num}/{len(batches)} ({len(batch)} pairs)")

    headers = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"}
    async with httpx.AsyncClient(
        base_url=OPENROUTER_BASE_URL, headers=headers, timeout=LLM_TIMEOUT
    ) as client:
        await asyncio.gather(*[process_batch(client, b, n) for b, n in batches])

    # Costly pairs got no verdict, so they count as "low_score" like in production.
    validated = []
    reasons = {"temporal": 0, "logical": 0, "entity": 0, "low_score": 0, "llm_failed": 0}
    for pair in candidate_pairs:
        validation = all_validations.get(pair["pair_id"], {})
        score = validation.get("viability_score", 0)
        if score >= min_viability and validation.get("is_valid", False):
            validated.append({**pair, "_validation": validation})
        elif not validation.get("temporal_valid", True):
            reasons["temporal"] += 1
        elif not validation.get("logical_valid", True):
            reasons["logical"] += 1
        elif not validation.get("entity_valid", True):
            reasons["entity"] += 1
        elif "LLM" in str(validation.get("rejection_reason", "")):
            reasons["llm_failed"] += 1
        else:
            reasons["low_score"] += 1

    summary = {
        "total_candidates": total_candidates,
        "from_cache": 0,
        "pre_filtered": pre_filtered_count,
        "pre_filter_reasons": pre_filter_rejections,
        "new_validated": new_validated,
        "validated_count": len(validated),
        "rejected_count": total_candidates - len(validated),
        "rejection_reasons": reasons,
        "retention_rate": round(len(validated) / total_candidates, 3) if total_candidates else 0,
        "model_used": model,
    }
    return validated, summary


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
        raise SystemExit(f"No runs in {base_dir}; run 04_expand_pairs.py first")
    return runs[-1]


def save_json(data: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def dry_run(pairs: list[dict]) -> None:
    """Rule filters + first prompt, no LLM."""
    kept, stats = pre_filter_pairs(pairs)
    to_validate, stats["cost_not_below_one"] = skip_costly_pairs(kept)
    print(f"\nCandidates: {len(pairs)}  ->  sent to LLM: {len(to_validate)}")
    print("Pre-filter rejections:")
    for reason, count in stats.items():
        print(f"  {reason:20} {count}")
    if not to_validate:
        print("\nNothing left to validate.")
        return
    n_batches = math.ceil(len(to_validate) / BATCH_SIZE)
    print(f"\nBatches of {BATCH_SIZE}: {n_batches}. Prompt for batch 1:\n")
    print(build_prompt(to_validate[:BATCH_SIZE]))


async def main(run: str | None, is_dry_run: bool) -> None:
    input_dir = find_run(INPUT_DIR, run)
    pairs = json.loads((input_dir / "pairs.json").read_text(encoding="utf-8"))
    log.info(f"Loaded {len(pairs)} pairs from {input_dir}")

    if is_dry_run:
        dry_run(pairs)
        return

    model = os.getenv("VALIDATION_MODEL")
    if not model or not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("Set VALIDATION_MODEL and OPENROUTER_API_KEY in .env (or use --dry-run)")

    validated, stats = await validate_pairs(pairs, model)

    output_dir = OUTPUT_DIR / input_dir.name
    save_json(validated, output_dir / "validated_pairs.json")
    save_json(
        {
            "run_timestamp": input_dir.name,
            "input": str(input_dir / "pairs.json"),
            "statistics": stats,
            "configuration": {
                "model": model,
                "batch_size": BATCH_SIZE,
                "min_viability_score": MIN_VIABILITY_SCORE,
                "max_total_cost": MAX_TOTAL_COST,
            },
        },
        output_dir / "summary.json",
    )
    log.info(
        f"Validated {stats['validated_count']}/{stats['total_candidates']} pairs. "
        f"Output: {output_dir}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate hedge pairs (rules + LLM)")
    parser.add_argument("--run", help="Run timestamp to read (default: latest)")
    parser.add_argument("--dry-run", action="store_true", help="Rules + prompt only, no LLM")
    args = parser.parse_args()
    asyncio.run(main(args.run, args.dry_run))

"""
Validate market pairs for temporal and logical coherence.

This module uses LLM to check if each target+cover market pair actually
works as a hedge. Filters out pairs with timing or logic problems.

Key validation criteria:
- TEMPORAL: Cover must resolve at/after when coverage is needed
- LOGICAL: The implication must apply to specific deadlines

Example problems:
    Target: "Election held by December"
    Cover:  "Election called by March"

    If election is held in November, the "called by March" market
    already resolved in March - hedge expired 8 months early.

Caching:
    Validated pairs are cached permanently in SQLite.
    pair_id is deterministic (hash of target+cover+positions).
"""

import os
from datetime import datetime
from typing import Callable

from loguru import logger

from core.models import get_llm_client
from core.state import PipelineState
from core.utils import extract_json_from_response

# =============================================================================
# CONFIGURATION
# =============================================================================

# Pairs per LLM call (balances efficiency with context limits)
BATCH_SIZE = 32

# Maximum concurrent API calls (respects OpenRouter rate limits)
MAX_CONCURRENT_BATCHES = 10

# Minimum viability score to keep a pair
# 0.90 requires very high LLM confidence in hedge validity
MIN_VIABILITY_SCORE = 0.90

# Default model for validation (can be overridden)
DEFAULT_VALIDATION_MODEL = os.getenv("VALIDATION_MODEL")
if not DEFAULT_VALIDATION_MODEL:
    raise ValueError("VALIDATION_MODEL environment variable not set")


# =============================================================================
# PROMPT
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

## VALIDATION CRITERIA

For each pair, assess:

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
      "rejection_reason": "null if valid, else explanation",
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


def pre_filter_pairs(pairs: list[dict]) -> tuple[list[dict], dict]:
    """
    Apply deterministic pre-filters to reject pairs that would always fail.

    These filters catch structural/temporal issues without needing LLM:
    - Same market: target and cover are the same market
    - Same group: target and cover are from the same event group
    - Deadline coherence: based on relationship type (direct vs contrapositive)

    Deadline rules by relationship type:
    - Direct (implies): Prerequisite chains require matching deadlines
      "Held by March" → "Called by March" (deadlines must match)
    - Contrapositive (implied_by): Nested deadlines, cover can be earlier
      "by March" → "by June" (earlier deadline is fine)

    Args:
        pairs: Candidate pairs to filter

    Returns:
        Tuple of (filtered_pairs, rejection_stats)
    """
    filtered = []
    rejections = {
        "same_market": 0,
        "same_group": 0,
        "deadline_mismatch": 0,
    }

    for pair in pairs:
        # Check 1: Same market (meaningless hedge)
        if pair.get("target_market_id") == pair.get("cover_market_id"):
            rejections["same_market"] += 1
            continue

        # Check 2: Same group (intra-event, handled by Polymarket)
        if pair.get("target_group_id") == pair.get("cover_group_id"):
            rejections["same_group"] += 1
            continue

        # Check 3: Deadline coherence based on relationship type
        is_valid, reason = check_deadline_coherence(pair)
        if not is_valid:
            logger.debug(
                f"Pre-filter rejecting pair: {pair.get('target_question', '?')[:30]} -> "
                f"{pair.get('cover_question', '?')[:30]}: {reason}"
            )
            rejections["deadline_mismatch"] += 1
            continue

        filtered.append(pair)

    return filtered, rejections


# =============================================================================
# KEYWORD AUTO-REJECTION
# =============================================================================

# Keywords that indicate invalid hedge (even if LLM set is_valid=true)
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

# =============================================================================
# DEADLINE PRE-FILTERING
# =============================================================================


# Maximum days difference for "direct" prerequisite relationships
# Prerequisite chains (held→called) require matching deadlines
DIRECT_RELATIONSHIP_MAX_DAYS_DIFF = 7


def parse_resolution_date(resolution: str | None) -> datetime | None:
    """
    Parse ISO resolution date string to datetime.

    Args:
        resolution: ISO date string like "2026-06-30T12:00:00Z"

    Returns:
        datetime object or None if invalid
    """
    if not resolution:
        return None

    try:
        # Handle ISO format with Z suffix
        if resolution.endswith("Z"):
            resolution = resolution[:-1] + "+00:00"
        return datetime.fromisoformat(resolution.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def is_direct_relationship(relationship: str | None) -> bool:
    """
    Check if relationship is "direct" (implies: target→other).

    Direct relationships are prerequisite chains where deadlines must match.
    Example: "Held by March" implies "Called by March"

    Contrapositive relationships are nested deadlines where cover can be earlier.
    Example: "Captured by June" implied_by "Captured by March"

    We detect this from our own structured output format in implications.py.
    """
    if not relationship:
        return False

    return "(direct)" in relationship.lower()


def check_deadline_coherence(pair: dict) -> tuple[bool, str | None]:
    """
    Check if pair has valid deadline coherence based on relationship type.

    Uses resolution_date from API (reliable) and relationship direction
    from implications step.

    Rules:
    - Direct (implies): Prerequisite chains need matching deadlines
      Example: "Held by March" → "Called by March" (same deadline required)

    - Contrapositive (implied_by) with YES/NO positions:
      Cover date must be <= target date for the implication to hold.
      Example: "NOT called by June → NOT held by Y" requires Y <= June
      Because if Y > June, event could happen between June and Y.

    Args:
        pair: Candidate pair with resolution dates and relationship

    Returns:
        Tuple of (is_valid, rejection_reason)
    """
    relationship = pair.get("relationship", "")
    target_res = pair.get("target_resolution")
    cover_res = pair.get("cover_resolution")
    target_pos = pair.get("target_position")
    cover_pos = pair.get("cover_position")

    target_date = parse_resolution_date(target_res)
    cover_date = parse_resolution_date(cover_res)

    # Can't check without dates
    if not target_date or not cover_date:
        return True, None

    # For direct/prerequisite relationships: deadlines must approximately match
    if is_direct_relationship(relationship):
        days_diff = abs((cover_date - target_date).days)
        if days_diff > DIRECT_RELATIONSHIP_MAX_DAYS_DIFF:
            return False, (
                f"direct_deadline_mismatch: {days_diff} days between "
                f"target ({target_res[:10]}) and cover ({cover_res[:10]})"
            )
    else:
        # Contrapositive relationships with YES target / NO cover:
        # When target loses (NOT by target_date), cover must win (NOT by cover_date)
        # "NOT X by target_date → NOT Y by cover_date" requires cover_date <= target_date
        # Otherwise, event could occur between target_date and cover_date
        logger.debug(
            f"Checking contrapositive: target_pos={target_pos}, cover_pos={cover_pos}, "
            f"target_date={target_date}, cover_date={cover_date}"
        )
        if target_pos == "YES" and cover_pos == "NO":
            if cover_date > target_date:
                days_diff = (cover_date - target_date).days
                return False, (
                    f"contrapositive_deadline_invalid: cover ({cover_res[:10]}) "
                    f"is {days_diff} days after target ({target_res[:10]}). "
                    f"For YES/NO hedge, cover must resolve at or before target."
                )

    return True, None


def should_auto_reject(analysis: str) -> bool:
    """
    Check if validation analysis contains keywords indicating invalid hedge.

    This catches cases where LLM wrote is_valid=true but the analysis
    text clearly describes problems with the hedge.
    """
    if not analysis:
        return False

    analysis_lower = analysis.lower()
    for keyword in REJECTION_KEYWORDS:
        if keyword in analysis_lower:
            return True
    return False


# =============================================================================
# HELPERS
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


# =============================================================================
# BATCH VALIDATION
# =============================================================================


async def validate_batch(
    pairs: list[dict],
    llm_model: str,
    batch_num: int,
) -> dict[str, dict]:
    """Validate a batch of pairs via LLM."""
    llm = get_llm_client(llm_model)

    pairs_text = "\n".join(format_pair_for_validation(p) for p in pairs)
    prompt = VALIDATION_PROMPT.format(pairs_text=pairs_text)

    try:
        response = await llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        result = extract_json_from_response(str(response))

        if not result or "validations" not in result:
            logger.warning(f"Batch {batch_num}: Failed to parse LLM response")
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

    except Exception as e:
        logger.error(f"Batch {batch_num} error: {e}")
        return {
            p["pair_id"]: {
                "viability_score": 0,
                "is_valid": False,
                "rejection_reason": f"Error: {e}",
            }
            for p in pairs
        }


# =============================================================================
# MAIN VALIDATION
# =============================================================================


async def validate_pairs(
    candidate_pairs: list[dict],
    state: PipelineState,
    llm_model: str | None = None,
    min_viability: float = MIN_VIABILITY_SCORE,
    batch_size: int = BATCH_SIZE,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[dict], dict]:
    """
    Validate candidate pairs using LLM (with caching).

    Only validates pairs not already in cache.

    Args:
        candidate_pairs: Pairs from expand step
        state: Pipeline state for caching
        llm_model: LLM model to use (default: claude-sonnet-4)
        min_viability: Minimum score to keep (default: 0.70)
        batch_size: Pairs per LLM call
        progress_callback: Optional progress callback

    Returns:
        Tuple of (validated_pairs, summary_stats)
    """
    model = llm_model or DEFAULT_VALIDATION_MODEL

    # Separate cached vs new pairs
    pairs_to_validate = []
    cached_validations = {}

    for pair in candidate_pairs:
        pair_id = pair["pair_id"]
        cached = state.get_validated_pair(pair_id)
        if cached:
            cached_validations[pair_id] = cached
        else:
            pairs_to_validate.append(pair)

    # Apply pre-filters to new pairs (skip pairs that would always fail)
    pre_filter_rejections = {"same_market": 0, "same_group": 0, "deadline_mismatch": 0}
    if pairs_to_validate:
        pairs_to_validate, pre_filter_rejections = pre_filter_pairs(pairs_to_validate)

    pre_filtered_count = sum(pre_filter_rejections.values())
    logger.info(
        f"Validating {len(pairs_to_validate)} pairs "
        f"({len(cached_validations)} cached, {pre_filtered_count} pre-filtered)"
    )

    if not pairs_to_validate:
        # All from cache - filter and return
        validated = []
        for pair in candidate_pairs:
            cached = cached_validations.get(pair["pair_id"], {})
            # Check both viability_score AND is_valid (default True for backward compat)
            if cached.get("viability_score", 0) >= min_viability and cached.get(
                "is_valid", True
            ):
                validated.append({**pair, "_validation": cached})

        return validated, {
            "total_candidates": len(candidate_pairs),
            "from_cache": len(cached_validations),
            "pre_filtered": pre_filtered_count,
            "pre_filter_reasons": pre_filter_rejections,
            "validated_count": len(validated),
            "new_validated": 0,
        }

    # Validate new pairs in batches (parallel with semaphore-based rate limiting)
    import asyncio

    all_validations = dict(cached_validations)
    new_validated_pairs = []
    lock = asyncio.Lock()

    # Create all batches upfront
    batches = []
    for i in range(0, len(pairs_to_validate), batch_size):
        batch = pairs_to_validate[i : i + batch_size]
        batch_num = i // batch_size + 1
        batches.append((batch, batch_num))

    total_batches = len(batches)
    completed_batches = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

    async def process_batch(batch: list[dict], batch_num: int) -> None:
        """Process a single batch with rate limiting."""
        nonlocal completed_batches

        async with semaphore:
            validations = await validate_batch(batch, model, batch_num)

            # Prepare valid pairs for caching
            pairs_to_cache = []
            auto_rejected = 0
            for pair in batch:
                pair_id = pair["pair_id"]
                validation = validations.get(pair_id, {})
                analysis = validation.get("brief_analysis", "")

                # Check LLM's is_valid flag
                llm_valid = validation.get("is_valid", False)

                # Auto-reject if analysis contains concerning keywords
                if llm_valid and should_auto_reject(analysis):
                    validation["is_valid"] = False
                    validation["rejection_reason"] = (
                        "Auto-rejected: analysis contains invalid keywords"
                    )
                    auto_rejected += 1
                    llm_valid = False

                if llm_valid:
                    pairs_to_cache.append(
                        {
                            "pair_id": pair_id,
                            "target_group_id": pair["target_group_id"],
                            "target_market_id": pair["target_market_id"],
                            "target_position": pair["target_position"],
                            "cover_group_id": pair["cover_group_id"],
                            "cover_market_id": pair["cover_market_id"],
                            "cover_position": pair["cover_position"],
                            "cover_probability": pair.get("cover_probability", 0),
                            "viability_score": validation.get("viability_score", 0),
                            "is_valid": True,
                            "validation_reason": analysis,
                        }
                    )

            if auto_rejected > 0:
                logger.info(
                    f"  Auto-rejected {auto_rejected} pairs due to keyword detection"
                )

            # Update shared state (protected by lock)
            async with lock:
                all_validations.update(validations)
                if pairs_to_cache:
                    state.add_validated_pairs(pairs_to_cache, model)
                    new_validated_pairs.extend(pairs_to_cache)

                completed_batches += 1
                if progress_callback:
                    progress_callback(
                        f"Validating batch {completed_batches}/{total_batches}"
                    )

            logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)")

    # Run all batches in parallel (semaphore limits concurrency)
    await asyncio.gather(*[process_batch(batch, num) for batch, num in batches])

    # Filter all pairs by viability score
    validated = []
    rejection_reasons = {
        "temporal": 0,
        "logical": 0,
        "entity": 0,
        "low_score": 0,
        "llm_failed": 0,
    }

    for pair in candidate_pairs:
        pair_id = pair["pair_id"]
        validation = all_validations.get(pair_id, {})

        score = validation.get("viability_score", 0)
        is_valid = validation.get("is_valid", False)

        if score >= min_viability and is_valid:
            validated.append({**pair, "_validation": validation})
        else:
            # Track rejection reason
            if not validation.get("temporal_valid", True):
                rejection_reasons["temporal"] += 1
            elif not validation.get("logical_valid", True):
                rejection_reasons["logical"] += 1
            elif not validation.get("entity_valid", True):
                rejection_reasons["entity"] += 1
            elif "LLM" in str(validation.get("rejection_reason", "")):
                rejection_reasons["llm_failed"] += 1
            else:
                rejection_reasons["low_score"] += 1

    summary = {
        "total_candidates": len(candidate_pairs),
        "from_cache": len(cached_validations),
        "pre_filtered": pre_filtered_count,
        "pre_filter_reasons": pre_filter_rejections,
        "new_validated": len(new_validated_pairs),
        "validated_count": len(validated),
        "rejected_count": len(candidate_pairs) - len(validated),
        "rejection_reasons": rejection_reasons,
        "retention_rate": round(len(validated) / len(candidate_pairs), 3)
        if candidate_pairs
        else 0,
        "model_used": model,
    }

    logger.info(
        f"Validated: {len(validated)}/{len(candidate_pairs)} "
        f"({summary['retention_rate']:.1%} retention)"
    )

    return validated, summary


async def validate_pairs_simple(
    candidate_pairs: list[dict],
    llm_model: str | None = None,
    min_viability: float = MIN_VIABILITY_SCORE,
    batch_size: int = BATCH_SIZE,
) -> tuple[list[dict], dict]:
    """
    Validate pairs without caching (for one-off runs).

    Args:
        candidate_pairs: Pairs to validate
        llm_model: LLM model to use
        min_viability: Minimum score to keep
        batch_size: Pairs per LLM call

    Returns:
        Tuple of (validated_pairs, summary_stats)
    """
    model = llm_model or DEFAULT_VALIDATION_MODEL

    # Apply pre-filters
    pairs_to_validate, pre_filter_rejections = pre_filter_pairs(candidate_pairs)
    pre_filtered_count = sum(pre_filter_rejections.values())

    if pre_filtered_count > 0:
        logger.info(f"Pre-filtered {pre_filtered_count} pairs")

    # Validate in parallel with semaphore-based rate limiting
    import asyncio

    all_validations = {}
    lock = asyncio.Lock()

    # Create all batches upfront
    batches = []
    for i in range(0, len(pairs_to_validate), batch_size):
        batch = pairs_to_validate[i : i + batch_size]
        batch_num = i // batch_size + 1
        batches.append((batch, batch_num))

    total_batches = len(batches)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)

    async def process_batch(batch: list[dict], batch_num: int) -> None:
        """Process a single batch with rate limiting."""
        async with semaphore:
            validations = await validate_batch(batch, model, batch_num)
            async with lock:
                all_validations.update(validations)
            logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)")

    # Run all batches in parallel
    await asyncio.gather(*[process_batch(batch, num) for batch, num in batches])

    # Filter by viability
    validated = []
    for pair in pairs_to_validate:
        validation = all_validations.get(pair["pair_id"], {})
        score = validation.get("viability_score", 0)

        if score >= min_viability and validation.get("is_valid", False):
            validated.append({**pair, "_validation": validation})

    summary = {
        "total_candidates": len(candidate_pairs),
        "pre_filtered": pre_filtered_count,
        "pre_filter_reasons": pre_filter_rejections,
        "validated_count": len(validated),
        "retention_rate": round(len(validated) / len(candidate_pairs), 3)
        if candidate_pairs
        else 0,
    }

    return validated, summary

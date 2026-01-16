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

import asyncio
from typing import Callable

from loguru import logger

from core.models import get_llm_client
from core.state import PipelineState
from core.utils import extract_json_from_response

# =============================================================================
# CONFIGURATION
# =============================================================================

import os

# Pairs per LLM call (balances efficiency with context limits)
BATCH_SIZE = 8

# Minimum viability score to keep a pair
MIN_VIABILITY_SCORE = 0.70

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
      "rejection_reason": "null if valid, else explanation",
      "brief_analysis": "1-2 sentence reasoning"
    }}
  ]
}}
```

Score meanings:
- 1.0: Perfect hedge, logically necessary
- 0.8-0.9: Strong hedge, minor concerns
- 0.6-0.7: Questionable, temporal or logical issues
- <0.5: Invalid hedge

BE STRICT. False positives cost money. When in doubt, reject.
"""


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

    logger.info(
        f"Validating {len(pairs_to_validate)} pairs ({len(cached_validations)} cached)"
    )

    if not pairs_to_validate:
        # All from cache - filter and return
        validated = []
        for pair in candidate_pairs:
            cached = cached_validations.get(pair["pair_id"], {})
            if cached.get("viability_score", 0) >= min_viability:
                validated.append({**pair, "_validation": cached})

        return validated, {
            "total_candidates": len(candidate_pairs),
            "from_cache": len(cached_validations),
            "validated_count": len(validated),
            "new_validated": 0,
        }

    # Validate new pairs in batches
    all_validations = dict(cached_validations)
    new_validated_pairs = []

    total_batches = (len(pairs_to_validate) + batch_size - 1) // batch_size

    for i in range(0, len(pairs_to_validate), batch_size):
        batch = pairs_to_validate[i : i + batch_size]
        batch_num = i // batch_size + 1

        if progress_callback:
            progress_callback(f"Validating batch {batch_num}/{total_batches}")

        logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)")

        validations = await validate_batch(batch, model, batch_num)
        all_validations.update(validations)

        # Store valid pairs to cache
        pairs_to_cache = []
        for pair in batch:
            pair_id = pair["pair_id"]
            validation = validations.get(pair_id, {})

            if validation.get("is_valid", False):
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
                        "validation_reason": validation.get("brief_analysis", ""),
                    }
                )

        if pairs_to_cache:
            state.add_validated_pairs(pairs_to_cache, model)
            new_validated_pairs.extend(pairs_to_cache)

        # Rate limiting between batches
        if i + batch_size < len(pairs_to_validate):
            await asyncio.sleep(1)

    # Filter all pairs by viability score
    validated = []
    rejection_reasons = {
        "temporal": 0,
        "logical": 0,
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
            elif "LLM" in str(validation.get("rejection_reason", "")):
                rejection_reasons["llm_failed"] += 1
            else:
                rejection_reasons["low_score"] += 1

    summary = {
        "total_candidates": len(candidate_pairs),
        "from_cache": len(cached_validations),
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

    all_validations = {}
    total_batches = (len(candidate_pairs) + batch_size - 1) // batch_size

    for i in range(0, len(candidate_pairs), batch_size):
        batch = candidate_pairs[i : i + batch_size]
        batch_num = i // batch_size + 1

        logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)")

        validations = await validate_batch(batch, model, batch_num)
        all_validations.update(validations)

        if i + batch_size < len(candidate_pairs):
            await asyncio.sleep(1)

    # Filter by viability
    validated = []
    for pair in candidate_pairs:
        validation = all_validations.get(pair["pair_id"], {})
        score = validation.get("viability_score", 0)

        if score >= min_viability and validation.get("is_valid", False):
            validated.append({**pair, "_validation": validation})

    summary = {
        "total_candidates": len(candidate_pairs),
        "validated_count": len(validated),
        "retention_rate": round(len(validated) / len(candidate_pairs), 3)
        if candidate_pairs
        else 0,
    }

    return validated, summary

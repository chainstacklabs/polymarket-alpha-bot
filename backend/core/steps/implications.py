"""
Extract logical implications between groups using LLM.

This module extracts "if A happens, then B must happen" relationships
between market groups. These logical implications are the foundation
for building covering hedges.

Key features:
- SQLite caching: Never recompute implications for existing groups
- Configurable LLM: Support for multiple models (mimo-v2-flash, claude-sonnet-4)
- Contrapositive derivation: Converts implies/implied_by to YES/NO covers

Example:
    "If Ukraine holds an election → Ukraine must have called one"
    (you can't hold an election without calling it first)

Relationship types:
    - "implies": target YES → other YES (target happening causes other)
    - "implied_by": other YES → target YES (other happening causes target)
    - "inverse": negative correlation (one up, other down)
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

# Probability mapping by confidence level
PROBABILITY_MAP = {
    "necessary": 0.98,  # Logical/geographic certainty
    "strong": 0.85,  # Very likely but not guaranteed
    "inverse": 0.70,  # Correlation, not causation
}

# Downgrade "implies" direction (LLMs often confuse correlation with implication)
IMPLIES_MULTIPLIER = 0.90


# =============================================================================
# PROMPT
# =============================================================================

IMPLICATION_PROMPT = """Analyze logical relationships between prediction market events.

## TARGET EVENT:
"{target_title}"

## AVAILABLE EVENTS:
{group_titles_text}

## CRITICAL: NECESSARY vs CORRELATION

A **NECESSARY** implication (A → B) means: "If A is true, B MUST be true. There is NO POSSIBLE scenario where A is true and B is false."

Test: Can you imagine ANY realistic scenario where A=YES but B=NO? If yes, it's NOT necessary.

### EXAMPLES OF NECESSARY IMPLICATIONS:
- "election held" → "election called" (NECESSARY: physically impossible to hold uncalled election)
- "city captured" → "military entered city" (NECESSARY: can't capture without entering)

### EXAMPLES THAT ARE NOT NECESSARY (just correlations):
- "election called" → "election held" (WRONG: election can be called then cancelled)
- "war started" → "peace talks failed" (WRONG: war can start without prior peace talks)

## YOUR TASK

For the target event, identify:

### 1. implied_by (OTHER → TARGET): What guarantees the target?
- "If OTHER=YES, then TARGET=YES is GUARANTEED"
- Confidence: "necessary" (no counterexample) or "strong" (rare counterexamples)

### 2. implies (TARGET → OTHER): What does the target guarantee?
- "If TARGET=YES, then OTHER=YES is GUARANTEED"
- BE VERY CAREFUL: This is often confused with correlation!
- Confidence: "necessary" (no counterexample) or "strong" (rare counterexamples)

### 3. inverse: Negatively correlated events
- When TARGET=NO, what becomes MORE LIKELY to be YES?

## COUNTEREXAMPLE CHECK (REQUIRED)

For each "necessary" relationship, verify: Can you construct ANY plausible scenario that violates it?

## OUTPUT FORMAT (JSON only):
```json
{{
  "implied_by": [
    {{
      "group_title": "exact title from list",
      "confidence": "necessary or strong",
      "explanation": "why other=YES guarantees target=YES",
      "counterexample_check": "why no counterexample exists OR describe the rare exception"
    }}
  ],
  "implies": [
    {{
      "group_title": "exact title from list",
      "confidence": "necessary or strong",
      "explanation": "why target=YES guarantees other=YES",
      "counterexample_check": "why no counterexample exists OR describe the rare exception"
    }}
  ],
  "inverse": [
    {{
      "group_title": "exact title from list",
      "explanation": "why these are negatively correlated"
    }}
  ]
}}
```

REMEMBER: When in doubt, leave it out. False positives are costly.

## IMPORTANT: Asymmetric relationships

If A → B is necessary (e.g., "held → called"), then B → A is usually NOT necessary!
Only ONE direction can be a logical necessity. Be very careful!
"""


# =============================================================================
# LLM HELPERS
# =============================================================================


def match_title_to_group(
    title: str,
    groups_by_title: dict[str, dict],
    groups_by_title_lower: dict[str, dict],
) -> dict | None:
    """Match LLM output title to actual group."""
    # Exact match
    if title in groups_by_title:
        return groups_by_title[title]

    # Case-insensitive match
    title_lower = title.lower().strip()
    if title_lower in groups_by_title_lower:
        return groups_by_title_lower[title_lower]

    # Fuzzy match - substring
    for group_title, group in groups_by_title.items():
        if title_lower in group_title.lower() or group_title.lower() in title_lower:
            return group

    return None


# =============================================================================
# COVER DERIVATION
# =============================================================================


def derive_covers(
    llm_result: dict,
    target_group: dict,
    groups_by_title: dict[str, dict],
    groups_by_title_lower: dict[str, dict],
) -> dict:
    """
    Derive covers from raw LLM implications using contrapositive logic.

    For target event T:
    - "implies" (T → other): other_YES covers T_NO
    - "implied_by" (other → T): other_NO covers T_YES (contrapositive)
    """
    target_id = target_group["group_id"]
    target_title = target_group["title"]

    yes_covered_by = []  # Covers for target_YES (fire when target=NO)
    no_covered_by = []  # Covers for target_NO (fire when target=YES)

    # Process "implied_by": other → target (contrapositive gives YES cover)
    for item in llm_result.get("implied_by", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        confidence = item.get("confidence", "strong")
        prob = PROBABILITY_MAP.get(confidence, 0.85)

        yes_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "NO",
                "relationship": f"other→target (contrapositive): {item.get('explanation', '')}",
                "relationship_type": confidence,
                "probability": prob,
                "counterexample_check": item.get("counterexample_check", ""),
            }
        )

    # Process "implies": target → other (direct gives NO cover)
    for item in llm_result.get("implies", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        confidence = item.get("confidence", "strong")
        base_prob = PROBABILITY_MAP.get(confidence, 0.85)
        prob = round(base_prob * IMPLIES_MULTIPLIER, 4)  # Downgrade "implies"

        no_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "YES",
                "relationship": f"target→other: {item.get('explanation', '')}",
                "relationship_type": confidence,
                "probability": prob,
                "counterexample_check": item.get("counterexample_check", ""),
            }
        )

    # Process "inverse": negatively correlated
    inverse_prob = PROBABILITY_MAP["inverse"]
    for item in llm_result.get("inverse", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        yes_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "YES",
                "relationship": f"inverse: {item.get('explanation', '')}",
                "relationship_type": "inverse",
                "probability": inverse_prob,
            }
        )
        no_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "NO",
                "relationship": f"inverse: {item.get('explanation', '')}",
                "relationship_type": "inverse",
                "probability": inverse_prob,
            }
        )

    return {
        "group_id": target_id,
        "title": target_title,
        "yes_covered_by": yes_covered_by,
        "no_covered_by": no_covered_by,
    }


# =============================================================================
# MAIN EXTRACTION
# =============================================================================


async def extract_implications(
    new_groups: list[dict],
    all_groups: list[dict],
    state: PipelineState,
    llm_model: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Extract group-level implications using LLM (CACHED).

    Only processes groups that don't have cached implications.
    Returns combined list of new + cached implications.

    Args:
        new_groups: Groups that need implication extraction
        all_groups: All groups (for context in prompt)
        state: Pipeline state for caching
        llm_model: Optional LLM model override
        progress_callback: Optional callback for progress updates

    Returns:
        List of implications (combined new + cached)
    """
    if not new_groups:
        logger.info("No new groups to process for implications")
        # Return cached implications
        return state.get_all_implications()

    # Build context for prompt (all group titles)
    group_titles_text = "\n".join(
        f"{i}. {g['title']}" for i, g in enumerate(all_groups, 1)
    )

    # Build lookup tables for matching
    groups_by_title = {g["title"]: g for g in all_groups}
    groups_by_title_lower = {g["title"].lower().strip(): g for g in all_groups}

    # Get LLM client
    llm = get_llm_client(llm_model)
    model_name = llm_model or llm.model

    logger.info(f"Extracting implications for {len(new_groups)} new groups")
    logger.info(f"Using LLM model: {model_name}")

    new_implications = []

    for i, target_group in enumerate(new_groups):
        if progress_callback:
            progress_callback(f"Extracting implications {i + 1}/{len(new_groups)}")

        # Check if already cached
        cached = state.get_implication(target_group["group_id"])
        if cached:
            logger.debug(f"Using cached implication for {target_group['title'][:40]}")
            continue

        logger.info(f"[{i + 1}/{len(new_groups)}] {target_group['title'][:60]}")

        # Build prompt
        prompt = IMPLICATION_PROMPT.format(
            group_titles_text=group_titles_text,
            target_title=target_group["title"],
        )

        # Call LLM
        try:
            response = await llm.complete(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
            )

            llm_result = extract_json_from_response(str(response))

            if not llm_result:
                logger.warning("  Failed to parse LLM response")
                # Store empty result to avoid reprocessing
                impl = {
                    "group_id": target_group["group_id"],
                    "title": target_group["title"],
                    "yes_covered_by": [],
                    "no_covered_by": [],
                }
            else:
                # Derive covers from raw implications
                impl = derive_covers(
                    llm_result,
                    target_group,
                    groups_by_title,
                    groups_by_title_lower,
                )

                logger.info(
                    f"  Found: {len(impl['yes_covered_by'])} YES, "
                    f"{len(impl['no_covered_by'])} NO covers"
                )

            new_implications.append(impl)

        except Exception as e:
            logger.error(f"  Error extracting implications: {e}")
            # Store empty result
            impl = {
                "group_id": target_group["group_id"],
                "title": target_group["title"],
                "yes_covered_by": [],
                "no_covered_by": [],
            }
            new_implications.append(impl)

    # Save new implications to cache
    if new_implications:
        state.add_implications(new_implications, model_name)
        logger.info(f"Cached {len(new_implications)} new implications")

    # Return all implications (new + cached)
    return state.get_all_implications()


async def extract_implications_batch(
    groups: list[dict],
    all_groups: list[dict],
    state: PipelineState,
    llm_model: str | None = None,
    batch_size: int = 5,  # Not used anymore, kept for API compat
    max_concurrent: int = 3,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Extract implications with concurrent LLM calls.

    Uses semaphore-based concurrency and processes results as they complete
    (not waiting for batches). This prevents slow LLM responses from blocking
    other requests.

    Args:
        groups: Groups to process
        all_groups: All groups for context
        state: Pipeline state for caching
        llm_model: Optional LLM model override
        batch_size: Deprecated, kept for API compatibility
        max_concurrent: Maximum concurrent LLM requests
        progress_callback: Optional progress callback

    Returns:
        List of all implications (new + cached)
    """
    # Filter to only uncached groups
    groups_to_process = []
    for g in groups:
        if not state.get_implication(g["group_id"]):
            groups_to_process.append(g)

    if not groups_to_process:
        logger.info("All implications already cached")
        return state.get_all_implications()

    logger.info(
        f"Processing {len(groups_to_process)} groups "
        f"({len(groups) - len(groups_to_process)} cached)"
    )

    # Build context
    group_titles_text = "\n".join(
        f"{i}. {g['title']}" for i, g in enumerate(all_groups, 1)
    )
    groups_by_title = {g["title"]: g for g in all_groups}
    groups_by_title_lower = {g["title"].lower().strip(): g for g in all_groups}

    # Get LLM client
    llm = get_llm_client(llm_model)
    model_name = llm_model or llm.model

    semaphore = asyncio.Semaphore(max_concurrent)
    completed_count = 0
    total_count = len(groups_to_process)

    async def process_group(target_group: dict, idx: int) -> dict:
        nonlocal completed_count
        async with semaphore:
            prompt = IMPLICATION_PROMPT.format(
                group_titles_text=group_titles_text,
                target_title=target_group["title"],
            )

            try:
                response = await llm.complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0.1,
                )

                llm_result = extract_json_from_response(str(response))

                if not llm_result:
                    result = {
                        "group_id": target_group["group_id"],
                        "title": target_group["title"],
                        "yes_covered_by": [],
                        "no_covered_by": [],
                    }
                else:
                    result = derive_covers(
                        llm_result,
                        target_group,
                        groups_by_title,
                        groups_by_title_lower,
                    )

            except Exception as e:
                logger.error(f"Error processing {target_group['title'][:40]}: {e}")
                result = {
                    "group_id": target_group["group_id"],
                    "title": target_group["title"],
                    "yes_covered_by": [],
                    "no_covered_by": [],
                }

            # Update progress after completion
            completed_count += 1
            if progress_callback:
                progress_callback(f"Extracted {completed_count}/{total_count}")

            return result

    # Start all tasks at once - semaphore controls concurrency
    tasks = [process_group(g, i) for i, g in enumerate(groups_to_process)]

    # Process results as they complete (not waiting for batches)
    all_new_implications = []
    save_buffer = []
    save_interval = 10  # Save to cache every N completions

    for coro in asyncio.as_completed(tasks):
        result = await coro
        all_new_implications.append(result)
        save_buffer.append(result)

        # Save to cache periodically
        if len(save_buffer) >= save_interval:
            state.add_implications(save_buffer, model_name)
            save_buffer = []

    # Save any remaining results
    if save_buffer:
        state.add_implications(save_buffer, model_name)

    logger.info(f"Processed {len(all_new_implications)} new implications")

    return state.get_all_implications()

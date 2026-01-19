"""
Extract NECESSARY logical implications between groups using LLM.

This module finds "if A happens, then B MUST happen" relationships
between market groups. Only logically necessary implications are accepted -
correlations and "likely" relationships are rejected.

Key features:
- STRICT: Only necessary implications (0.98 probability)
- Contrapositive logic: "other → target" means "NOT target → NOT other"
- SQLite caching: Never recompute implications for existing groups

Example:
    "If Ukraine holds an election → Ukraine must have called one"
    (you can't hold an election without calling it first)

How it works:
    1. LLM finds "implied_by" relationships: other → target
    2. We derive cover via contrapositive: if target=NO, then other=NO
    3. So buying NO on "other" covers a YES position on "target"
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

# Probability for necessary relationships
NECESSARY_PROBABILITY = 0.98

# Multiplier for "implies" direction (1.0 = treat both directions equally)
IMPLIES_MULTIPLIER = 1.0


# =============================================================================
# PROMPT
# =============================================================================

IMPLICATION_PROMPT = """Find ONLY logically necessary relationships between prediction market events.

## TARGET EVENT:
"{target_title}"

## AVAILABLE EVENTS:
{group_titles_text}

## WHAT IS "NECESSARY"?

A **NECESSARY** implication (A → B) means: "If A is true, B MUST be true BY DEFINITION OR PHYSICAL LAW."

There must be ZERO possible scenarios where A=YES and B=NO. Not "unlikely" - IMPOSSIBLE.

## VALID NECESSARY RELATIONSHIPS (include these):
- "election held" → "election called" (DEFINITION: can't hold without calling)
- "city captured" → "military operation in city" (PHYSICAL: can't capture without entering)
- "person dies" → "person was alive" (LOGICAL: death requires prior life)
- "child born" → "pregnancy occurred" (BIOLOGICAL: birth requires pregnancy)

## NOT NECESSARY - DO NOT INCLUDE:
- "war started" → "peace talks failed" (WRONG: war can start without talks)
- "election called" → "election held" (WRONG: can be cancelled)
- "military clash" → "nuclear weapon used" (WRONG: clash doesn't require nukes)
- "ceasefire broken" → "war escalates" (WRONG: could de-escalate)
- "sanctions imposed" → "conflict worsens" (WRONG: correlation, not causation)
- "candidate wins primary" → "candidate wins general" (WRONG: can lose general)

## YOUR TASK

Find relationships where events GUARANTEE each other:

### 1. implied_by (OTHER → TARGET): What GUARANTEES the target?
- "If OTHER=YES, then TARGET=YES is 100% CERTAIN"
- Must be definitionally or physically impossible for OTHER=YES and TARGET=NO

### 2. implies (TARGET → OTHER): What does the target GUARANTEE?
- "If TARGET=YES, then OTHER=YES is 100% CERTAIN"
- BE VERY CAREFUL: This direction is often confused with correlation!

## STRICT COUNTEREXAMPLE TEST (REQUIRED)

For EACH relationship, you MUST:
1. Try to construct a scenario that violates the implication
2. If you can imagine ANY such scenario (even unlikely), DO NOT INCLUDE IT
3. Only include if the scenario is LOGICALLY IMPOSSIBLE

## OUTPUT FORMAT (JSON only):
```json
{{
  "implied_by": [
    {{
      "group_title": "exact title from list",
      "explanation": "why other=YES makes target=YES logically certain",
      "counterexample_attempt": "I tried to imagine [scenario] but it's impossible because [reason]"
    }}
  ],
  "implies": [
    {{
      "group_title": "exact title from list",
      "explanation": "why target=YES makes other=YES logically certain",
      "counterexample_attempt": "I tried to imagine [scenario] but it's impossible because [reason]"
    }}
  ]
}}
```

## CRITICAL RULES:
1. QUALITY OVER QUANTITY - empty lists are fine, false positives are NOT
2. "Likely" or "usually" means DO NOT INCLUDE
3. Correlations are NOT implications - "A often leads to B" is NOT "A guarantees B"
4. Political/social predictions are almost NEVER necessary (humans are unpredictable)
5. When in doubt, LEAVE IT OUT
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
    - "implied_by" (other → target): contrapositive gives YES cover (buy NO on other)
    - "implies" (target → other): direct gives NO cover (buy YES on other)
    """
    target_id = target_group["group_id"]
    target_title = target_group["title"]

    yes_covered_by = []  # Covers for target_YES position (fire when target=NO)
    no_covered_by = []  # Covers for target_NO position (fire when target=YES)

    # Process "implied_by": other → target (contrapositive gives YES cover)
    for item in llm_result.get("implied_by", []):
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
                "cover_position": "NO",
                "relationship": f"necessary (contrapositive): {item.get('explanation', '')}",
                "relationship_type": "necessary",
                "probability": NECESSARY_PROBABILITY,
                "counterexample_attempt": item.get("counterexample_attempt", ""),
            }
        )

    # Process "implies": target → other (direct gives NO cover)
    # Apply multiplier since "implies" direction is less reliable
    for item in llm_result.get("implies", []):
        other_title = item.get("group_title", "")
        matched = match_title_to_group(
            other_title, groups_by_title, groups_by_title_lower
        )
        if not matched or matched["group_id"] == target_id:
            continue

        prob = round(NECESSARY_PROBABILITY * IMPLIES_MULTIPLIER, 4)
        no_covered_by.append(
            {
                "group_id": matched["group_id"],
                "title": matched["title"],
                "cover_position": "YES",
                "relationship": f"necessary (direct): {item.get('explanation', '')}",
                "relationship_type": "necessary",
                "probability": prob,
                "counterexample_attempt": item.get("counterexample_attempt", ""),
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

            result = None
            max_retries = 3
            timeout_seconds = 90  # Hard timeout per attempt

            for attempt in range(max_retries):
                try:
                    response = await asyncio.wait_for(
                        llm.complete(
                            [{"role": "user", "content": prompt}],
                            temperature=0.1,
                        ),
                        timeout=timeout_seconds,
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
                    break  # Success, exit retry loop

                except asyncio.TimeoutError:
                    logger.warning(
                        f"Timeout (attempt {attempt + 1}/{max_retries}) for "
                        f"{target_group['title'][:40]}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue

                except Exception as e:
                    logger.error(
                        f"Error (attempt {attempt + 1}/{max_retries}) processing "
                        f"{target_group['title'][:40]}: {e}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    break

            # If all retries failed, return empty result
            if result is None:
                logger.error(f"All retries failed for {target_group['title'][:40]}")
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

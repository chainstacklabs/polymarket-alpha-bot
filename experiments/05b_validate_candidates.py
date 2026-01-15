"""
05b: Validate Market Pairs with LLM.

WHAT IT DOES
    Uses an LLM to check if each target+cover market pair actually works
    as a hedge. Filters out pairs with temporal or logical problems.

WHY WE NEED THIS
    Step 05a created ALL market combinations, but most don't work:

    TEMPORAL PROBLEM (80% of failures):
        Target: "Election held by December"
        Cover:  "Election called by March"

        If election is held in November, the "called by March" market
        already resolved in March! Your hedge expired 8 months before
        you needed it. Useless.

    LOGICAL PROBLEM (15% of failures):
        Target: "Region fully captured = NO"
        Cover:  "City captured = YES"

        If region is NOT fully captured, the city might still have been
        captured (just not ALL cities). The cover fires when you don't
        need it, doesn't fire when you do.

HOW IT WORKS
    1. Batch pairs into groups of 8 (efficient LLM usage)
    2. For each batch, ask LLM to evaluate viability (0-100%)
    3. LLM checks: temporal alignment, logical consistency, hedge direction
    4. Filter pairs below MIN_VIABILITY_SCORE threshold
    5. Typically removes 80-90% of pairs

PIPELINE
    05a_expand_to_markets → [05b_validate_candidates] → 06_find_portfolios

INPUT
    data/05a_expand_to_markets/<timestamp>/
        - market_candidates.json: All target × cover pairs

OUTPUT
    data/05b_validate_candidates/<timestamp>/
        - validated_candidates.json : Pairs that passed LLM validation
        - summary.json              : Validation stats by failure reason

RUNTIME
    ~15 seconds per batch of 8 pairs (LLM-bound)
    32 pairs = ~1 minute
    100 pairs = ~3 minutes

CONFIGURATION
    - BATCH_SIZE: Pairs per LLM call (8)
    - MIN_VIABILITY_SCORE: Threshold to keep (0.70)
    - LLM_MODEL: Model for validation (claude-sonnet-4)
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_DIR = DATA_DIR / "05a_expand_to_markets"
OUTPUT_DIR = DATA_DIR / "05b_validate_candidates"

INPUT_RUN_FOLDER: str | None = None

# LLM settings
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
LLM_MODEL = "anthropic/claude-sonnet-4"
LLM_BASE_URL = "https://openrouter.ai/api/v1"
LLM_TIMEOUT = 120.0
MAX_RETRIES = 3
MAX_TOKENS = 8000

# Validation settings
BATCH_SIZE = 8  # Pairs per LLM call
MIN_VIABILITY_SCORE = 0.7  # Filter below this

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


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
      "pair_id": "pair_1",
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
# LLM CLIENT
# =============================================================================


def extract_json_from_response(text: str) -> dict | None:
    """Extract and parse JSON from LLM response."""
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


async def call_llm(client: httpx.AsyncClient, prompt: str, context: str) -> dict | None:
    """Call LLM with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.post(
                "/chat/completions",
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": MAX_TOKENS,
                },
                timeout=LLM_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logger.warning(f"Empty response for {context} (attempt {attempt})")
                await asyncio.sleep(2 * attempt)
                continue

            result = extract_json_from_response(content)
            if result:
                return result

            logger.warning(f"JSON parse failed for {context}: {content[:200]}")
            await asyncio.sleep(2 * attempt)

        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP error for {context}: {e}")
            await asyncio.sleep(2 * attempt * 2)
        except Exception as e:
            logger.warning(f"Error for {context}: {e}")
            await asyncio.sleep(2 * attempt)

    return None


# =============================================================================
# PAIR FORMATTING
# =============================================================================


def format_pair_for_validation(
    pair_id: str,
    target: dict,
    cover: dict,
    cover_side: str,  # "yes" or "no"
) -> str:
    """Format a target-cover pair for LLM validation."""
    target_deadline = target.get("bracket_label", "unknown")
    target_resolution = target.get("resolution_date", "unknown")
    cover_deadline = cover.get("bracket_label", "unknown")
    cover_resolution = cover.get("resolution_date", "unknown")

    # Determine positions
    if cover_side == "yes":
        # YES covering: Buy target_YES, cover fires when target=NO
        target_pos = "YES"
        cover_pos = cover.get("cover_position", "NO")
        scenario = "target_YES loses"
    else:
        # NO covering: Buy target_NO, cover fires when target=YES
        target_pos = "NO"
        cover_pos = cover.get("cover_position", "YES")
        scenario = "target_NO loses"

    return f"""### {pair_id}
TARGET: "{target.get("question", "unknown")}"
  - Position: {target_pos}
  - Deadline: {target_deadline}
  - Resolution: {target_resolution}

COVER: "{cover.get("question", "unknown")}"
  - Position: {cover_pos}
  - Deadline: {cover_deadline}
  - Resolution: {cover_resolution}

RELATIONSHIP: {cover.get("relationship", "unknown")}
RELATIONSHIP TYPE: {cover.get("relationship_type", "unknown")}
PROBABILITY: {cover.get("probability", 0)}

HEDGE LOGIC: When {scenario}, cover should pay out.
"""


def collect_pairs_for_validation(market_candidates: list[dict]) -> list[dict]:
    """
    Collect all target-cover pairs with metadata for validation.

    Returns list of dicts with:
    - pair_id
    - target_group_id, target_market_id
    - cover_group_id, cover_market_id
    - cover_side ("yes" or "no")
    - formatted text for LLM
    """
    pairs = []
    pair_idx = 0

    for group in market_candidates:
        target_group_id = group["target_group_id"]

        for market in group.get("markets", []):
            target_market_id = market["market_id"]

            # YES covering candidates
            for cover in market.get("yes_covering_candidates", []):
                pair_idx += 1
                pair_id = f"pair_{pair_idx}"
                pairs.append(
                    {
                        "pair_id": pair_id,
                        "target_group_id": target_group_id,
                        "target_market_id": target_market_id,
                        "cover_group_id": cover["source_group_id"],
                        "cover_market_id": cover["market_id"],
                        "cover_side": "yes",
                        "target": market,
                        "cover": cover,
                        "formatted": format_pair_for_validation(
                            pair_id, market, cover, "yes"
                        ),
                    }
                )

            # NO covering candidates
            for cover in market.get("no_covering_candidates", []):
                pair_idx += 1
                pair_id = f"pair_{pair_idx}"
                pairs.append(
                    {
                        "pair_id": pair_id,
                        "target_group_id": target_group_id,
                        "target_market_id": target_market_id,
                        "cover_group_id": cover["source_group_id"],
                        "cover_market_id": cover["market_id"],
                        "cover_side": "no",
                        "target": market,
                        "cover": cover,
                        "formatted": format_pair_for_validation(
                            pair_id, market, cover, "no"
                        ),
                    }
                )

    return pairs


# =============================================================================
# VALIDATION
# =============================================================================


async def validate_batch(
    client: httpx.AsyncClient,
    pairs: list[dict],
    batch_num: int,
) -> dict[str, dict]:
    """Validate a batch of pairs via LLM."""
    pairs_text = "\n".join(p["formatted"] for p in pairs)
    prompt = VALIDATION_PROMPT.format(pairs_text=pairs_text)

    result = await call_llm(client, prompt, f"batch_{batch_num}")

    if not result or "validations" not in result:
        logger.warning(f"Batch {batch_num}: LLM failed, marking all as invalid")
        return {
            p["pair_id"]: {
                "viability_score": 0,
                "is_valid": False,
                "rejection_reason": "LLM validation failed",
            }
            for p in pairs
        }

    # Map results by pair_id
    return {v["pair_id"]: v for v in result.get("validations", [])}


async def validate_all_pairs(
    client: httpx.AsyncClient,
    pairs: list[dict],
) -> dict[str, dict]:
    """Validate all pairs in batches."""
    all_validations = {}

    # Process in batches
    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(pairs) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)")

        validations = await validate_batch(client, batch, batch_num)
        all_validations.update(validations)

        # Rate limiting
        if i + BATCH_SIZE < len(pairs):
            await asyncio.sleep(1)

    return all_validations


# =============================================================================
# FILTERING
# =============================================================================


def filter_candidates(
    market_candidates: list[dict],
    validations: dict[str, dict],
    pairs: list[dict],
    min_score: float,
) -> tuple[list[dict], dict]:
    """
    Filter market_candidates based on validation results.

    Returns filtered candidates and stats.
    """
    # Build lookup: (target_market_id, cover_market_id, cover_side) -> validation
    pair_lookup = {}
    for p in pairs:
        key = (p["target_market_id"], p["cover_market_id"], p["cover_side"])
        pair_lookup[key] = validations.get(p["pair_id"], {})

    filtered = []
    stats = {
        "total_pairs_before": 0,
        "total_pairs_after": 0,
        "removed_temporal": 0,
        "removed_logical": 0,
        "removed_low_score": 0,
        "removed_llm_failed": 0,
    }

    for group in market_candidates:
        filtered_markets = []

        for market in group.get("markets", []):
            target_market_id = market["market_id"]

            # Filter YES covering candidates
            filtered_yes = []
            for cover in market.get("yes_covering_candidates", []):
                stats["total_pairs_before"] += 1
                key = (target_market_id, cover["market_id"], "yes")
                validation = pair_lookup.get(key, {})

                score = validation.get("viability_score", 0)
                if score >= min_score and validation.get("is_valid", False):
                    # Add validation metadata to cover
                    cover["_validation"] = {
                        "score": score,
                        "analysis": validation.get("brief_analysis", ""),
                    }
                    filtered_yes.append(cover)
                    stats["total_pairs_after"] += 1
                else:
                    # Track rejection reason
                    if not validation.get("temporal_valid", True):
                        stats["removed_temporal"] += 1
                    elif not validation.get("logical_valid", True):
                        stats["removed_logical"] += 1
                    elif "LLM" in (validation.get("rejection_reason") or ""):
                        stats["removed_llm_failed"] += 1
                    else:
                        stats["removed_low_score"] += 1

            # Filter NO covering candidates
            filtered_no = []
            for cover in market.get("no_covering_candidates", []):
                stats["total_pairs_before"] += 1
                key = (target_market_id, cover["market_id"], "no")
                validation = pair_lookup.get(key, {})

                score = validation.get("viability_score", 0)
                if score >= min_score and validation.get("is_valid", False):
                    cover["_validation"] = {
                        "score": score,
                        "analysis": validation.get("brief_analysis", ""),
                    }
                    filtered_no.append(cover)
                    stats["total_pairs_after"] += 1
                else:
                    if not validation.get("temporal_valid", True):
                        stats["removed_temporal"] += 1
                    elif not validation.get("logical_valid", True):
                        stats["removed_logical"] += 1
                    elif "LLM" in (validation.get("rejection_reason") or ""):
                        stats["removed_llm_failed"] += 1
                    else:
                        stats["removed_low_score"] += 1

            filtered_markets.append(
                {
                    **market,
                    "yes_covering_candidates": filtered_yes,
                    "no_covering_candidates": filtered_no,
                }
            )

        filtered.append(
            {
                **group,
                "markets": filtered_markets,
            }
        )

    stats["retention_rate"] = (
        round(stats["total_pairs_after"] / stats["total_pairs_before"], 3)
        if stats["total_pairs_before"]
        else 0
    )

    return filtered, stats


# =============================================================================
# MAIN
# =============================================================================


def get_latest_folder(base_dir: Path) -> Path | None:
    """Get latest run folder."""
    if not base_dir.exists():
        return None
    folders = [f for f in base_dir.iterdir() if f.is_dir()]
    return max(folders, key=lambda f: f.stat().st_mtime) if folders else None


async def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("STEP 05b: Validate Market Candidates (LLM)")
    logger.info(f"Min viability score: {MIN_VIABILITY_SCORE}")
    logger.info("=" * 70)

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not set")
        return

    # Load input
    input_folder = (
        INPUT_DIR / INPUT_RUN_FOLDER
        if INPUT_RUN_FOLDER
        else get_latest_folder(INPUT_DIR)
    )
    if not input_folder:
        logger.error("No input folder found")
        return

    input_file = input_folder / "market_candidates.json"
    if not input_file.exists():
        logger.error(f"File not found: {input_file}")
        return

    logger.info(f"Loading from: {input_file}")
    with open(input_file, encoding="utf-8") as f:
        data = json.load(f)

    market_candidates = data.get("market_candidates", [])
    logger.info(f"Loaded {len(market_candidates)} groups")

    # Collect pairs for validation
    logger.info("\nCollecting pairs for validation...")
    pairs = collect_pairs_for_validation(market_candidates)
    logger.info(f"Total pairs to validate: {len(pairs)}")

    if not pairs:
        logger.warning("No pairs to validate!")
        return

    # Validate via LLM
    logger.info("\nValidating via LLM...")
    async with httpx.AsyncClient(
        base_url=LLM_BASE_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
    ) as client:
        validations = await validate_all_pairs(client, pairs)

    logger.info(f"Validated {len(validations)} pairs")

    # Filter candidates
    logger.info(f"\nFiltering with min_score={MIN_VIABILITY_SCORE}...")
    filtered_candidates, filter_stats = filter_candidates(
        market_candidates, validations, pairs, MIN_VIABILITY_SCORE
    )

    # Save validation details (for debugging)
    run_timestamp = input_folder.name
    output_folder = OUTPUT_DIR / run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    validation_details = [
        {
            "pair_id": p["pair_id"],
            "target_question": p["target"].get("question", ""),
            "cover_question": p["cover"].get("question", ""),
            "cover_side": p["cover_side"],
            **validations.get(p["pair_id"], {}),
        }
        for p in pairs
    ]

    with open(output_folder / "validation_details.json", "w", encoding="utf-8") as f:
        json.dump({"validations": validation_details}, f, indent=2, ensure_ascii=False)

    # Save filtered candidates
    output_data = {
        "_meta": {
            "source": str(input_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": LLM_MODEL,
            "min_viability_score": MIN_VIABILITY_SCORE,
            "description": "Market candidates filtered by LLM validation",
        },
        "market_candidates": filtered_candidates,
    }

    output_file = output_folder / "validated_candidates.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    summary = {
        "script": "05b_validate_candidates",
        "run_at": end_time.isoformat(),
        "duration_seconds": round(duration, 1),
        "config": {
            "model": LLM_MODEL,
            "min_viability_score": MIN_VIABILITY_SCORE,
            "batch_size": BATCH_SIZE,
        },
        "input": str(input_file),
        "filter_stats": filter_stats,
        "output": str(output_folder),
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # Print results
    logger.info("\n" + "=" * 70)
    logger.info("VALIDATION RESULTS")
    logger.info("=" * 70)
    logger.info(f"  Pairs before: {filter_stats['total_pairs_before']}")
    logger.info(f"  Pairs after:  {filter_stats['total_pairs_after']}")
    logger.info(f"  Retention:    {filter_stats['retention_rate']:.1%}")
    logger.info("  Removed by reason:")
    logger.info(f"    - Temporal issues: {filter_stats['removed_temporal']}")
    logger.info(f"    - Logical issues:  {filter_stats['removed_logical']}")
    logger.info(f"    - Low score:       {filter_stats['removed_low_score']}")
    logger.info(f"    - LLM failed:      {filter_stats['removed_llm_failed']}")
    logger.info("=" * 70)
    logger.info(f"Duration: {duration:.1f}s")
    logger.info(f"Output: {output_file}")


if __name__ == "__main__":
    asyncio.run(main())

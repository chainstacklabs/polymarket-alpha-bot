"""
03a: Extract Logical Implications Between Groups (LLM).

WHAT IT DOES
    Uses an LLM to find "if A happens, then B must happen" relationships
    between market groups. These logical implications are the foundation
    for building hedges.

    Example: "If Ukraine holds an election → Ukraine must have called one"
    (you can't hold an election without calling it first)

WHY WE NEED THIS
    To build a hedge, we need pairs where one outcome GUARANTEES another.
    If we bet on "election held = NO" and it resolves YES, we need a cover
    that MUST pay out. Only necessary implications provide this guarantee.

HOW IT WORKS
    1. For each target group, show LLM all other groups
    2. Ask: "Which events have logical relationships with this one?"
    3. LLM returns three relationship types:
       - "implies": target YES → other YES (target happening causes other)
       - "implied_by": other YES → target YES (other happening causes target)
       - "inverse": negative correlation (one up, other down)
    4. Each relationship includes confidence: "necessary" vs "strong"

RELATIONSHIP TYPES EXPLAINED
    - implies:    "Region captured" → "City captured" (can't capture region
                  without capturing cities inside it)
    - implied_by: "Election held" ← "Election called" (holding requires calling)
    - inverse:    "Ceasefire signed" ↔ "War escalates" (mutually exclusive)

PIPELINE
    02_build_groups → [03a_extract_implications] → 03b_derive_covers

INPUT
    data/02_build_groups/<timestamp>/
        - groups.json: Market groups with titles and partition types

OUTPUT
    data/03a_extract_implications/<timestamp>/
        - raw_implications.json : LLM-extracted relationships per group
        - summary.json          : Run stats (counts by type)

RUNTIME
    ~30 seconds per group (LLM-bound)
    TEST_MODE (3 groups): ~1 minute
    Full run (500+ groups): ~4-6 hours

CONFIGURATION
    - TEST_MODE: Only process TEST_GROUP_TITLES subset (True for dev)
    - LLM_MODEL: Model to use (default: claude-sonnet-4)
    - MAX_TOKENS: Max response length (8000)
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
INPUT_SCRIPT_DIR = DATA_DIR / "02_build_groups"
INPUT_RUN_FOLDER: str | None = None
SCRIPT_OUTPUT_DIR = DATA_DIR / "03a_extract_implications"

# LLM settings
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
LLM_MODEL = "anthropic/claude-sonnet-4"
LLM_BASE_URL = "https://openrouter.ai/api/v1"
LLM_TIMEOUT = 120.0
MAX_RETRIES = 3
MAX_TOKENS = 8000

# Test mode - only process specific groups
TEST_MODE = True
TEST_GROUP_TITLES = [
    # Quick validation subset (3 groups)
    "Ukraine election called by...?",
    "Ukraine election held by...?",
    "Will Zelenskyy talk to Putin by...?",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


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
# LLM CLIENT
# =============================================================================


def extract_json_from_response(text: str) -> dict | None:
    """Extract and parse JSON from LLM response."""
    text = text.strip()

    # Remove markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1]
    if "```" in text:
        text = text.split("```")[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object
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
# MAIN
# =============================================================================


def get_input_folder() -> Path | None:
    """Get input folder (specified or latest)."""
    if INPUT_RUN_FOLDER:
        return INPUT_SCRIPT_DIR / INPUT_RUN_FOLDER
    if not INPUT_SCRIPT_DIR.exists():
        return None
    folders = [f for f in INPUT_SCRIPT_DIR.iterdir() if f.is_dir()]
    return max(folders, key=lambda f: f.stat().st_mtime) if folders else None


async def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 70)
    logger.info("STEP 03a: Extract Implications (LLM)")
    logger.info("=" * 70)

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not set")
        return

    # Load input
    input_folder = get_input_folder()
    if not input_folder or not input_folder.exists():
        logger.error(f"Input folder not found: {input_folder}")
        return

    groups_file = input_folder / "groups.json"
    if not groups_file.exists():
        logger.error(f"Groups file not found: {groups_file}")
        return

    logger.info(f"Loading from: {groups_file}")
    with open(groups_file, encoding="utf-8") as f:
        data = json.load(f)

    groups = data.get("groups", [])
    logger.info(f"Loaded {len(groups)} groups")

    # Format all titles for prompt context
    group_titles_text = "\n".join(f"{i}. {g['title']}" for i, g in enumerate(groups, 1))

    # Select groups to process
    if TEST_MODE:
        target_groups = [g for g in groups if g["title"] in TEST_GROUP_TITLES]
        logger.info(f"TEST MODE: Processing {len(target_groups)} groups")
    else:
        target_groups = groups
        logger.info(f"Processing all {len(target_groups)} groups")

    if not target_groups:
        logger.error("No target groups found!")
        return

    # Process each target group
    results = []

    async with httpx.AsyncClient(
        base_url=LLM_BASE_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
    ) as client:
        for i, target_group in enumerate(target_groups):
            logger.info(f"\n[{i + 1}/{len(target_groups)}] {target_group['title']}")

            prompt = IMPLICATION_PROMPT.format(
                group_titles_text=group_titles_text,
                target_title=target_group["title"],
            )

            llm_result = await call_llm(
                client, prompt, f"implications/{target_group['group_id']}"
            )

            if not llm_result:
                logger.warning("  Failed to get LLM response")
                results.append(
                    {
                        "group_id": target_group["group_id"],
                        "title": target_group["title"],
                        "error": "LLM call failed",
                        "implies": [],
                        "implied_by": [],
                        "inverse": [],
                    }
                )
                continue

            # Log counts
            logger.info(
                f"  Found: {len(llm_result.get('implies', []))} implies, "
                f"{len(llm_result.get('implied_by', []))} implied_by, "
                f"{len(llm_result.get('inverse', []))} inverse"
            )

            results.append(
                {
                    "group_id": target_group["group_id"],
                    "title": target_group["title"],
                    **llm_result,
                }
            )

    # Save raw results
    input_run_timestamp = input_folder.name
    output_folder = SCRIPT_OUTPUT_DIR / input_run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    output_data = {
        "_meta": {
            "source": str(groups_file),
            "input_run": input_run_timestamp,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": LLM_MODEL,
            "test_mode": TEST_MODE,
            "description": "Raw LLM implication extraction - needs processing by 03b",
        },
        "raw_implications": results,
    }

    output_file = output_folder / "raw_implications.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    # Summary
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()

    total_implies = sum(len(r.get("implies", [])) for r in results)
    total_implied_by = sum(len(r.get("implied_by", [])) for r in results)
    total_inverse = sum(len(r.get("inverse", [])) for r in results)

    summary = {
        "script": "03a_extract_implications",
        "run_at": end_time.isoformat(),
        "duration_seconds": round(duration, 1),
        "config": {"model": LLM_MODEL, "test_mode": TEST_MODE},
        "input": {
            "groups_file": str(groups_file),
            "groups_processed": len(target_groups),
        },
        "results": {
            "total_implies": total_implies,
            "total_implied_by": total_implied_by,
            "total_inverse": total_inverse,
        },
        "output": {"folder": str(output_folder)},
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info("\n" + "=" * 70)
    logger.info(f"Groups processed: {len(target_groups)}")
    logger.info(f"Total implies: {total_implies}")
    logger.info(f"Total implied_by: {total_implied_by}")
    logger.info(f"Total inverse: {total_inverse}")
    logger.info(f"Duration: {duration:.1f}s")
    logger.info(f"Output: {output_file}")
    logger.info("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

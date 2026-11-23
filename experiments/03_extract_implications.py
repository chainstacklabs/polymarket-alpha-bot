"""
03: Extract Logical Implications Between Groups (LLM), then Derive Covers.

WHAT IT DOES
    Asks an LLM, once per target group, which other groups GUARANTEE it or are
    GUARANTEED by it, then turns those raw relationships into hedge covers.

    Example: "Ukraine holds an election" → "Ukraine calls an election"
    (you can't hold an election without calling it first)

WHY WE NEED THIS
    A hedge needs a cover that MUST pay out when the main bet loses. Only
    logically necessary implications give that guarantee; correlations don't.

HOW IT WORKS (mirrors backend/core/steps/implications.py)
    1. List every group with its structure (format_group_for_prompt) as context.
    2. Per target group, send IMPLICATION_PROMPT (identical to production; the
       target sits at the end so the shared prefix can be prompt-cached).
    3. The LLM returns two lists:
       - implied_by (OTHER → TARGET): contrapositive "NOT target → NOT other",
         so buying NO on OTHER covers a YES position on TARGET.
       - implies (TARGET → OTHER): if target is YES, other is YES, so buying
         YES on OTHER covers a NO position on TARGET.
    4. derive_covers() matches titles back to groups and gives each cover
       probability 0.98 ("necessary") plus its bracket_alignment.
    Production caches results in SQLite; this script processes every group.

INPUT
    data/02_build_groups/<run>/groups.json  (latest run, or --run <run>)

OUTPUT
    data/03_extract_implications/<run>/
        - implications.json : [{group_id, title, yes_covered_by, no_covered_by,
                               extracted_at, llm_model}], as production stores it
        - summary.json      : counts

USAGE
    uv run --frozen --project backend experiments/03_extract_implications.py --dry-run
    uv run --frozen --project backend experiments/03_extract_implications.py [--limit N]

    --dry-run prints the exact prompt for the first target and the number of LLM
    calls a real run makes, without calling the LLM or needing an API key.
    A real run calls OpenRouter (OPENROUTER_API_KEY, IMPLICATIONS_MODEL) and costs money.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_DIR = DATA_DIR / "02_build_groups"
OUTPUT_DIR = DATA_DIR / "03_extract_implications"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_TIMEOUT = 60.0  # httpx timeout per request
LLM_MAX_RETRIES = 3  # retries inside one request (429 / network errors)
MAX_CONCURRENT = 8  # runner.py passes max_concurrent=8
ATTEMPTS = 3  # outer attempts per group
ATTEMPT_TIMEOUT = 90  # hard timeout per attempt, seconds

# Probability for necessary relationships
NECESSARY_PROBABILITY = 0.98
# Multiplier for "implies" direction (1.0 = treat both directions equally)
IMPLIES_MULTIPLIER = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# PROMPT (verbatim copy of production IMPLICATION_PROMPT)
# =============================================================================

IMPLICATION_PROMPT = """Find ONLY logically necessary relationships between prediction market events.

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

## BRACKET ALIGNMENT (REQUIRED for each relationship)

Each event has multiple sub-markets (brackets). You must specify how the implication maps across brackets.

RULE: Look at the group type shown in square brackets (e.g. [candidate group], [timeframe group]).

- "match": ONLY for [candidate group] ↔ [candidate group] where both groups share the SAME people/entities.
  The implication applies per-person: "Cepeda wins 1st round → Cepeda wins election", NOT "Cepeda → Dávila".
  Example: "Colombia 1st round" [candidate] and "Colombia Election" [candidate] — same candidates → "match"

- "all": For EVERYTHING ELSE. This includes:
  • [timeframe group] ↔ [timeframe group] — dates are NOT entities to match, each date combo needs evaluation
  • [timeframe group] ↔ [candidate group] — different bracket types
  • [threshold group] ↔ anything — different bracket types
  • [candidate group] ↔ [candidate group] where groups have DIFFERENT people — no shared entities to match
  Example: "Capture Lyman by [dates]" and "Capture Donetsk by [dates]" → "all" (dates are deadlines, not entities)
  Example: "Fed Chair nominee" [candidate] and "Fed Chair confirmed by" [timeframe] → "all"

When in doubt, use "all". Using "match" incorrectly on timeframe groups will DISCARD valid pairs.

## OUTPUT FORMAT (JSON only):
```json
{{
  "implied_by": [
    {{
      "group_title": "exact title from list",
      "explanation": "why other=YES makes target=YES logically certain",
      "counterexample_attempt": "I tried to imagine [scenario] but it's impossible because [reason]",
      "bracket_alignment": "match or all"
    }}
  ],
  "implies": [
    {{
      "group_title": "exact title from list",
      "explanation": "why target=YES makes other=YES logically certain",
      "counterexample_attempt": "I tried to imagine [scenario] but it's impossible because [reason]",
      "bracket_alignment": "match or all"
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

## TARGET EVENT
Find the necessary relationships between the TARGET event below and the AVAILABLE EVENTS listed above. Use the exact titles from the AVAILABLE EVENTS list.

TARGET: "{target_title}"

Output ONLY the JSON described in OUTPUT FORMAT above.
"""


def format_group_for_prompt(group: dict, max_brackets: int = 8) -> str:
    """Format group with structure info for the implication prompt."""
    title = group["title"]
    partition = group.get("partition_type", "unknown")
    markets = group.get("markets", [])
    brackets = [m.get("bracket_label", "?") for m in markets if m.get("bracket_label")]

    if not brackets:
        return f'"{title}"'

    label = {
        "candidate": "options",
        "timeframe": "deadlines",
        "threshold": "ranges",
    }.get(partition, "options")

    if len(brackets) <= max_brackets:
        bracket_str = ", ".join(brackets)
    else:
        bracket_str = (
            ", ".join(brackets[:max_brackets]) + f", ... ({len(brackets)} total)"
        )

    return f'"{title}" [{partition} group, {len(brackets)} {label}: {bracket_str}]'


def build_prompt(group_titles_text: str, target: dict) -> str:
    return IMPLICATION_PROMPT.format(
        group_titles_text=group_titles_text, target_title=target["title"]
    )


# =============================================================================
# LLM (OpenRouter chat completions, like core/models.py LLMClient)
# =============================================================================


class LLMAuthError(RuntimeError):
    """OpenRouter rejected the key (401/403): abort instead of returning empties."""


async def complete(client: httpx.AsyncClient, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    for attempt in range(LLM_MAX_RETRIES):
        try:
            resp = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions", json=payload
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 429:
                await asyncio.sleep(2**attempt)
                continue
            if code in (401, 403):
                raise LLMAuthError(
                    f"OpenRouter rejected the request (HTTP {code})"
                ) from e
            raise
        except httpx.RequestError:
            if attempt < LLM_MAX_RETRIES - 1:
                await asyncio.sleep(1)
                continue
            raise
    raise RuntimeError(f"Failed after {LLM_MAX_RETRIES} attempts")


def extract_json_from_response(text: str) -> dict | None:
    """Parse JSON from an LLM reply (```json fences, raw JSON, or JSON inside text)."""
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


# =============================================================================
# COVER DERIVATION (contrapositive logic)
# =============================================================================


def match_title_to_group(
    title: str,
    groups_by_title: dict[str, dict],
    groups_by_title_lower: dict[str, dict],
) -> dict | None:
    """Match an LLM-returned title to a group: exact, case-insensitive, then substring."""
    if title in groups_by_title:
        return groups_by_title[title]
    title_lower = title.lower().strip()
    if title_lower in groups_by_title_lower:
        return groups_by_title_lower[title_lower]
    for group_title, group in groups_by_title.items():
        if title_lower in group_title.lower() or group_title.lower() in title_lower:
            return group
    return None


def make_cover(
    matched: dict, item: dict, position: str, kind: str, prob: float
) -> dict:
    return {
        "group_id": matched["group_id"],
        "title": matched["title"],
        "cover_position": position,
        "relationship": f"necessary ({kind}): {item.get('explanation', '')}",
        "relationship_type": "necessary",
        "probability": prob,
        "counterexample_attempt": item.get("counterexample_attempt", ""),
        "bracket_alignment": item.get("bracket_alignment", "all"),
    }


def derive_covers(
    llm_result: dict,
    target_group: dict,
    groups_by_title: dict[str, dict],
    groups_by_title_lower: dict[str, dict],
) -> dict:
    """
    Turn raw LLM relationships into covers for the target group.

    - implied_by (other → target): contrapositive, NO on other covers target YES
    - implies (target → other): direct, YES on other covers target NO
    """
    target_id = target_group["group_id"]
    yes_covered_by, no_covered_by = [], []

    for item in llm_result.get("implied_by", []):
        matched = match_title_to_group(
            item.get("group_title", ""), groups_by_title, groups_by_title_lower
        )
        if matched and matched["group_id"] != target_id:
            yes_covered_by.append(
                make_cover(matched, item, "NO", "contrapositive", NECESSARY_PROBABILITY)
            )

    implies_prob = round(NECESSARY_PROBABILITY * IMPLIES_MULTIPLIER, 4)
    for item in llm_result.get("implies", []):
        matched = match_title_to_group(
            item.get("group_title", ""), groups_by_title, groups_by_title_lower
        )
        if matched and matched["group_id"] != target_id:
            no_covered_by.append(
                make_cover(matched, item, "YES", "direct", implies_prob)
            )

    return {
        "group_id": target_id,
        "title": target_group["title"],
        "yes_covered_by": yes_covered_by,
        "no_covered_by": no_covered_by,
    }


def empty_implication(group: dict) -> dict:
    return {
        "group_id": group["group_id"],
        "title": group["title"],
        "yes_covered_by": [],
        "no_covered_by": [],
    }


# =============================================================================
# EXTRACTION (concurrent, like extract_implications_batch)
# =============================================================================


async def extract_all(
    targets: list[dict], all_groups: list[dict], model: str
) -> list[dict]:
    group_titles_text = "\n".join(
        f"{i}. {format_group_for_prompt(g)}" for i, g in enumerate(all_groups, 1)
    )
    by_title = {g["title"]: g for g in all_groups}
    by_title_lower = {g["title"].lower().strip(): g for g in all_groups}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    done = 0

    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT, headers=headers) as client:

        async def process(target: dict) -> dict:
            nonlocal done
            async with semaphore:
                prompt = build_prompt(group_titles_text, target)
                result = None
                for attempt in range(ATTEMPTS):
                    try:
                        reply = await asyncio.wait_for(
                            complete(client, model, prompt), timeout=ATTEMPT_TIMEOUT
                        )
                        parsed = extract_json_from_response(str(reply))
                        result = (
                            derive_covers(parsed, target, by_title, by_title_lower)
                            if parsed
                            else empty_implication(target)
                        )
                        break
                    except asyncio.TimeoutError:
                        log.warning(
                            f"Timeout ({attempt + 1}/{ATTEMPTS}) {target['title'][:40]}"
                        )
                        if attempt < ATTEMPTS - 1:
                            await asyncio.sleep(2**attempt)
                    except LLMAuthError:
                        raise
                    except Exception as e:
                        log.error(
                            f"Error ({attempt + 1}/{ATTEMPTS}) {target['title'][:40]}: {e}"
                        )
                        if attempt < ATTEMPTS - 1:
                            await asyncio.sleep(1)
                            continue
                        break
                if result is None:
                    result = empty_implication(target)
                done += 1
                log.info(
                    f"[{done}/{len(targets)}] {target['title'][:50]}: "
                    f"{len(result['yes_covered_by'])} YES, "
                    f"{len(result['no_covered_by'])} NO covers"
                )
                return result

        return await asyncio.gather(*(process(t) for t in targets))


# =============================================================================
# MAIN
# =============================================================================


def find_run(run: str | None) -> Path:
    if run:
        folder = INPUT_DIR / run
    else:
        runs = sorted(p for p in INPUT_DIR.glob("*") if (p / "groups.json").exists())
        if not runs:
            sys.exit(f"No runs in {INPUT_DIR}; run 02_build_groups.py first")
        folder = runs[-1]
    if not (folder / "groups.json").exists():
        sys.exit(f"Missing {folder / 'groups.json'}")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--run", help="run timestamp (default: latest 02 run)")
    parser.add_argument("--limit", type=int, help="only the first N groups as targets")
    parser.add_argument(
        "--dry-run", action="store_true", help="print prompt, no LLM call"
    )
    args = parser.parse_args()

    load_dotenv(Path(__file__).parent.parent / ".env")
    in_folder = find_run(args.run)
    groups = json.loads((in_folder / "groups.json").read_text(encoding="utf-8"))
    targets = groups[: args.limit] if args.limit else groups
    log.info(f"Run {in_folder.name}: {len(groups)} groups, {len(targets)} targets")

    if args.dry_run:
        if not targets:
            sys.exit("No groups to build a prompt for")
        group_titles_text = "\n".join(
            f"{i}. {format_group_for_prompt(g)}" for i, g in enumerate(groups, 1)
        )
        print(build_prompt(group_titles_text, targets[0]))
        print(
            f"--- dry run: a real run makes {len(targets)} LLM calls "
            f"(one per target, up to {MAX_CONCURRENT} at once)"
        )
        return

    model = os.getenv("IMPLICATIONS_MODEL")
    if not model or not os.getenv("OPENROUTER_API_KEY"):
        sys.exit("Set IMPLICATIONS_MODEL and OPENROUTER_API_KEY (in .env)")
    log.info(f"Model: {model}")

    implications = asyncio.run(extract_all(targets, groups, model))
    extracted_at = datetime.now(timezone.utc).isoformat()
    for impl in implications:  # the two columns state.get_all_implications() adds
        impl["extracted_at"] = extracted_at
        impl["llm_model"] = model

    out = OUTPUT_DIR / in_folder.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "implications.json").write_text(
        json.dumps(implications, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary = {
        "run": in_folder.name,
        "created_at": extracted_at,
        "model": model,
        "groups": len(groups),
        "targets": len(targets),
        "with_covers": sum(
            1 for i in implications if i["yes_covered_by"] or i["no_covered_by"]
        ),
        "yes_covers": sum(len(i["yes_covered_by"]) for i in implications),
        "no_covers": sum(len(i["no_covered_by"]) for i in implications),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info(f"Saved to {out}: {summary}")


if __name__ == "__main__":
    main()

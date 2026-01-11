"""
Event semantics extraction.

Extracted from experiments/04_1_extract_event_semantics.py for production pipeline.

Parses event titles into structured semantic components:
- event_type: OCCURRENCE, THRESHOLD, DEADLINE, COMPARISON, COUNT
- polarity: POSITIVE, NEGATIVE, NEUTRAL
- subject_entity, predicate, object_entity
- outcome_states: economic_crisis, military_conflict, peace_outcome, etc.
- timeframe and condition (via regex)
"""

import re
from typing import Any, Callable

from loguru import logger

from core.models import get_llm_client
from core.state import PipelineState

# =============================================================================
# CONFIGURATION
# =============================================================================

BATCH_SIZE = 10

EVENT_TYPES = ["OCCURRENCE", "THRESHOLD", "DEADLINE", "COMPARISON", "COUNT"]
POLARITIES = ["POSITIVE", "NEGATIVE", "NEUTRAL"]
OUTCOME_STATES = [
    "economic_crisis",
    "military_conflict",
    "peace_outcome",
    "regime_change",
    "market_shock",
    "policy_shift",
]

# =============================================================================
# REGEX PATTERNS
# =============================================================================

# Year patterns
YEAR_PATTERN = re.compile(
    r"\b(?:in|by|before|after|during)?\s*(\d{4})\b\??", re.IGNORECASE
)

# Date patterns
MONTH_NAMES = (
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
)
DATE_PATTERN = re.compile(
    rf"\b(?:by|before|on)?\s*(?:{MONTH_NAMES})\s*\d{{1,2}}?,?\s*\d{{4}}\b",
    re.IGNORECASE,
)
MONTH_YEAR_PATTERN = re.compile(
    rf"\b(?:by|before|in)?\s*(?:{MONTH_NAMES})\s*\d{{4}}\b", re.IGNORECASE
)
QUARTER_PATTERN = re.compile(r"\bQ([1-4])\s*(\d{4})\b", re.IGNORECASE)

# Threshold patterns
THRESHOLD_PATTERN = re.compile(
    r"\b(?:more than|less than|at least|over|under|above|below|exceed|reach)?\s*"
    r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?:or more|or less|or fewer|\+|%|percent|people|million|billion|trillion)?\b",
    re.IGNORECASE,
)
PERCENTAGE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
RANGE_PATTERN = re.compile(r"(\d{1,3}(?:,\d{3})*)-(\d{1,3}(?:,\d{3})*)", re.IGNORECASE)

# =============================================================================
# LLM PROMPTS
# =============================================================================

SYSTEM_PROMPT = """You are an expert at analyzing prediction market event titles and extracting structured semantic components.

CONTEXT: These are event titles from Polymarket political prediction markets.

Your task: Extract semantic structure from event titles.

OUTPUT RULES:
- event_type: OCCURRENCE (binary yes/no), THRESHOLD (numeric comparison), DEADLINE (time-bounded), COMPARISON (relative), COUNT (enumeration)
- polarity: POSITIVE (good/constructive like peace, growth), NEGATIVE (bad/destructive like war, recession), NEUTRAL (no value judgment)
- subject_entity: The entity performing the action (lowercase)
- predicate: The main action/event verb phrase
- object_entity: The entity receiving the action (lowercase)
- outcome_states: List from [economic_crisis, military_conflict, peace_outcome, regime_change, market_shock, policy_shift]
- negation: true if the event asks about something NOT happening

Output valid JSON only, no markdown."""

USER_PROMPT_TEMPLATE = """Analyze these prediction market events and extract structured semantics.

Events:
{events_text}

For each event, return JSON array with:
{{
  "id": "event_id",
  "event_type": "OCCURRENCE|THRESHOLD|DEADLINE|COMPARISON|COUNT",
  "polarity": "POSITIVE|NEGATIVE|NEUTRAL",
  "subject_entity": "entity or null",
  "predicate": "main action phrase",
  "object_entity": "entity or null",
  "outcome_states": ["list of applicable states"],
  "negation": false
}}

Return a JSON array with one object per event."""


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def parse_number(text: str) -> int | float | None:
    """Parse a number string, handling commas."""
    text = text.replace(",", "").strip()
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return None


def extract_timeframe(title: str, end_date: str | None = None) -> dict | None:
    """Extract timeframe from event title using regex."""
    # Try quarter pattern
    quarter_match = QUARTER_PATTERN.search(title)
    if quarter_match:
        quarter = int(quarter_match.group(1))
        year = int(quarter_match.group(2))
        quarter_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
        return {
            "type": "deadline",
            "end_date": f"{year}-{quarter_end[quarter]}",
            "year": year,
            "quarter": quarter,
        }

    # Try date pattern
    date_match = DATE_PATTERN.search(title)
    if date_match:
        year_in_date = YEAR_PATTERN.search(date_match.group(0))
        if year_in_date:
            year = int(year_in_date.group(1))
            return {"type": "deadline", "end_date": f"{year}-12-31", "year": year}

    # Try month-year pattern
    month_year_match = MONTH_YEAR_PATTERN.search(title)
    if month_year_match:
        year_in_match = YEAR_PATTERN.search(month_year_match.group(0))
        if year_in_match:
            year = int(year_in_match.group(1))
            return {"type": "deadline", "end_date": f"{year}-12-31", "year": year}

    # Try year pattern
    year_match = YEAR_PATTERN.search(title)
    if year_match:
        year = int(year_match.group(1))
        if 2020 <= year <= 2035:
            return {"type": "deadline", "end_date": f"{year}-12-31", "year": year}

    return None


def extract_threshold(title: str) -> dict | None:
    """Extract threshold/condition from event title using regex."""
    # Check for percentage
    pct_match = PERCENTAGE_PATTERN.search(title)
    if pct_match:
        value = parse_number(pct_match.group(1))
        if value is not None:
            operator = ">="
            title_lower = title.lower()
            if "less than" in title_lower or "under" in title_lower:
                operator = "<"
            elif "more than" in title_lower or "over" in title_lower:
                operator = ">"
            return {
                "type": "threshold",
                "operator": operator,
                "value": value,
                "unit": "percent",
            }

    # Check for range
    range_match = RANGE_PATTERN.search(title)
    if range_match:
        low = parse_number(range_match.group(1))
        high = parse_number(range_match.group(2))
        if low is not None and high is not None:
            return {"type": "range", "low": low, "high": high, "unit": "count"}

    # Check for threshold numbers
    threshold_match = THRESHOLD_PATTERN.search(title)
    if threshold_match:
        value = parse_number(threshold_match.group(1))
        if value is not None and value > 100:
            operator = ">="
            title_lower = title.lower()
            if "less than" in title_lower or "under" in title_lower:
                operator = "<"
            elif "more than" in title_lower or "exceed" in title_lower:
                operator = ">"
            elif "or more" in title_lower or "at least" in title_lower:
                operator = ">="
            elif "or less" in title_lower:
                operator = "<="
            return {
                "type": "threshold",
                "operator": operator,
                "value": value,
                "unit": "count",
            }

    return None


def parse_llm_response(response: str) -> list[dict]:
    """Parse JSON array from LLM response."""
    import json

    response = response.strip()
    if response.startswith("```"):
        response = response.split("\n", 1)[1]
    if response.endswith("```"):
        response = response.rsplit("```", 1)[0]
    response = response.strip()

    try:
        result = json.loads(response)
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        start = response.find("[")
        end = response.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(response[start : end + 1])
            except json.JSONDecodeError:
                pass
        return []


# =============================================================================
# MAIN FUNCTIONS
# =============================================================================


async def extract_semantics_batch(
    events: list[dict],
    entity_lookup: dict[str, str],
) -> list[dict]:
    """Process a batch of events through LLM for semantics extraction."""
    llm = get_llm_client()

    # Build prompt
    events_text = "\n".join(
        f'{i + 1}. [ID: {e["id"]}] "{e.get("title", "")}"' for i, e in enumerate(events)
    )
    prompt = USER_PROMPT_TEMPLATE.format(events_text=events_text)

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        response = await llm.complete(messages, temperature=0.1)
        llm_results = parse_llm_response(response)
    except Exception as e:
        logger.warning(f"LLM semantics extraction failed: {e}")
        llm_results = []

    # Create lookup by ID
    llm_by_id = {str(r.get("id", "")): r for r in llm_results}

    results = []
    for event in events:
        event_id = event["id"]
        title = event.get("title", "")
        end_date = event.get("endDate")

        llm_result = llm_by_id.get(event_id, {})

        # Extract timeframe and condition with regex
        timeframe = extract_timeframe(title, end_date)
        condition = extract_threshold(title)

        # Normalize entities using lookup
        subject = llm_result.get("subject_entity")
        if subject and isinstance(subject, str):
            subject = entity_lookup.get(subject.lower(), subject.lower())

        obj = llm_result.get("object_entity")
        if obj and isinstance(obj, str):
            obj = entity_lookup.get(obj.lower(), obj.lower())

        # Validate fields
        event_type = llm_result.get("event_type", "OCCURRENCE")
        if event_type not in EVENT_TYPES:
            event_type = "OCCURRENCE"

        polarity = llm_result.get("polarity", "NEUTRAL")
        if polarity not in POLARITIES:
            polarity = "NEUTRAL"

        outcome_states = llm_result.get("outcome_states", [])
        if not isinstance(outcome_states, list):
            outcome_states = []
        outcome_states = [s for s in outcome_states if s in OUTCOME_STATES]

        semantics = {
            "event_type": event_type,
            "polarity": polarity,
            "subject_entity": subject,
            "predicate": llm_result.get("predicate"),
            "object_entity": obj,
            "condition": condition,
            "timeframe": timeframe,
            "outcome_states": outcome_states,
            "negation": bool(llm_result.get("negation", False)),
        }

        results.append({"id": event_id, "title": title, "semantics": semantics})

    return results


async def extract_event_semantics(
    events: list[dict],
    state: PipelineState,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, dict]:
    """
    Extract structured semantics from all events.

    Args:
        events: NLP-prepared events
        state: Pipeline state for entity mappings
        progress_callback: Optional callback(message: str) to report progress

    Returns:
        Dict mapping event_id to semantics dict
    """
    # Build entity lookup from state
    entity_mappings = state.get_entity_mappings()
    entity_lookup = {k.lower(): v.lower() for k, v in entity_mappings.items()}

    all_results = []
    total = len(events)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, total, BATCH_SIZE):
        batch = events[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        batch_results = await extract_semantics_batch(batch, entity_lookup)
        all_results.extend(batch_results)

        # Report progress every 3 batches
        if batch_num % 3 == 0 or batch_num == 1:
            progress_msg = (
                f"Batch {batch_num}/{total_batches} ({len(all_results)}/{total} events)"
            )
            logger.debug(f"Semantics: {progress_msg}")
            if progress_callback:
                progress_callback(progress_msg)

    # Convert to dict by ID
    semantics_by_id = {r["id"]: r for r in all_results}
    logger.info(f"Extracted semantics for {len(semantics_by_id)} events")

    return semantics_by_id


def get_semantics_for_prioritization(
    semantics_by_id: dict[str, dict],
) -> dict[str, dict[str, Any]]:
    """
    Extract key fields for pair prioritization and classification.

    Returns dict of event_id -> {outcome_states, polarity, subject_entity, predicate,
                                 event_type, condition, title}
    """
    result = {}
    for event_id, data in semantics_by_id.items():
        sem = data.get("semantics", {})
        result[event_id] = {
            "outcome_states": set(sem.get("outcome_states", [])),
            "polarity": sem.get("polarity"),
            "subject_entity": sem.get("subject_entity"),
            "predicate": sem.get("predicate"),
            "title": data.get("title", ""),
            # Add event_type and condition for threshold detection
            "event_type": sem.get("event_type", "OCCURRENCE"),
            "condition": sem.get("condition"),
        }
    return result

"""
Relation extraction and classification.

Combines logic from:
- experiments/03_4_extract_relations.py
- experiments/05_1_block_candidate_pairs.py
- experiments/05_2_classify_structural.py
- experiments/05_3_classify_causal.py

For production pipeline with incremental support.
"""

import asyncio
from typing import Callable

import numpy as np
from loguru import logger

from core.models import get_llm_client

# =============================================================================
# CONFIGURATION
# =============================================================================

# Blocking thresholds
FAISS_TOP_K = 50
SIMILARITY_THRESHOLD = 0.55  # Increased from 0.5 for stricter filtering
REQUIRE_SHARED_ENTITY = True  # Require at least 1 shared entity between events

# Bidirectional classification thresholds
BIDIRECTIONAL_CORRELATION_THRESHOLD = 0.6  # If both directions > this, it's correlation
BIDIRECTIONAL_DIRECTION_MARGIN = 0.2  # Winner must exceed loser by this margin

# Performance optimization thresholds
CONCURRENT_BATCHES = 4  # Number of batch requests to run in parallel
PRE_FILTER_HIGH_SIM = 0.95  # Auto-classify as CORRELATED if similarity > this
PRE_FILTER_LOW_SIM = 0.58  # Auto-classify as INDEPENDENT if similarity < this
SKIP_BIDIRECTIONAL_CONFIDENCE = 0.85  # Skip reverse check if forward confidence > this

# Relation types
STRUCTURAL_RELATIONS = [
    "TIMEFRAME_VARIANT",
    "THRESHOLD_VARIANT",
    "SUBSET_VARIANT",  # Specific instance implies general category (Cuba → Latin America)
    "HIERARCHICAL",
    "SERIES_MEMBER",
    "MUTUALLY_EXCLUSIVE",
]

# Geographic subset relationships for SUBSET_VARIANT detection
GEOGRAPHIC_SUBSETS = {
    "cuba": ["latin america", "latin american country", "caribbean"],
    "mexico": ["latin america", "latin american country", "north america"],
    "venezuela": ["latin america", "latin american country", "south america"],
    "brazil": ["latin america", "latin american country", "south america"],
    "argentina": ["latin america", "latin american country", "south america"],
    "colombia": ["latin america", "latin american country", "south america"],
    "chile": ["latin america", "latin american country", "south america"],
    "peru": ["latin america", "latin american country", "south america"],
    "taiwan": ["asia", "east asia", "indo-pacific"],
    "japan": ["asia", "east asia"],
    "south korea": ["asia", "east asia"],
    "ukraine": ["europe", "eastern europe"],
    "poland": ["europe", "eastern europe", "nato country"],
    "germany": ["europe", "western europe", "nato country", "eu country"],
    "france": ["europe", "western europe", "nato country", "eu country"],
    "iran": ["middle east", "persian gulf"],
    "iraq": ["middle east", "persian gulf"],
    "syria": ["middle east"],
    "israel": ["middle east"],
    "saudi arabia": ["middle east", "persian gulf", "gulf state"],
}

CAUSAL_RELATIONS = [
    "DIRECT_CAUSE",
    "ENABLING_CONDITION",
    "INHIBITING_CONDITION",
    "REQUIRES",
    "CORRELATED",
    "INDEPENDENT",
]

# Confounding variable detection - groups of entities that share common causes
# When a pair has entities from the same confounder group, the LLM is warned
CONFOUNDERS = {
    "trump_admin": {
        "entities": [
            "trump",
            "donald trump",
            "elon",
            "elon musk",
            "doge",
            "musk",
            "administration",
            "cabinet",
            "white house",
            "executive order",
        ],
        "warning": "Both events depend on Trump administration actions/decisions",
    },
    "ukraine_conflict": {
        "entities": [
            "ukraine",
            "russia",
            "nato",
            "zelensky",
            "putin",
            "kyiv",
            "moscow",
            "crimea",
            "donbas",
            "ceasefire",
        ],
        "warning": "Both events are outcomes of the ongoing Ukraine conflict",
    },
    "macro_economy": {
        "entities": [
            "inflation",
            "deficit",
            "fed",
            "federal reserve",
            "interest rate",
            "recession",
            "gdp",
            "unemployment",
            "treasury",
            "fiscal",
        ],
        "warning": "Both events respond to macroeconomic conditions (common driver)",
    },
    "us_elections": {
        "entities": [
            "election",
            "vote",
            "ballot",
            "campaign",
            "democrat",
            "republican",
            "congress",
            "senate",
            "house",
            "midterm",
        ],
        "warning": "Both events are influenced by US electoral dynamics",
    },
}

# GLiNER2 relation labels
RELATION_LABELS = {
    "causes": "A causes or leads to B happening",
    "requires": "B requires A to happen first",
    "prevents": "A prevents or blocks B",
    "enables": "A makes B possible but doesn't guarantee it",
    "same_topic": "A and B are about the same subject",
    "timeframe_of": "A is a time-bound version of B",
    "threshold_of": "A is a threshold variant of B",
    "part_of": "A is part of a larger series/group B",
    "opposite_of": "A and B are mutually exclusive",
}


# =============================================================================
# CANDIDATE PAIR BLOCKING
# =============================================================================


def _get_entity_canonicals(
    event_id: str, entities_by_event: dict[str, list[dict]]
) -> set[str]:
    """Extract canonical entity names for an event."""
    entities = entities_by_event.get(event_id, [])
    return {e.get("canonical", "").lower() for e in entities if e.get("canonical")}


def _has_shared_entities(
    event_a_id: str,
    event_b_id: str,
    entities_by_event: dict[str, list[dict]],
) -> bool:
    """Check if two events share at least one entity."""
    entities_a = _get_entity_canonicals(event_a_id, entities_by_event)
    entities_b = _get_entity_canonicals(event_b_id, entities_by_event)
    return bool(entities_a & entities_b)


def _detect_confounders(
    event_a: dict,
    event_b: dict,
    entities_by_event: dict[str, list[dict]] | None = None,
) -> list[str]:
    """
    Detect potential confounding variables between two events.

    Checks if both events share entities from known confounder groups
    (e.g., Trump administration, Ukraine conflict, macroeconomic factors).

    Args:
        event_a: First event dict (with 'id' and 'title')
        event_b: Second event dict (with 'id' and 'title')
        entities_by_event: Optional dict mapping event_id -> list of entity dicts

    Returns:
        List of warning messages for detected confounders
    """
    warnings = []

    # Collect all text to check for confounder entities
    text_a = event_a.get("title", "").lower()
    text_b = event_b.get("title", "").lower()

    # Add entity canonicals if available
    if entities_by_event:
        entities_a = _get_entity_canonicals(event_a.get("id", ""), entities_by_event)
        entities_b = _get_entity_canonicals(event_b.get("id", ""), entities_by_event)
        text_a = f"{text_a} {' '.join(entities_a)}"
        text_b = f"{text_b} {' '.join(entities_b)}"

    # Check each confounder group
    for confounder_name, confounder_data in CONFOUNDERS.items():
        entities = confounder_data["entities"]
        warning = confounder_data["warning"]

        # Check if both events have entities from this confounder group
        a_has_confounder = any(entity in text_a for entity in entities)
        b_has_confounder = any(entity in text_b for entity in entities)

        if a_has_confounder and b_has_confounder:
            warnings.append(f"⚠️ CONFOUNDER ({confounder_name}): {warning}")

    return warnings


def block_candidate_pairs(
    new_events: list[dict],
    all_events: list[dict],
    new_embeddings: np.ndarray,
    all_embeddings: np.ndarray,
    all_event_ids: list[str],
    entities_by_event: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """
    Find candidate event pairs for relation classification.

    Uses FAISS for fast approximate nearest neighbor search.
    For incremental mode, finds pairs between new events and all events.

    Filtering criteria:
    1. Similarity > SIMILARITY_THRESHOLD (0.55)
    2. If REQUIRE_SHARED_ENTITY is True, must have at least 1 shared entity

    Args:
        new_events: Newly added events
        all_events: All events (including new)
        new_embeddings: Embeddings for new events
        all_embeddings: All embeddings
        all_event_ids: IDs corresponding to all_embeddings
        entities_by_event: Optional dict mapping event_id -> list of entity dicts

    Returns:
        List of candidate pairs with similarity scores
    """
    try:
        import faiss
    except ImportError:
        logger.warning("FAISS not available, using brute force search")
        return _brute_force_pairs(
            new_events, all_embeddings, all_event_ids, new_embeddings, entities_by_event
        )

    if len(all_embeddings) == 0 or len(new_embeddings) == 0:
        return []

    # Build FAISS index
    dim = all_embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)  # Inner product = cosine for normalized vectors
    index.add(all_embeddings.astype(np.float32))

    # Search for each new event
    new_event_ids = [e["id"] for e in new_events]
    distances, indices = index.search(
        new_embeddings.astype(np.float32), min(FAISS_TOP_K, len(all_embeddings))
    )

    # Build candidate pairs
    pairs = []
    seen = set()

    for i, event_id in enumerate(new_event_ids):
        for j, idx in enumerate(indices[i]):
            if idx < 0:
                continue

            other_id = all_event_ids[idx]
            if event_id == other_id:
                continue

            similarity = float(distances[i][j])
            if similarity < SIMILARITY_THRESHOLD:
                continue

            # Check for shared entities if required
            if REQUIRE_SHARED_ENTITY and entities_by_event:
                if not _has_shared_entities(event_id, other_id, entities_by_event):
                    continue

            # Create canonical pair key (sorted to avoid duplicates)
            pair_key = tuple(sorted([event_id, other_id]))
            if pair_key in seen:
                continue
            seen.add(pair_key)

            pairs.append(
                {
                    "event_a_id": pair_key[0],
                    "event_b_id": pair_key[1],
                    "similarity": similarity,
                }
            )

    # Log filtering stats
    if REQUIRE_SHARED_ENTITY and entities_by_event:
        logger.info(
            f"Found {len(pairs)} candidate pairs for {len(new_events)} new events "
            f"(threshold={SIMILARITY_THRESHOLD}, shared_entity=required)"
        )
    else:
        logger.info(
            f"Found {len(pairs)} candidate pairs for {len(new_events)} new events"
        )
    return pairs


def _brute_force_pairs(
    new_events: list[dict],
    all_embeddings: np.ndarray,
    all_event_ids: list[str],
    new_embeddings: np.ndarray,
    entities_by_event: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Fallback brute force pair finding."""
    pairs = []
    new_event_ids = [e["id"] for e in new_events]

    for i, event_id in enumerate(new_event_ids):
        similarities = np.dot(all_embeddings, new_embeddings[i])

        for j, sim in enumerate(similarities):
            if sim < SIMILARITY_THRESHOLD:
                continue

            other_id = all_event_ids[j]
            if event_id == other_id:
                continue

            # Check for shared entities if required
            if REQUIRE_SHARED_ENTITY and entities_by_event:
                if not _has_shared_entities(event_id, other_id, entities_by_event):
                    continue

            pairs.append(
                {
                    "event_a_id": min(event_id, other_id),
                    "event_b_id": max(event_id, other_id),
                    "similarity": float(sim),
                }
            )

    # Deduplicate
    seen = set()
    unique = []
    for p in pairs:
        key = (p["event_a_id"], p["event_b_id"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


# =============================================================================
# STRUCTURAL CLASSIFICATION
# =============================================================================


def classify_structural(
    pairs: list[dict],
    events_by_id: dict[str, dict],
    semantics_by_id: dict[str, dict] | None = None,
) -> list[dict]:
    """
    Classify structural relations using rules.

    Structural relations are deterministic based on:
    - Shared entities
    - Title/question similarity patterns
    - Time/threshold patterns
    - Event type relationships (COUNT meta-market + THRESHOLD)
    """
    classified = []
    semantics_by_id = semantics_by_id or {}

    for pair in pairs:
        event_a = events_by_id.get(pair["event_a_id"])
        event_b = events_by_id.get(pair["event_b_id"])

        if not event_a or not event_b:
            continue

        title_a = event_a.get("title", "").lower()
        title_b = event_b.get("title", "").lower()

        # Get semantic data if available
        sem_a = semantics_by_id.get(pair["event_a_id"], {})
        sem_b = semantics_by_id.get(pair["event_b_id"], {})

        relation_type = None
        confidence = 0.0
        evidence = {}

        # Check for hierarchical relationship (COUNT meta-market + specific THRESHOLD)
        # e.g., "How many X?" + "Will X exceed 750k?"
        if _is_hierarchical_threshold(title_a, title_b, sem_a, sem_b):
            relation_type = "HIERARCHICAL"
            confidence = 0.95

        # Check for timeframe variants (e.g., "by end of 2024" vs "by end of 2025")
        # First try title-based detection
        elif _is_timeframe_variant(title_a, title_b):
            relation_type = "TIMEFRAME_VARIANT"
            confidence = 0.9
            evidence = {"method": "title_pattern"}

        # Then try semantic-based timeframe detection (using parsed end_dates)
        elif _is_semantic_timeframe_variant(sem_a, sem_b, title_a, title_b):
            relation_type = "TIMEFRAME_VARIANT"
            confidence = 0.95
            evidence = {
                "method": "semantic",
                "subject": sem_a.get("subject_entity"),
                "predicate": sem_a.get("predicate"),
            }

        # Check for subset variants (Cuba → Latin America)
        elif (is_subset := _is_subset_variant(title_a, title_b))[0]:
            relation_type = "SUBSET_VARIANT"
            confidence = 0.9
            evidence = is_subset[1]

        # Check for threshold variants (e.g., ">50%" vs ">60%")
        elif _is_threshold_variant(title_a, title_b):
            relation_type = "THRESHOLD_VARIANT"
            confidence = 0.9

        # Check for mutual exclusivity (opposite outcomes)
        elif _is_mutually_exclusive(title_a, title_b):
            relation_type = "MUTUALLY_EXCLUSIVE"
            confidence = 0.85

        if relation_type:
            result = {
                "source_id": pair["event_a_id"],
                "target_id": pair["event_b_id"],
                "relation_type": relation_type,
                "confidence": confidence,
                "classification_method": "structural_rules",
            }
            if evidence:
                result["evidence"] = evidence
            classified.append(result)

    logger.info(f"Classified {len(classified)} structural relations")
    return classified


def _is_hierarchical_threshold(
    title_a: str,
    title_b: str,
    sem_a: dict,
    sem_b: dict,
) -> bool:
    """
    Check if one event is a COUNT/meta-market and the other is a specific THRESHOLD.

    Examples:
    - "How many people will Trump deport?" (COUNT) + "Will Trump deport 750k+?" (THRESHOLD)
    - "What will GDP growth be?" (COUNT) + "Will GDP exceed 3%?" (THRESHOLD)

    These are HIERARCHICAL: the COUNT market encompasses multiple threshold outcomes,
    so treating them as correlated with equal probability is incorrect.
    """
    import re
    from rapidfuzz import fuzz

    type_a = sem_a.get("event_type", "OCCURRENCE")
    type_b = sem_b.get("event_type", "OCCURRENCE")
    cond_a = sem_a.get("condition")
    cond_b = sem_b.get("condition")

    # Pattern 1: One is COUNT, the other has a threshold condition
    # COUNT = "how many", "what will", "# of" type questions
    if type_a == "COUNT" and type_b == "THRESHOLD" and cond_b:
        return True
    if type_b == "COUNT" and type_a == "THRESHOLD" and cond_a:
        return True

    # Pattern 2: Detect "how many" / "# of" in title without semantic data
    meta_patterns = [
        r"^how many\b",
        r"^# of\b",
        r"^number of\b",
        r"^what will .+ be\??$",
        r"^what .+ will\b",
    ]

    def is_meta_market(title: str) -> bool:
        for pattern in meta_patterns:
            if re.search(pattern, title, re.IGNORECASE):
                return True
        return False

    # Pattern 3: One is meta-market title, other has specific threshold number
    threshold_pattern = r"\b\d{1,3}(?:,\d{3})+\b|\b\d+%\b|\b(?:at least|or more|exceed|over|above)\s+\d+"

    a_is_meta = is_meta_market(title_a)
    b_is_meta = is_meta_market(title_b)
    a_has_threshold = bool(re.search(threshold_pattern, title_a, re.IGNORECASE))
    b_has_threshold = bool(re.search(threshold_pattern, title_b, re.IGNORECASE))

    if a_is_meta and b_has_threshold and not a_has_threshold:
        # Check if they're about the same topic (fuzzy match after removing numbers)
        clean_pattern = r"\b\d+(?:,\d+)*(?:\.\d+)?%?\b"
        a_clean = re.sub(clean_pattern, "", title_a).strip()
        b_clean = re.sub(clean_pattern, "", title_b).strip()
        if fuzz.token_set_ratio(a_clean, b_clean) > 60:
            return True

    if b_is_meta and a_has_threshold and not b_has_threshold:
        clean_pattern = r"\b\d+(?:,\d+)*(?:\.\d+)?%?\b"
        a_clean = re.sub(clean_pattern, "", title_a).strip()
        b_clean = re.sub(clean_pattern, "", title_b).strip()
        if fuzz.token_set_ratio(a_clean, b_clean) > 60:
            return True

    return False


def _is_timeframe_variant(title_a: str, title_b: str) -> bool:
    """
    Check if titles differ only by timeframe.

    Enhanced patterns to catch:
    - Years: 2024, 2025, 2026
    - Month names: January, Feb, etc.
    - Day numbers with months: "June 30", "March 31"
    - Date phrases: "end of", "before", "by"
    - Quarters: Q1, Q2
    """
    import re

    # Comprehensive date removal patterns (order matters - more specific first)
    date_patterns = [
        # Full dates: "June 30, 2026", "March 31"
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}(?:,?\s*\d{4})?\b",
        # Month + year: "June 2026", "December 2025"
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+\d{4}\b",
        # Just months
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b",
        # Years: 2024, 2025, 2026, 2027
        r"\b20[2-3]\d\b",
        # Quarters: Q1, Q2, Q3, Q4
        r"\bq[1-4]\b",
        # Date phrases that indicate timeframes
        r"\b(?:end\s+of|before|by|in|during)\b",
        # Ordinal day numbers: "31st", "30th", "1st"
        r"\b\d{1,2}(?:st|nd|rd|th)\b",
        # Standalone day numbers when adjacent to removed content
        r"\b(?:30|31)\b",
    ]

    a_clean = title_a.lower()
    b_clean = title_b.lower()

    for pattern in date_patterns:
        a_clean = re.sub(pattern, "", a_clean, flags=re.IGNORECASE)
        b_clean = re.sub(pattern, "", b_clean, flags=re.IGNORECASE)

    # Normalize whitespace
    a_clean = re.sub(r"\s+", " ", a_clean).strip()
    b_clean = re.sub(r"\s+", " ", b_clean).strip()

    # Remove trailing punctuation that might differ
    a_clean = re.sub(r"[?,!]+$", "", a_clean).strip()
    b_clean = re.sub(r"[?,!]+$", "", b_clean).strip()

    # Check if >75% similar after removing dates (relaxed from 80%)
    from rapidfuzz import fuzz

    similarity = fuzz.ratio(a_clean, b_clean)
    return similarity > 75


def _is_threshold_variant(title_a: str, title_b: str) -> bool:
    """Check if titles differ only by threshold value."""
    import re

    # Remove numeric thresholds and compare
    pattern = r"\b\d+(\.\d+)?%?\b"
    a_clean = re.sub(pattern, "", title_a)
    b_clean = re.sub(pattern, "", title_b)
    from rapidfuzz import fuzz

    return fuzz.ratio(a_clean, b_clean) > 85


def _is_mutually_exclusive(title_a: str, title_b: str) -> bool:
    """Check if titles represent opposite outcomes."""
    opposites = [
        ("win", "lose"),
        ("yes", "no"),
        ("above", "below"),
        ("more", "less"),
        ("increase", "decrease"),
    ]
    for pos, neg in opposites:
        if (pos in title_a and neg in title_b) or (neg in title_a and pos in title_b):
            return True
    return False


def _is_subset_variant(title_a: str, title_b: str) -> tuple[bool, dict]:
    """
    Check if one event is a specific instance that implies a general category.

    Examples:
    - "U.S. invade Cuba" → "U.S. invade Latin American country" (Cuba is subset of Latin America)
    - "China invade Taiwan" → "China invade East Asia" (Taiwan is in East Asia)

    Returns:
        tuple of (is_subset_variant, evidence_dict)
    """
    title_a_lower = title_a.lower()
    title_b_lower = title_b.lower()

    # Check if one title has a specific location and the other has its parent region
    for specific, parents in GEOGRAPHIC_SUBSETS.items():
        # Case 1: A has specific, B has parent
        if specific in title_a_lower:
            for parent in parents:
                if parent in title_b_lower:
                    # Verify they're about the same action (simple word overlap check)
                    # Remove the geographic terms and check similarity
                    a_action = title_a_lower.replace(specific, "").strip()
                    b_action = title_b_lower.replace(parent, "").strip()
                    from rapidfuzz import fuzz

                    if fuzz.ratio(a_action, b_action) > 70:
                        return True, {
                            "specific": specific,
                            "parent": parent,
                            "direction": "a_implies_b",
                        }

        # Case 2: B has specific, A has parent
        if specific in title_b_lower:
            for parent in parents:
                if parent in title_a_lower:
                    a_action = title_a_lower.replace(parent, "").strip()
                    b_action = title_b_lower.replace(specific, "").strip()
                    from rapidfuzz import fuzz

                    if fuzz.ratio(a_action, b_action) > 70:
                        return True, {
                            "specific": specific,
                            "parent": parent,
                            "direction": "b_implies_a",
                        }

    return False, {}


def _is_semantic_timeframe_variant(
    sem_a: dict, sem_b: dict, title_a: str, title_b: str
) -> bool:
    """
    Check if two events are timeframe variants using semantic data.

    Uses extracted subject_entity, predicate, and end_date from semantic parsing.
    If subject+predicate match but end_dates differ, it's a timeframe variant.
    """
    if not sem_a or not sem_b:
        return False

    subj_a = sem_a.get("subject_entity")
    subj_b = sem_b.get("subject_entity")
    pred_a = sem_a.get("predicate")
    pred_b = sem_b.get("predicate")

    # Need matching subject and predicate
    if not (subj_a and subj_b and pred_a and pred_b):
        return False

    # Check for matching subject and predicate
    if subj_a.lower() != subj_b.lower():
        return False

    # Predicate comparison with fuzzy matching (allow minor variations)
    from rapidfuzz import fuzz

    if fuzz.ratio(pred_a.lower(), pred_b.lower()) < 80:
        return False

    # Check for different timeframes
    tf_a = sem_a.get("timeframe", {})
    tf_b = sem_b.get("timeframe", {})

    if not tf_a or not tf_b:
        # Fallback: if subject+predicate match with high confidence,
        # and titles have different date-like patterns, likely timeframe variant
        return False

    end_a = tf_a.get("end_date")
    end_b = tf_b.get("end_date")

    if end_a and end_b and end_a != end_b:
        return True

    return False


# =============================================================================
# PAIR PRIORITIZATION (from 05_3_classify_causal.py)
# =============================================================================


def prioritize_pairs(
    pairs: list[dict],
    semantics_by_id: dict[str, dict],
) -> list[dict]:
    """
    Order pairs by likelihood of causal relationship.
    Higher score = more likely causal.

    Prioritization based on:
    - Shared outcome_states (most important, +10)
    - Opposite polarity (inhibiting?, +5)
    - Same subject entity (+3)
    - Same predicate (+2)
    """
    scored = []

    for pair in pairs:
        score = 0
        sem_a = semantics_by_id.get(pair["event_a_id"], {})
        sem_b = semantics_by_id.get(pair["event_b_id"], {})

        # Shared outcome states (most important)
        states_a = sem_a.get("outcome_states", set())
        states_b = sem_b.get("outcome_states", set())
        if states_a and states_b and states_a & states_b:
            score += 10

        # Opposite polarity (inhibiting?)
        pol_a = sem_a.get("polarity")
        pol_b = sem_b.get("polarity")
        if pol_a and pol_b and pol_a != pol_b:
            score += 5

        # Same subject entity
        subj_a = sem_a.get("subject_entity")
        subj_b = sem_b.get("subject_entity")
        if subj_a and subj_b and subj_a == subj_b:
            score += 3

        # Same predicate
        pred_a = sem_a.get("predicate")
        pred_b = sem_b.get("predicate")
        if pred_a and pred_b and pred_a == pred_b:
            score += 2

        scored.append((score, pair))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    return [pair for _, pair in scored]


# =============================================================================
# CAUSAL CLASSIFICATION (LLM) - Enhanced with semantics
# =============================================================================

LLM_SYSTEM_PROMPT = """You are an expert analyst evaluating causal relationships between prediction market events.

ANALYSIS PROCESS (follow these steps):

STEP 1: UNDERSTAND THE EVENTS
- What is event A asking? What would make it resolve YES?
- What is event B asking? What would make it resolve YES?
- Are they about the same topic, related topics, or unrelated?

STEP 2: IDENTIFY CAUSAL MECHANISM
- If A happens, what is the specific mechanism that affects B?
- List intermediate steps: A → [mechanism] → B
- Example: "NATO deploys" → "prevents Russian victory" → "reduces Ukraine capitulation probability"
- If no clear mechanism exists, it may be CORRELATED or INDEPENDENT

STEP 3: DETERMINE DIRECTION
- Temporal: Which would happen first?
- Logical: Does A cause B, B cause A, or mutual influence?
- CAUTION: Interventions often PREVENT outcomes, not cause them
  - Example: "Medical treatment" PREVENTS "disease" (INHIBITING)
  - Example: "NATO deployment" PREVENTS "ally capitulation" (INHIBITING)

STEP 4: CLASSIFY RELATION TYPE

DIRECT_CAUSE: A directly causes B with strong mechanism
- High confidence in causation
- Clear, short causal chain
- Example: "Nuclear war declared" → "Stock market crashes"
- P(B|A) typically 70-95%

ENABLING_CONDITION: A makes B possible or more likely, but doesn't guarantee it
- A creates conditions for B
- Other factors also matter
- Example: "Peace talks begin" → "Ceasefire achieved"
- P(B|A) typically 40-70%

INHIBITING_CONDITION: A prevents or reduces likelihood of B
- A blocks or counteracts B
- Interventions, preventions, opposites
- Example: "Military intervention" → "Aggressor victory" (A inhibits B)
- P(B|A) typically 5-30% (LOW probability when A occurs)

REQUIRES: B cannot happen without A (strong dependency)
- B is impossible if A doesn't happen first
- Example: "War declared" → "War casualties" (can't have casualties without war)
- P(B|¬A) = 0 or near 0

CORRELATED: A and B co-occur but causal direction unclear
- Both might be caused by a third factor
- Or bidirectional influence
- Example: "Oil prices spike" ↔ "Inflation rises"
- When unsure of direction, use CORRELATED

INDEPENDENT: No meaningful connection
- Events are about unrelated topics
- No plausible mechanism
- Example: "Sports championship outcome" and "Tax policy change"

STEP 5: ESTIMATE P(B|A) AND P(B|¬A)

Guidelines for estimation:
- P(B|A) = Probability of B given A happens
- P(B|¬A) = Probability of B given A does NOT happen

Base rate check:
- If events are independent: P(B|A) ≈ P(B|¬A) ≈ P(B)
- If A causes B: P(B|A) >> P(B|¬A)
- If A inhibits B: P(B|A) << P(B|¬A)

Confidence calibration:
- Very few events have P(B|A) > 85%
- Most DIRECT_CAUSE relations are 60-80%
- Be conservative: lower confidence is better than overconfidence

STEP 6: SELF-CRITIQUE
Ask yourself:
- Does this mechanism make logical sense?
- Am I confusing correlation with causation?
- Is the direction correct? (does A prevent B rather than cause it?)
- Is my P(B|A) estimate overconfident?
- What could I be wrong about?

COMMON MISTAKES TO AVOID:
❌ "Geopolitical crisis → Inflation" (too generic, many confounders)
❌ "Intervention → Bad outcome" (interventions often PREVENT bad outcomes)
❌ Assigning P(B|A) > 80% without strong evidence
❌ Ignoring temporal order (B happens before A, so A can't cause B)

Output valid JSON only, no other text."""


def _build_llm_batch_prompt(
    pairs: list[dict],
    events_by_id: dict[str, dict],
    semantics_by_id: dict[str, dict],
    entities_by_event: dict[str, list[dict]] | None = None,
) -> str:
    """Build prompt for batch LLM classification with semantic info, descriptions, and confounder warnings."""
    prompt_parts = ["Classify the causal relationships for these event pairs:\n"]

    for i, pair in enumerate(pairs):
        event_a = events_by_id.get(pair["event_a_id"], {})
        event_b = events_by_id.get(pair["event_b_id"], {})
        sem_a = semantics_by_id.get(pair["event_a_id"], {})
        sem_b = semantics_by_id.get(pair["event_b_id"], {})

        prompt_parts.append(f"\n=== PAIR {i + 1} ===")

        # Check for confounders and add warnings at the top of each pair
        confounder_warnings = _detect_confounders(event_a, event_b, entities_by_event)
        if confounder_warnings:
            prompt_parts.append("\n".join(confounder_warnings))
            prompt_parts.append(
                "Consider: Is this CAUSAL or just CORRELATED due to shared context?\n"
            )
        prompt_parts.append(f'Event A: "{event_a.get("title", "N/A")}"')

        # Add description (first 200 chars)
        desc_a = event_a.get("description", "")
        if desc_a and str(desc_a).strip():
            truncated = desc_a[:200]
            prompt_parts.append(
                f'  Description: "{truncated}{"..." if len(desc_a) > 200 else ""}"'
            )

        if sem_a:
            prompt_parts.append(f"  - Type: {sem_a.get('event_type', 'N/A')}")
            prompt_parts.append(f"  - Subject: {sem_a.get('subject_entity', 'N/A')}")
            prompt_parts.append(f"  - Polarity: {sem_a.get('polarity', 'N/A')}")
            if sem_a.get("outcome_states"):
                prompt_parts.append(
                    f"  - Outcome states: {list(sem_a['outcome_states'])}"
                )

        prompt_parts.append(f'Event B: "{event_b.get("title", "N/A")}"')

        # Add description (first 200 chars)
        desc_b = event_b.get("description", "")
        if desc_b and str(desc_b).strip():
            truncated = desc_b[:200]
            prompt_parts.append(
                f'  Description: "{truncated}{"..." if len(desc_b) > 200 else ""}"'
            )

        if sem_b:
            prompt_parts.append(f"  - Type: {sem_b.get('event_type', 'N/A')}")
            prompt_parts.append(f"  - Subject: {sem_b.get('subject_entity', 'N/A')}")
            prompt_parts.append(f"  - Polarity: {sem_b.get('polarity', 'N/A')}")
            if sem_b.get("outcome_states"):
                prompt_parts.append(
                    f"  - Outcome states: {list(sem_b['outcome_states'])}"
                )

    prompt_parts.append("\n\nOutput JSON array with one object per pair:")
    prompt_parts.append("""[
  {
    "pair": 1,
    "reasoning": "STEP 2 mechanism: [explain], STEP 3 direction: [explain], STEP 5 estimate: [explain]",
    "relation_type": "DIRECT_CAUSE|ENABLING_CONDITION|INHIBITING_CONDITION|REQUIRES|CORRELATED|INDEPENDENT",
    "direction": "forward|reverse|bidirectional",
    "confidence": 0.0-1.0,
    "P_B_given_A": 0.0-1.0,
    "P_B_given_not_A": 0.0-1.0,
    "self_critique": "Any doubts or alternative interpretations"
  },
  ...
]""")

    return "\n".join(prompt_parts)


def _parse_llm_batch_response(response: str, num_pairs: int) -> list[dict]:
    """Parse LLM JSON batch response."""
    import json
    import re

    # Try to extract JSON array from response
    try:
        start = response.find("[")
        end = response.rfind("]") + 1
        if start >= 0 and end > start:
            json_str = response[start:end]
            results = json.loads(json_str)
            return results
    except json.JSONDecodeError:
        pass

    # Try to parse as individual JSON objects
    results = []
    for i in range(num_pairs):
        try:
            pattern = rf'\{{"pair":\s*{i + 1}[^}}]+\}}'
            match = re.search(pattern, response)
            if match:
                results.append(json.loads(match.group()))
        except (json.JSONDecodeError, AttributeError):
            continue

    return results


def _create_reverse_pair(pair: dict) -> dict:
    """Create a reversed pair (swap A and B) for bidirectional classification."""
    return {
        "event_a_id": pair["event_b_id"],
        "event_b_id": pair["event_a_id"],
        "similarity": pair.get("similarity", 0.5),
    }


def _pre_filter_pair(pair: dict) -> dict | None:
    """
    Pre-filter obvious cases based on similarity to skip LLM calls.

    High similarity (>0.95): Likely CORRELATED (same topic)
    Low similarity (<0.58): Likely INDEPENDENT (no relation)

    Args:
        pair: Candidate pair with similarity score

    Returns:
        Pre-classified relation dict, or None if LLM classification needed
    """
    similarity = pair.get("similarity", 0.5)

    # Very high similarity = likely same topic, correlation not causation
    if similarity > PRE_FILTER_HIGH_SIM:
        return {
            "source_id": pair["event_a_id"],
            "target_id": pair["event_b_id"],
            "relation_type": "CORRELATED",
            "confidence": 0.9,
            "direction": "bidirectional",
            "reasoning": f"Auto-classified: Very high similarity ({similarity:.2f}) suggests same-topic correlation",
            "implied_conditional": {
                "P_B_given_A": 0.6,
                "P_B_given_not_A": 0.4,
                "P_A_given_B": 0.6,
                "P_A_given_not_B": 0.4,
            },
            "classification_method": "auto_high_sim",
            "self_critique": "Pre-filtered due to extreme similarity - may miss nuanced causal relations",
        }

    # Low similarity (but above blocking threshold) = likely independent
    if similarity < PRE_FILTER_LOW_SIM:
        return {
            "source_id": pair["event_a_id"],
            "target_id": pair["event_b_id"],
            "relation_type": "INDEPENDENT",
            "confidence": 0.7,
            "direction": "none",
            "reasoning": f"Auto-classified: Low similarity ({similarity:.2f}) suggests no meaningful relation",
            "implied_conditional": {
                "P_B_given_A": 0.5,
                "P_B_given_not_A": 0.5,
                "P_A_given_B": 0.5,
                "P_A_given_not_B": 0.5,
            },
            "classification_method": "auto_low_sim",
            "self_critique": "Pre-filtered due to low similarity - may miss weak causal connections",
        }

    return None  # Needs LLM classification


def _resolve_bidirectional(
    forward_result: dict | None,
    reverse_result: dict | None,
    original_pair: dict,
) -> dict | None:
    """
    Resolve bidirectional classification results into a single relation.

    Logic:
    1. If both directions have high confidence (>0.6): It's correlation, not causation
    2. If forward wins by margin (>0.2): Use forward direction (A → B)
    3. If reverse wins by margin (>0.2): Swap to reverse direction (B → A)
    4. Otherwise: Weak signal, treat as correlation or skip

    Args:
        forward_result: LLM result for A → B classification
        reverse_result: LLM result for B → A classification
        original_pair: Original pair dict with event_a_id, event_b_id

    Returns:
        Resolved relation dict, or None if no clear relationship
    """
    # Handle missing results
    if not forward_result and not reverse_result:
        return None

    # Get confidence scores (default to 0 if missing)
    forward_conf = float(forward_result.get("confidence", 0)) if forward_result else 0
    reverse_conf = float(reverse_result.get("confidence", 0)) if reverse_result else 0

    # Get relation types
    forward_type = (
        forward_result.get("relation_type", "INDEPENDENT")
        if forward_result
        else "INDEPENDENT"
    )
    reverse_type = (
        reverse_result.get("relation_type", "INDEPENDENT")
        if reverse_result
        else "INDEPENDENT"
    )

    # Skip if both are INDEPENDENT
    if forward_type == "INDEPENDENT" and reverse_type == "INDEPENDENT":
        return None

    # Case 1: Both directions have high confidence → correlation (not directional causation)
    # This catches "reversed causality" errors where the LLM sees a relationship but can't
    # determine direction - a sign that it's actually correlation or mutual influence.
    if (
        forward_conf > BIDIRECTIONAL_CORRELATION_THRESHOLD
        and reverse_conf > BIDIRECTIONAL_CORRELATION_THRESHOLD
    ):
        # Use the lower confidence as our correlation confidence (conservative)
        correlation_conf = min(forward_conf, reverse_conf)

        # Extract probability estimates from both directions
        # Note: LLM returns P_B_given_A at top level, not nested in implied_conditional
        fwd_p_b_given_a = (
            float(forward_result.get("P_B_given_A", 0.5)) if forward_result else 0.5
        )
        fwd_p_b_given_not_a = (
            float(forward_result.get("P_B_given_not_A", 0.5)) if forward_result else 0.5
        )
        rev_p_b_given_a = (
            float(reverse_result.get("P_B_given_A", 0.5)) if reverse_result else 0.5
        )
        rev_p_b_given_not_a = (
            float(reverse_result.get("P_B_given_not_A", 0.5)) if reverse_result else 0.5
        )

        return {
            "source_id": original_pair["event_a_id"],
            "target_id": original_pair["event_b_id"],
            "relation_type": "CORRELATED",
            "confidence": correlation_conf,
            "direction": "bidirectional",
            "reasoning": (
                f"Bidirectional check: Both A→B ({forward_conf:.2f}) and B→A ({reverse_conf:.2f}) "
                f"scored high, indicating correlation rather than directional causation. "
                f"Forward reasoning: {forward_result.get('reasoning', 'N/A')[:200] if forward_result else 'N/A'}"
            ),
            "implied_conditional": {
                "P_B_given_A": fwd_p_b_given_a,
                "P_B_given_not_A": fwd_p_b_given_not_a,
                # Reverse result's P_B_given_A is P(original_A | original_B) = our P_A_given_B
                "P_A_given_B": rev_p_b_given_a,
                "P_A_given_not_B": rev_p_b_given_not_a,
            },
            "classification_method": "llm_bidirectional",
            "self_critique": (
                f"Converted to CORRELATED due to high bidirectional confidence. "
                f"Original types: forward={forward_type}, reverse={reverse_type}"
            ),
        }

    # Case 2: Forward direction clearly wins
    if forward_conf > reverse_conf + BIDIRECTIONAL_DIRECTION_MARGIN:
        if forward_type == "INDEPENDENT":
            return None

        # Extract probabilities from top-level LLM response fields
        fwd_p_b_a = (
            float(forward_result.get("P_B_given_A", 0.5)) if forward_result else 0.5
        )
        fwd_p_b_not_a = (
            float(forward_result.get("P_B_given_not_A", 0.5)) if forward_result else 0.5
        )
        rev_p_b_a = (
            float(reverse_result.get("P_B_given_A", 0.5)) if reverse_result else 0.5
        )
        rev_p_b_not_a = (
            float(reverse_result.get("P_B_given_not_A", 0.5)) if reverse_result else 0.5
        )

        return {
            "source_id": original_pair["event_a_id"],
            "target_id": original_pair["event_b_id"],
            "relation_type": forward_type,
            "confidence": forward_conf,
            "direction": "forward",
            "reasoning": forward_result.get("reasoning", "") if forward_result else "",
            "implied_conditional": {
                "P_B_given_A": fwd_p_b_a,
                "P_B_given_not_A": fwd_p_b_not_a,
                "P_A_given_B": rev_p_b_a,
                "P_A_given_not_B": rev_p_b_not_a,
            },
            "classification_method": "llm_bidirectional",
            "self_critique": (
                f"Forward direction confirmed ({forward_conf:.2f} vs reverse {reverse_conf:.2f}). "
                f"{forward_result.get('self_critique', '') if forward_result else ''}"
            ),
        }

    # Case 3: Reverse direction clearly wins → swap source and target
    if reverse_conf > forward_conf + BIDIRECTIONAL_DIRECTION_MARGIN:
        if reverse_type == "INDEPENDENT":
            return None

        # Extract probabilities from top-level LLM response fields
        # Note: When swapping direction, the reverse result's P_B_given_A becomes our P_B_given_A
        # because in the reverse prompt, event_b was "A" and event_a was "B"
        rev_p_b_a = (
            float(reverse_result.get("P_B_given_A", 0.5)) if reverse_result else 0.5
        )
        rev_p_b_not_a = (
            float(reverse_result.get("P_B_given_not_A", 0.5)) if reverse_result else 0.5
        )
        fwd_p_b_a = (
            float(forward_result.get("P_B_given_A", 0.5)) if forward_result else 0.5
        )
        fwd_p_b_not_a = (
            float(forward_result.get("P_B_given_not_A", 0.5)) if forward_result else 0.5
        )

        return {
            # SWAP source and target
            "source_id": original_pair["event_b_id"],
            "target_id": original_pair["event_a_id"],
            "relation_type": reverse_type,
            "confidence": reverse_conf,
            "direction": "forward",  # Now it's forward from the new source
            "reasoning": (
                f"Direction corrected via bidirectional check. "
                f"{reverse_result.get('reasoning', '') if reverse_result else ''}"
            ),
            "implied_conditional": {
                "P_B_given_A": rev_p_b_a,
                "P_B_given_not_A": rev_p_b_not_a,
                "P_A_given_B": fwd_p_b_a,
                "P_A_given_not_B": fwd_p_b_not_a,
            },
            "classification_method": "llm_bidirectional",
            "self_critique": (
                f"Reversed direction: B→A ({reverse_conf:.2f}) beat A→B ({forward_conf:.2f}). "
                f"{reverse_result.get('self_critique', '') if reverse_result else ''}"
            ),
        }

    # Case 4: No clear winner and at least one is non-INDEPENDENT
    # Treat as weak correlation or skip
    if forward_type != "INDEPENDENT" or reverse_type != "INDEPENDENT":
        # Use the higher confidence result but mark as CORRELATED (uncertain direction)
        if forward_conf >= reverse_conf and forward_type != "INDEPENDENT":
            base = forward_result
            other = reverse_result
        elif reverse_type != "INDEPENDENT":
            base = reverse_result
            other = forward_result
        else:
            return None

        # Extract probabilities from top-level LLM response fields
        base_p_b_a = float(base.get("P_B_given_A", 0.5)) if base else 0.5
        base_p_b_not_a = float(base.get("P_B_given_not_A", 0.5)) if base else 0.5
        other_p_b_a = float(other.get("P_B_given_A", 0.5)) if other else 0.5
        other_p_b_not_a = float(other.get("P_B_given_not_A", 0.5)) if other else 0.5

        return {
            "source_id": original_pair["event_a_id"],
            "target_id": original_pair["event_b_id"],
            "relation_type": "CORRELATED",
            "confidence": max(forward_conf, reverse_conf)
            * 0.8,  # Reduce for ambiguous direction
            "direction": "bidirectional",
            "reasoning": (
                f"Direction ambiguous (forward: {forward_conf:.2f}, reverse: {reverse_conf:.2f}). "
                f"Treating as correlation. {base.get('reasoning', '')[:200] if base else ''}"
            ),
            "implied_conditional": {
                "P_B_given_A": base_p_b_a,
                "P_B_given_not_A": base_p_b_not_a,
                "P_A_given_B": other_p_b_a,
                "P_A_given_not_B": other_p_b_not_a,
            },
            "classification_method": "llm_bidirectional",
            "self_critique": f"Ambiguous direction, converted to CORRELATED. Forward type: {forward_type}, Reverse type: {reverse_type}",
        }

    return None


async def _process_batch_with_semaphore(
    semaphore: asyncio.Semaphore,
    batch: list[dict],
    events_by_id: dict[str, dict],
    semantics_by_id: dict[str, dict],
    entities_by_event: dict[str, list[dict]] | None,
    llm,
    skip_bidirectional_on_high_conf: bool = True,
) -> list[dict]:
    """
    Process a single batch with semaphore rate limiting.

    Implements optimization 5d: Skip reverse check if forward confidence > 0.85
    for clear directional relations (ENABLING_CONDITION, INHIBITING_CONDITION).
    """
    async with semaphore:
        results = []

        # Create forward batch
        forward_batch = batch
        forward_prompt = _build_llm_batch_prompt(
            forward_batch, events_by_id, semantics_by_id, entities_by_event
        )
        forward_messages = [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": forward_prompt},
        ]

        # First, get forward results
        try:
            forward_response = await llm.complete(forward_messages, temperature=0.1)
            forward_str = str(forward_response)
        except Exception as e:
            logger.warning(f"Forward LLM call failed: {e}")
            forward_str = ""

        forward_parsed = _parse_llm_batch_response(forward_str, len(batch))

        # Determine which pairs need reverse check
        pairs_needing_reverse = []
        forward_results_map = {}  # pair index -> forward result

        for i, pair in enumerate(batch):
            forward_result = forward_parsed[i] if i < len(forward_parsed) else None
            forward_results_map[i] = forward_result

            # Validate forward relation type
            if forward_result:
                rel_type = forward_result.get("relation_type", "INDEPENDENT")
                if rel_type not in CAUSAL_RELATIONS:
                    forward_result["relation_type"] = "INDEPENDENT"

                # Optimization 5d: Skip reverse for high-confidence directional relations
                forward_conf = float(forward_result.get("confidence", 0))
                forward_type = forward_result.get("relation_type", "INDEPENDENT")

                if (
                    skip_bidirectional_on_high_conf
                    and forward_conf > SKIP_BIDIRECTIONAL_CONFIDENCE
                    and forward_type
                    in ["ENABLING_CONDITION", "INHIBITING_CONDITION", "DIRECT_CAUSE"]
                ):
                    # Skip reverse check - use forward result directly
                    results.append(
                        {
                            "pair": pair,
                            "forward": forward_result,
                            "reverse": None,
                            "skipped_reverse": True,
                        }
                    )
                    continue

            # This pair needs reverse check
            pairs_needing_reverse.append((i, pair))

        # Get reverse results only for pairs that need them
        if pairs_needing_reverse:
            reverse_batch = [_create_reverse_pair(p) for _, p in pairs_needing_reverse]
            reverse_prompt = _build_llm_batch_prompt(
                reverse_batch, events_by_id, semantics_by_id, entities_by_event
            )
            reverse_messages = [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": reverse_prompt},
            ]

            try:
                reverse_response = await llm.complete(reverse_messages, temperature=0.1)
                reverse_str = str(reverse_response)
            except Exception as e:
                logger.warning(f"Reverse LLM call failed: {e}")
                reverse_str = ""

            reverse_parsed = _parse_llm_batch_response(
                reverse_str, len(pairs_needing_reverse)
            )

            # Map reverse results back to original indices
            for j, (orig_idx, pair) in enumerate(pairs_needing_reverse):
                reverse_result = reverse_parsed[j] if j < len(reverse_parsed) else None

                # Validate reverse relation type
                if reverse_result:
                    rel_type = reverse_result.get("relation_type", "INDEPENDENT")
                    if rel_type not in CAUSAL_RELATIONS:
                        reverse_result["relation_type"] = "INDEPENDENT"

                results.append(
                    {
                        "pair": pair,
                        "forward": forward_results_map[orig_idx],
                        "reverse": reverse_result,
                        "skipped_reverse": False,
                    }
                )

        return results


async def classify_causal(
    pairs: list[dict],
    events_by_id: dict[str, dict],
    semantics_by_id: dict[str, dict] | None = None,
    entities_by_event: dict[str, list[dict]] | None = None,
    max_pairs: int = 10000,
    batch_size: int = 10,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Classify causal relations using LLM with bidirectional verification.

    Performance optimizations (fix #5 from quality-review.local.md):
    - Batch size increased from 3 to 10 (3x fewer API calls)
    - Concurrent batch processing with semaphore (4 parallel batches)
    - Pre-filtering of obvious cases by similarity (skip LLM for extremes)
    - Skip bidirectional check for high-confidence directional relations

    Uses bidirectional classification to prevent reversed causality errors:
    - Classifies both A→B and B→A directions for each pair
    - If both directions score high (>0.6), treats as CORRELATED (not directional)
    - If one direction clearly wins (>0.2 margin), uses that direction
    - Stores both P(B|A) and P(A|B) conditional probability estimates
    - Warns the LLM about potential confounding variables (shared context)

    Args:
        pairs: Candidate pairs (already filtered by blocking)
        events_by_id: Event lookup dict
        semantics_by_id: Optional semantic info per event (from semantics step)
        entities_by_event: Optional dict mapping event_id -> list of entity dicts for confounder detection
        max_pairs: Maximum pairs to classify (LLM cost control)
        batch_size: Pairs per LLM batch request (default 10)
        progress_callback: Optional callback(message: str) to report progress

    Returns:
        List of classified causal relations with implied conditionals
    """
    llm = get_llm_client()
    semantics_by_id = semantics_by_id or {}

    # Prioritize pairs by semantic similarity
    if semantics_by_id:
        pairs = prioritize_pairs(pairs, semantics_by_id)
        logger.debug("Pairs prioritized by semantic similarity")

    # Limit to max_pairs
    to_classify = pairs[:max_pairs]

    if not to_classify:
        return []

    # ==========================================================================
    # OPTIMIZATION 5c: Pre-filter obvious cases before LLM
    # ==========================================================================
    pre_filtered = []
    llm_pairs = []
    auto_high_sim = 0
    auto_low_sim = 0

    for pair in to_classify:
        pre_result = _pre_filter_pair(pair)
        if pre_result:
            pre_filtered.append(pre_result)
            if pre_result["classification_method"] == "auto_high_sim":
                auto_high_sim += 1
            else:
                auto_low_sim += 1
        else:
            llm_pairs.append(pair)

    if pre_filtered:
        logger.info(
            f"Pre-filtered {len(pre_filtered)} pairs "
            f"({auto_high_sim} high-sim → CORRELATED, {auto_low_sim} low-sim → INDEPENDENT)"
        )

    if not llm_pairs:
        logger.info("All pairs pre-filtered, no LLM classification needed")
        return pre_filtered

    # ==========================================================================
    # OPTIMIZATION 5a + 5b: Larger batches + concurrent processing
    # ==========================================================================
    logger.info(
        f"Classifying {len(llm_pairs)} pairs with LLM "
        f"(batch_size={batch_size}, concurrency={CONCURRENT_BATCHES})..."
    )

    classified = list(pre_filtered)  # Start with pre-filtered results
    total_batches = (len(llm_pairs) + batch_size - 1) // batch_size
    direction_corrections = 0
    correlation_conversions = 0
    skipped_bidirectional = 0

    # Create semaphore for concurrent batch limiting
    semaphore = asyncio.Semaphore(CONCURRENT_BATCHES)

    # Split into batches
    batches = [
        llm_pairs[i : i + batch_size] for i in range(0, len(llm_pairs), batch_size)
    ]

    # Process batches concurrently with progress reporting
    completed_batches = 0

    async def process_and_report(batch: list[dict]) -> list[dict]:
        nonlocal completed_batches

        results = await _process_batch_with_semaphore(
            semaphore,
            batch,
            events_by_id,
            semantics_by_id,
            entities_by_event,
            llm,
            skip_bidirectional_on_high_conf=True,
        )

        completed_batches += 1

        # Report progress every 5 batches or at milestones
        if completed_batches % 5 == 0 or completed_batches == total_batches:
            progress_msg = (
                f"LLM batch {completed_batches}/{total_batches} "
                f"({len(classified) + sum(1 for r in results if r)} relations)"
            )
            logger.debug(progress_msg)
            if progress_callback:
                progress_callback(progress_msg)

        return results

    # Launch all batches concurrently (semaphore controls actual parallelism)
    batch_results = await asyncio.gather(
        *[process_and_report(batch) for batch in batches],
        return_exceptions=True,
    )

    # Process results from all batches
    for batch_result in batch_results:
        if isinstance(batch_result, Exception):
            logger.warning(f"Batch processing failed: {batch_result}")
            continue

        for item in batch_result:
            pair = item["pair"]
            forward_result = item["forward"]
            reverse_result = item["reverse"]

            if item.get("skipped_reverse"):
                skipped_bidirectional += 1
                # Use forward result directly for skipped pairs
                if (
                    forward_result
                    and forward_result.get("relation_type") != "INDEPENDENT"
                ):
                    classified.append(
                        {
                            "source_id": pair["event_a_id"],
                            "target_id": pair["event_b_id"],
                            "relation_type": forward_result.get("relation_type"),
                            "confidence": float(forward_result.get("confidence", 0)),
                            "direction": "forward",
                            "reasoning": forward_result.get("reasoning", ""),
                            "implied_conditional": {
                                "P_B_given_A": float(
                                    forward_result.get("P_B_given_A", 0.5)
                                ),
                                "P_B_given_not_A": float(
                                    forward_result.get("P_B_given_not_A", 0.5)
                                ),
                            },
                            "classification_method": "llm_unidirectional",
                            "self_critique": (
                                f"Skipped reverse check due to high confidence ({forward_result.get('confidence', 0):.2f}). "
                                f"{forward_result.get('self_critique', '')}"
                            ),
                        }
                    )
            else:
                # Resolve using bidirectional check
                resolved = _resolve_bidirectional(forward_result, reverse_result, pair)

                if resolved:
                    classified.append(resolved)

                    # Track corrections for logging
                    if resolved.get("classification_method") == "llm_bidirectional":
                        if "Direction corrected" in resolved.get("reasoning", ""):
                            direction_corrections += 1
                        if resolved["relation_type"] == "CORRELATED" and (
                            "Bidirectional check" in resolved.get("reasoning", "")
                            or "Ambiguous direction" in resolved.get("reasoning", "")
                        ):
                            correlation_conversions += 1

    # Final summary
    logger.info(
        f"Classified {len(classified)} causal relations: "
        f"{len(pre_filtered)} pre-filtered, "
        f"{skipped_bidirectional} skipped bidirectional, "
        f"{direction_corrections} dir corrections, "
        f"{correlation_conversions} → CORRELATED"
    )
    return classified


# =============================================================================
# GRAPH BUILDING
# =============================================================================


def _build_edge_from_relation(rel: dict) -> dict:
    """
    Build graph edge dict from relation dict.

    Transforms relation with source_id/target_id into edge with source/target.
    Preserves LLM metadata for causal relations.

    Args:
        rel: Relation dict from classify_structural() or classify_causal()

    Returns:
        Edge dict ready for graph export
    """
    edge = {
        "source": rel["source_id"],
        "target": rel["target_id"],
        "relation_type": rel["relation_type"],
        "confidence": rel.get("confidence", 0.5),
        "classification_method": rel.get("classification_method", "unknown"),
    }

    # Include LLM reasoning metadata for causal relations
    if rel.get("classification_method") in ("llm", "llm_bidirectional"):
        edge["direction"] = rel.get("direction", "")
        edge["reasoning"] = rel.get("reasoning", "")
        edge["implied_conditional"] = rel.get("implied_conditional", {})
        edge["self_critique"] = rel.get("self_critique", "")

    return edge


def build_relation_graph(
    events: list[dict],
    structural_relations: list[dict],
    causal_relations: list[dict],
) -> dict:
    """
    Build the relation graph from classified relations.

    Args:
        events: All events
        structural_relations: Structural relation edges
        causal_relations: Causal relation edges

    Returns:
        Graph dict with nodes and edges
    """
    # Build nodes
    nodes = []
    for event in events:
        price = 0.5
        markets = event.get("markets", [])
        if markets:
            prices = markets[0].get("outcomePrices", [0.5])
            price = prices[0] if prices else 0.5

        nodes.append(
            {
                "id": event["id"],
                "title": event.get("title", ""),
                "current_price": price,
            }
        )

    # Combine edges with full metadata
    edges = []
    seen_edges = set()

    for rel in structural_relations + causal_relations:
        edge_key = (rel["source_id"], rel["target_id"], rel["relation_type"])
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(_build_edge_from_relation(rel))

    graph = {
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }

    logger.info(f"Built graph with {len(nodes)} nodes and {len(edges)} edges")
    return graph


def merge_into_graph(
    existing_graph: dict,
    new_nodes: list[dict],
    new_edges: list[dict],
) -> dict:
    """
    Merge new nodes and edges into existing graph.

    Args:
        existing_graph: Current graph state
        new_nodes: Nodes to add
        new_edges: Edges to add

    Returns:
        Merged graph
    """
    # Get existing node IDs
    existing_node_ids = {n["id"] for n in existing_graph.get("nodes", [])}
    existing_edge_keys = {
        (e["source"], e["target"], e["relation_type"])
        for e in existing_graph.get("edges", [])
    }

    # Add new nodes (skip duplicates)
    merged_nodes = list(existing_graph.get("nodes", []))
    for node in new_nodes:
        if node["id"] not in existing_node_ids:
            merged_nodes.append(node)

    # Add new edges (skip duplicates)
    merged_edges = list(existing_graph.get("edges", []))
    for edge in new_edges:
        edge_key = (edge["source"], edge["target"], edge["relation_type"])
        if edge_key not in existing_edge_keys:
            merged_edges.append(edge)

    return {
        "nodes": merged_nodes,
        "edges": merged_edges,
        "node_count": len(merged_nodes),
        "edge_count": len(merged_edges),
    }

"""
Relation extraction and classification.

Combines logic from:
- experiments/03_4_extract_relations.py
- experiments/05_1_block_candidate_pairs.py
- experiments/05_2_classify_structural.py
- experiments/05_3_classify_causal.py

For production pipeline with incremental support.
"""

from typing import Callable

import numpy as np
from loguru import logger

from core.models import get_llm_client

# =============================================================================
# CONFIGURATION
# =============================================================================

# Blocking thresholds
FAISS_TOP_K = 50
SIMILARITY_THRESHOLD = 0.5

# Bidirectional classification thresholds
BIDIRECTIONAL_CORRELATION_THRESHOLD = 0.6  # If both directions > this, it's correlation
BIDIRECTIONAL_DIRECTION_MARGIN = 0.2  # Winner must exceed loser by this margin

# Relation types
STRUCTURAL_RELATIONS = [
    "TIMEFRAME_VARIANT",
    "THRESHOLD_VARIANT",
    "HIERARCHICAL",
    "SERIES_MEMBER",
    "MUTUALLY_EXCLUSIVE",
]

CAUSAL_RELATIONS = [
    "DIRECT_CAUSE",
    "ENABLING_CONDITION",
    "INHIBITING_CONDITION",
    "REQUIRES",
    "CORRELATED",
    "INDEPENDENT",
]

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


def block_candidate_pairs(
    new_events: list[dict],
    all_events: list[dict],
    new_embeddings: np.ndarray,
    all_embeddings: np.ndarray,
    all_event_ids: list[str],
) -> list[dict]:
    """
    Find candidate event pairs for relation classification.

    Uses FAISS for fast approximate nearest neighbor search.
    For incremental mode, finds pairs between new events and all events.

    Args:
        new_events: Newly added events
        all_events: All events (including new)
        new_embeddings: Embeddings for new events
        all_embeddings: All embeddings
        all_event_ids: IDs corresponding to all_embeddings

    Returns:
        List of candidate pairs with similarity scores
    """
    try:
        import faiss
    except ImportError:
        logger.warning("FAISS not available, using brute force search")
        return _brute_force_pairs(
            new_events, all_embeddings, all_event_ids, new_embeddings
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

    logger.info(f"Found {len(pairs)} candidate pairs for {len(new_events)} new events")
    return pairs


def _brute_force_pairs(
    new_events: list[dict],
    all_embeddings: np.ndarray,
    all_event_ids: list[str],
    new_embeddings: np.ndarray,
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
) -> list[dict]:
    """
    Classify structural relations using rules.

    Structural relations are deterministic based on:
    - Shared entities
    - Title/question similarity patterns
    - Time/threshold patterns
    """
    classified = []

    for pair in pairs:
        event_a = events_by_id.get(pair["event_a_id"])
        event_b = events_by_id.get(pair["event_b_id"])

        if not event_a or not event_b:
            continue

        title_a = event_a.get("title", "").lower()
        title_b = event_b.get("title", "").lower()

        relation_type = None
        confidence = 0.0

        # Check for timeframe variants (e.g., "by end of 2024" vs "by end of 2025")
        if _is_timeframe_variant(title_a, title_b):
            relation_type = "TIMEFRAME_VARIANT"
            confidence = 0.9

        # Check for threshold variants (e.g., ">50%" vs ">60%")
        elif _is_threshold_variant(title_a, title_b):
            relation_type = "THRESHOLD_VARIANT"
            confidence = 0.9

        # Check for mutual exclusivity (opposite outcomes)
        elif _is_mutually_exclusive(title_a, title_b):
            relation_type = "MUTUALLY_EXCLUSIVE"
            confidence = 0.85

        if relation_type:
            classified.append(
                {
                    "source_id": pair["event_a_id"],
                    "target_id": pair["event_b_id"],
                    "relation_type": relation_type,
                    "confidence": confidence,
                    "classification_method": "structural_rules",
                }
            )

    logger.info(f"Classified {len(classified)} structural relations")
    return classified


def _is_timeframe_variant(title_a: str, title_b: str) -> bool:
    """Check if titles differ only by timeframe."""
    import re

    # Remove year/date patterns and compare
    pattern = r"\b(20\d{2}|january|february|march|april|may|june|july|august|september|october|november|december)\b"
    a_clean = re.sub(pattern, "", title_a)
    b_clean = re.sub(pattern, "", title_b)
    # Check if >80% similar after removing dates
    from rapidfuzz import fuzz

    return fuzz.ratio(a_clean, b_clean) > 80


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
) -> str:
    """Build prompt for batch LLM classification with semantic info and descriptions."""
    prompt_parts = ["Classify the causal relationships for these event pairs:\n"]

    for i, pair in enumerate(pairs):
        event_a = events_by_id.get(pair["event_a_id"], {})
        event_b = events_by_id.get(pair["event_b_id"], {})
        sem_a = semantics_by_id.get(pair["event_a_id"], {})
        sem_b = semantics_by_id.get(pair["event_b_id"], {})

        prompt_parts.append(f"\n=== PAIR {i + 1} ===")
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


async def classify_causal(
    pairs: list[dict],
    events_by_id: dict[str, dict],
    semantics_by_id: dict[str, dict] | None = None,
    max_pairs: int = 500,
    batch_size: int = 3,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Classify causal relations using LLM with bidirectional verification.

    Uses bidirectional classification to prevent reversed causality errors:
    - Classifies both A→B and B→A directions for each pair
    - If both directions score high (>0.6), treats as CORRELATED (not directional)
    - If one direction clearly wins (>0.2 margin), uses that direction
    - Stores both P(B|A) and P(A|B) conditional probability estimates

    Args:
        pairs: Candidate pairs (already filtered by blocking)
        events_by_id: Event lookup dict
        semantics_by_id: Optional semantic info per event (from semantics step)
        max_pairs: Maximum pairs to classify (LLM cost control)
        batch_size: Pairs per LLM batch request
        progress_callback: Optional callback(message: str) to report progress

    Returns:
        List of classified causal relations with implied conditionals
    """
    import asyncio

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

    logger.info(
        f"Classifying {len(to_classify)} pairs bidirectionally with LLM "
        f"(batch size {batch_size}, 2x calls per batch)..."
    )

    classified = []
    total_batches = (len(to_classify) + batch_size - 1) // batch_size
    direction_corrections = 0
    correlation_conversions = 0

    for batch_idx in range(0, len(to_classify), batch_size):
        batch = to_classify[batch_idx : batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1

        # Report progress every 5 batches
        if batch_num % 5 == 0 or batch_num == 1:
            progress_msg = (
                f"Bidirectional batch {batch_num}/{total_batches} "
                f"({len(classified)} relations, {direction_corrections} dir corrections, "
                f"{correlation_conversions} → CORRELATED)"
            )
            logger.debug(progress_msg)
            if progress_callback:
                progress_callback(progress_msg)

        try:
            # Create forward and reverse batches
            forward_batch = batch
            reverse_batch = [_create_reverse_pair(p) for p in batch]

            # Build prompts for both directions
            forward_prompt = _build_llm_batch_prompt(
                forward_batch, events_by_id, semantics_by_id
            )
            reverse_prompt = _build_llm_batch_prompt(
                reverse_batch, events_by_id, semantics_by_id
            )

            # Execute both LLM calls concurrently for efficiency
            forward_messages = [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": forward_prompt},
            ]
            reverse_messages = [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": reverse_prompt},
            ]

            forward_response, reverse_response = await asyncio.gather(
                llm.complete(forward_messages, temperature=0.1),
                llm.complete(reverse_messages, temperature=0.1),
                return_exceptions=True,
            )

            # Handle potential exceptions from gather
            forward_str: str = ""
            reverse_str: str = ""
            if isinstance(forward_response, Exception):
                logger.warning(f"Forward LLM call failed: {forward_response}")
            else:
                forward_str = str(forward_response)
            if isinstance(reverse_response, Exception):
                logger.warning(f"Reverse LLM call failed: {reverse_response}")
            else:
                reverse_str = str(reverse_response)

            # Parse both responses
            forward_parsed = _parse_llm_batch_response(forward_str, len(batch))
            reverse_parsed = _parse_llm_batch_response(reverse_str, len(batch))

            # Resolve each pair using bidirectional logic
            for i, pair in enumerate(batch):
                forward_result = forward_parsed[i] if i < len(forward_parsed) else None
                reverse_result = reverse_parsed[i] if i < len(reverse_parsed) else None

                # Validate relation types
                if forward_result:
                    rel_type = forward_result.get("relation_type", "INDEPENDENT")
                    if rel_type not in CAUSAL_RELATIONS:
                        forward_result["relation_type"] = "INDEPENDENT"

                if reverse_result:
                    rel_type = reverse_result.get("relation_type", "INDEPENDENT")
                    if rel_type not in CAUSAL_RELATIONS:
                        reverse_result["relation_type"] = "INDEPENDENT"

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

        except Exception as e:
            logger.warning(f"Bidirectional batch classification failed: {e}")
            continue

    logger.info(
        f"Classified {len(classified)} causal relations with bidirectional check: "
        f"{direction_corrections} direction corrections, "
        f"{correlation_conversions} converted to CORRELATED"
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

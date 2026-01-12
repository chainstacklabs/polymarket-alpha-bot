"""
Define world states for cross-event clusters using LLM reasoning.

World states are mutually exclusive scenarios that determine how cluster events resolve.
This step uses LLM to enumerate 4-6 possible futures for each cluster, with probabilities.

Why LLM is required:
- World states require understanding of real-world causality
- "What are the possible outcomes of the Russia-Ukraine war?" requires reasoning
- No traditional ML model can enumerate domain-specific scenarios

Input:
- data/07_1_build_clusters/<latest>/clusters.json

Output:
- data/07_2_define_world_states/<timestamp>/world_states.json
- data/07_2_define_world_states/<timestamp>/summary.json

Pipeline: 07_1_build_clusters -> [07_2_define_world_states] -> 07_3_build_resolution_matrix
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# =============================================================================
# CONFIGURATION
# =============================================================================

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

# Input directory
INPUT_CLUSTERS_DIR = DATA_DIR / "07_1_build_clusters"
INPUT_RUN_FOLDER: str | None = None  # None = use latest

# Output directory
SCRIPT_OUTPUT_DIR = DATA_DIR / "07_2_define_world_states"

# OpenRouter API settings
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_MODEL = "xiaomi/mimo-v2-flash:free"

# LLM settings
MAX_RETRIES = 3
RETRY_DELAY = 2.0
REQUEST_TIMEOUT = 120.0
REQUEST_DELAY = 1.0  # Delay between requests (free tier rate limiting)

# World state constraints
MIN_WORLD_STATES = 4
MAX_WORLD_STATES = 8  # Increased to accommodate ensemble + adversarial
PROBABILITY_SUM_TOLERANCE = 0.15  # Allow 0.85-1.15 sum

# =============================================================================
# ENSEMBLE CONFIGURATION
# =============================================================================
# Call LLM multiple times and merge results for robustness
ENSEMBLE_ENABLED = True
ENSEMBLE_CALLS = 3  # Number of LLM calls per cluster
ENSEMBLE_MIN_AGREEMENT = 2  # State must appear in N runs to be included
ENSEMBLE_TEMPERATURE_VARIANCE = [0.2, 0.4, 0.6]  # Different temps for diversity

# =============================================================================
# EXHAUSTIVENESS ANALYSIS
# =============================================================================
# Check if world states cover all logical outcome combinations
EXHAUSTIVENESS_ENABLED = True
MIN_EXHAUSTIVENESS_SCORE = 0.7  # Warn if <70% of combinations covered

# =============================================================================
# ADVERSARIAL STATE INJECTION
# =============================================================================
# Add stress-test states that the LLM might miss
ADVERSARIAL_STATES_ENABLED = True
ADVERSARIAL_STATE_PROBABILITY = 0.02  # 2% probability for each adversarial state

# Logging
LOG_LEVEL = logging.INFO

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA STRUCTURES
# =============================================================================


@dataclass
class WorldState:
    """A possible future scenario for a cluster."""

    id: str  # "S1", "S2", ...
    name: str  # "Russia military victory"
    description: str  # Detailed scenario
    probability: float  # 0.0-1.0
    key_drivers: list[str] = field(default_factory=list)  # What causes this

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "probability": round(self.probability, 3),
            "key_drivers": self.key_drivers,
        }


@dataclass
class ClusterWorldStates:
    """World states for a single cluster."""

    cluster_id: str
    cluster_theme: str
    world_states: list[WorldState]
    completeness_confidence: float  # LLM's self-reported confidence
    reasoning: str  # Why these states are exhaustive
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    # New robustness metrics
    exhaustiveness_score: float = 0.0  # % of outcome combos covered
    exhaustiveness_details: dict = field(default_factory=dict)
    ensemble_agreement: dict = field(
        default_factory=dict
    )  # State agreement across runs
    adversarial_states_added: int = 0

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "cluster_theme": self.cluster_theme,
            "world_states": [s.to_dict() for s in self.world_states],
            "state_count": len(self.world_states),
            "probability_sum": sum(s.probability for s in self.world_states),
            "completeness_confidence": round(self.completeness_confidence, 3),
            "reasoning": self.reasoning,
            "validation": {
                "errors": self.validation_errors,
                "warnings": self.validation_warnings,
                "is_valid": len(self.validation_errors) == 0,
            },
            "robustness": {
                "exhaustiveness_score": round(self.exhaustiveness_score, 3),
                "exhaustiveness_details": self.exhaustiveness_details,
                "ensemble_agreement": self.ensemble_agreement,
                "adversarial_states_added": self.adversarial_states_added,
            },
        }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def find_latest_run_folder(script_dir: Path) -> Path | None:
    """Find the most recent run folder."""
    if not script_dir.exists():
        return None
    run_folders = [f for f in script_dir.iterdir() if f.is_dir()]
    if not run_folders:
        return None
    return max(run_folders, key=lambda f: f.stat().st_mtime)


def extract_json_from_response(response: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks."""
    # Try to find JSON in code blocks first
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to parse the whole response as JSON
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object pattern
    json_match = re.search(r"\{[\s\S]*\}", response)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    return None


import itertools
from collections import Counter


# =============================================================================
# EXHAUSTIVENESS ANALYSIS
# =============================================================================


def generate_all_outcome_combinations(n_events: int) -> list[tuple[str, ...]]:
    """
    Generate all possible YES/NO combinations for n events.

    For 4 events: 2^4 = 16 combinations
    """
    return list(itertools.product(["YES", "NO"], repeat=n_events))


def analyze_exhaustiveness(
    world_states: list[WorldState],
    events: list[dict],
    resolution_matrix: dict[str, dict[str, str]] | None = None,
) -> dict:
    """
    Analyze how exhaustively world states cover all outcome combinations.

    Returns dict with:
    - score: 0.0-1.0 (% of combinations covered)
    - total_combinations: 2^n
    - covered_combinations: list of covered combos
    - missing_combinations: list of uncovered combos
    """
    n_events = len(events)
    event_ids = [str(e.get("id", "")) for e in events]

    if n_events == 0:
        return {"score": 1.0, "total_combinations": 0, "covered": [], "missing": []}

    all_combos = generate_all_outcome_combinations(n_events)
    total = len(all_combos)  # 2^n

    # If we have a resolution matrix, use it to determine which combos are covered
    # Otherwise, we can only estimate based on state descriptions
    covered_combos = set()

    if resolution_matrix:
        for state in world_states:
            state_resolutions = resolution_matrix.get(state.id, {})
            if state_resolutions:
                # Build the outcome tuple for this state
                combo = tuple(state_resolutions.get(eid, "UNC") for eid in event_ids)
                # Only count if no UNC (uncertain) values
                if "UNC" not in combo:
                    covered_combos.add(combo)
    else:
        # Without resolution matrix, estimate based on number of states
        # Each state typically covers 1-2 combinations
        estimated_coverage = min(len(world_states) * 1.5, total)
        return {
            "score": estimated_coverage / total,
            "total_combinations": total,
            "covered_count": int(estimated_coverage),
            "missing_count": total - int(estimated_coverage),
            "note": "Estimated (no resolution matrix available)",
        }

    missing_combos = [c for c in all_combos if c not in covered_combos]

    return {
        "score": len(covered_combos) / total if total > 0 else 1.0,
        "total_combinations": total,
        "covered_count": len(covered_combos),
        "missing_count": len(missing_combos),
        "covered_combinations": [
            dict(zip(event_ids, combo)) for combo in list(covered_combos)[:5]
        ],  # First 5
        "missing_combinations": [
            dict(zip(event_ids, combo)) for combo in missing_combos[:5]
        ],  # First 5
    }


# =============================================================================
# ADVERSARIAL STATE INJECTION
# =============================================================================


def generate_adversarial_states(
    events: list[dict],
    existing_states: list[WorldState],
) -> list[WorldState]:
    """
    Generate adversarial "model breaker" states that stress-test the portfolio.

    These are edge cases the LLM might miss:
    1. All events resolve YES
    2. All events resolve NO
    3. Alternating pattern (odd YES, even NO)
    4. Single event differs from majority
    """
    adversarial = []
    n_events = len(events)
    existing_names = {s.name.lower() for s in existing_states}

    if n_events < 2:
        return []

    # State: All YES
    if "all yes" not in " ".join(existing_names):
        adversarial.append(
            WorldState(
                id="ADV_ALL_YES",
                name="All Events Resolve YES",
                description=f"Adversarial: All {n_events} events in the cluster resolve to YES simultaneously.",
                probability=ADVERSARIAL_STATE_PROBABILITY,
                key_drivers=["stress_test", "correlated_positive_shock"],
            )
        )

    # State: All NO
    if "all no" not in " ".join(existing_names):
        adversarial.append(
            WorldState(
                id="ADV_ALL_NO",
                name="All Events Resolve NO",
                description=f"Adversarial: All {n_events} events in the cluster resolve to NO simultaneously.",
                probability=ADVERSARIAL_STATE_PROBABILITY,
                key_drivers=["stress_test", "correlated_negative_shock"],
            )
        )

    # State: Only first event YES, rest NO (and vice versa)
    if n_events >= 3:
        adversarial.append(
            WorldState(
                id="ADV_SINGLE_YES",
                name="Single Outlier YES",
                description="Adversarial: Only one event resolves YES while all others resolve NO.",
                probability=ADVERSARIAL_STATE_PROBABILITY,
                key_drivers=["stress_test", "uncorrelated_outcome"],
            )
        )

    return adversarial


# =============================================================================
# ENSEMBLE MERGING
# =============================================================================


def normalize_state_name(name: str) -> str:
    """Normalize state name for comparison across runs."""
    return name.lower().strip()


def compute_state_similarity(state1: WorldState, state2: WorldState) -> float:
    """
    Compute similarity between two world states.

    Uses name similarity and key driver overlap.
    """
    # Simple name-based matching for now
    name1 = normalize_state_name(state1.name)
    name2 = normalize_state_name(state2.name)

    # Exact match
    if name1 == name2:
        return 1.0

    # Word overlap
    words1 = set(name1.split())
    words2 = set(name2.split())
    if words1 and words2:
        overlap = len(words1 & words2) / max(len(words1), len(words2))
        if overlap > 0.5:
            return overlap

    # Key driver overlap
    drivers1 = set(d.lower() for d in state1.key_drivers)
    drivers2 = set(d.lower() for d in state2.key_drivers)
    if drivers1 and drivers2:
        driver_overlap = len(drivers1 & drivers2) / max(len(drivers1), len(drivers2))
        if driver_overlap > 0.3:
            return 0.5 + driver_overlap * 0.5

    return 0.0


def merge_ensemble_states(
    all_runs: list[list[WorldState]],
    min_agreement: int = ENSEMBLE_MIN_AGREEMENT,
    max_states: int = MAX_WORLD_STATES - 3,  # Leave room for adversarial
) -> tuple[list[WorldState], dict]:
    """
    Merge world states from multiple LLM runs.

    Strategy:
    1. Group similar states across runs
    2. Keep states that appear in >= min_agreement runs
    3. Average probabilities for merged states
    4. Only add low-agreement states if needed to reach MIN_WORLD_STATES

    Returns (merged_states, agreement_stats)
    """
    if not all_runs:
        return [], {}

    if len(all_runs) == 1:
        return all_runs[0], {"single_run": True}

    # Flatten all states with run index
    all_states_with_run = []
    for run_idx, states in enumerate(all_runs):
        for state in states:
            all_states_with_run.append((state, run_idx))

    # Group similar states
    state_groups: list[list[tuple[WorldState, int]]] = []
    used = set()

    for i, (state1, run1) in enumerate(all_states_with_run):
        if i in used:
            continue

        group = [(state1, run1)]
        used.add(i)

        for j, (state2, run2) in enumerate(all_states_with_run):
            if j in used or run1 == run2:  # Don't match within same run
                continue

            similarity = compute_state_similarity(state1, state2)
            if similarity >= 0.5:
                group.append((state2, run2))
                used.add(j)

        state_groups.append(group)

    # Build merged states
    merged_states = []
    agreement_stats = {
        "total_unique_concepts": len(state_groups),
        "high_agreement": 0,  # >= min_agreement
        "low_agreement": 0,  # < min_agreement
        "state_agreements": {},
    }

    for group in state_groups:
        runs_in_group = set(run_idx for _, run_idx in group)
        agreement_count = len(runs_in_group)

        # Use first state as template, average probability
        template = group[0][0]
        avg_probability = sum(s.probability for s, _ in group) / len(group)

        # Collect all key drivers
        all_drivers = []
        for state, _ in group:
            all_drivers.extend(state.key_drivers)
        driver_counts = Counter(all_drivers)
        top_drivers = [d for d, _ in driver_counts.most_common(5)]

        merged_state = WorldState(
            id=template.id,
            name=template.name,
            description=template.description,
            probability=avg_probability,
            key_drivers=top_drivers,
        )

        # Track agreement
        agreement_stats["state_agreements"][template.name] = {
            "agreement_count": agreement_count,
            "total_runs": len(all_runs),
            "probabilities": [s.probability for s, _ in group],
        }

        if agreement_count >= min_agreement:
            agreement_stats["high_agreement"] += 1
            merged_states.append(merged_state)
        else:
            agreement_stats["low_agreement"] += 1
            # Store for potential inclusion if we need more states
            agreement_stats.setdefault("low_agreement_candidates", []).append(
                merged_state
            )

    # If we have fewer than MIN_WORLD_STATES, add some low-agreement states
    low_candidates = agreement_stats.pop("low_agreement_candidates", [])
    if len(merged_states) < MIN_WORLD_STATES and low_candidates:
        # Sort by probability (higher = more important)
        low_candidates.sort(key=lambda s: s.probability, reverse=True)
        needed = MIN_WORLD_STATES - len(merged_states)
        for state in low_candidates[:needed]:
            state.probability *= 0.5  # Reduce probability for low-agreement
            merged_states.append(state)
            agreement_stats["low_agreement_added"] = (
                agreement_stats.get("low_agreement_added", 0) + 1
            )

    # Limit to max_states (keep highest probability)
    if len(merged_states) > max_states:
        merged_states.sort(key=lambda s: s.probability, reverse=True)
        merged_states = merged_states[:max_states]
        agreement_stats["states_trimmed"] = True

    # Re-normalize probabilities
    total_prob = sum(s.probability for s in merged_states)
    if total_prob > 0:
        for state in merged_states:
            state.probability = state.probability / total_prob

    return merged_states, agreement_stats


def validate_world_states(states: list[WorldState]) -> tuple[list[str], list[str]]:
    """
    Validate world states without LLM.

    Returns (errors, warnings)
    """
    errors = []
    warnings = []

    # Check count
    if len(states) < MIN_WORLD_STATES:
        errors.append(f"Too few states: {len(states)} < {MIN_WORLD_STATES}")
    elif len(states) > MAX_WORLD_STATES:
        errors.append(f"Too many states: {len(states)} > {MAX_WORLD_STATES}")

    # Check probability sum
    prob_sum = sum(s.probability for s in states)
    if not (1 - PROBABILITY_SUM_TOLERANCE <= prob_sum <= 1 + PROBABILITY_SUM_TOLERANCE):
        errors.append(f"Probabilities sum to {prob_sum:.3f}, expected ~1.0")

    # Check for duplicate names
    names = [s.name.lower() for s in states]
    if len(names) != len(set(names)):
        errors.append("Duplicate state names detected")

    # Check individual probabilities
    for state in states:
        if state.probability <= 0:
            errors.append(
                f"State {state.id} has non-positive probability: {state.probability}"
            )
        elif state.probability < 0.05:
            warnings.append(
                f"State {state.id} has very low probability: {state.probability}"
            )
        elif state.probability > 0.5:
            warnings.append(
                f"State {state.id} has high probability: {state.probability} - is it too broad?"
            )

    # Check for empty descriptions
    for state in states:
        if not state.description or len(state.description) < 20:
            warnings.append(f"State {state.id} has short/missing description")

    return errors, warnings


# =============================================================================
# LLM CLIENT
# =============================================================================


class LLMClient:
    """Client for OpenRouter API."""

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.client = httpx.Client(timeout=REQUEST_TIMEOUT)
        self.total_tokens = 0
        self.request_count = 0

    def close(self) -> None:
        self.client.close()

    def complete(
        self, prompt: str, system: str | None = None, temperature: float = 0.3
    ) -> str:
        """Send completion request to LLM."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                    },
                )
                response.raise_for_status()
                data = response.json()

                if "usage" in data:
                    self.total_tokens += data["usage"].get("total_tokens", 0)
                self.request_count += 1

                return data["choices"][0]["message"]["content"]

            except httpx.HTTPStatusError as e:
                logger.warning(f"HTTP error (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise
            except Exception as e:
                logger.warning(f"Error (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise

        return ""


# =============================================================================
# LLM PROMPTS
# =============================================================================

WORLD_STATES_SYSTEM_PROMPT = """You are an expert analyst specializing in scenario planning and prediction markets.

Your task is to define WORLD STATES - mutually exclusive future scenarios that determine how a cluster of related prediction market events will resolve.

Guidelines:
- Think about real-world causality and how events are connected
- States must be MUTUALLY EXCLUSIVE (only one can happen)
- States should be COLLECTIVELY EXHAUSTIVE (cover >90% of probability mass)
- Each state should clearly determine how multiple events in the cluster resolve
- Probabilities should be calibrated against current market prices and world knowledge
- Avoid vague catch-all states like "something else happens"
"""

WORLD_STATES_PROMPT_TEMPLATE = """You are analyzing a cluster of prediction market events that share an underlying driver.

CLUSTER INFORMATION:
- Cluster ID: {cluster_id}
- Domain: {domain}
- Shared entities: {shared_entities}
- Event count: {event_count}

EVENTS IN CLUSTER:
{events_formatted}

YOUR TASK:
Define {min_states}-{max_states} WORLD STATES (possible future scenarios) for this cluster.

REQUIREMENTS:
1. MUTUALLY EXCLUSIVE: Only one state can occur
2. COLLECTIVELY EXHAUSTIVE: States should cover >90% of probability mass
3. ACTIONABLE: Each state must clearly determine how multiple events resolve
4. SPECIFIC: Avoid vague states like "something else happens"
5. CALIBRATED: Probabilities should reflect current market prices and world knowledge

OUTPUT FORMAT (JSON only, no markdown):
{{
  "world_states": [
    {{
      "id": "S1",
      "name": "Short name (3-5 words)",
      "description": "What happens in this scenario (1-2 sentences)",
      "probability": 0.XX,
      "key_drivers": ["what causes this state"]
    }}
  ],
  "completeness_confidence": 0.XX,
  "reasoning": "Brief explanation of why these states are exhaustive"
}}

Remember: Output ONLY valid JSON, no markdown code blocks or extra text."""


def format_cluster_for_prompt(cluster: dict) -> str:
    """Format a cluster's events for the LLM prompt."""
    events_formatted = []
    for i, event in enumerate(cluster.get("events", []), 1):
        events_formatted.append(
            f"{i}. [{event.get('id')}] {event.get('title')}\n"
            f"   Description: {event.get('description', 'N/A')[:300]}..."
        )
    return "\n\n".join(events_formatted)


def parse_llm_world_states(response: str) -> list[WorldState] | None:
    """Parse LLM response into WorldState objects."""
    parsed = extract_json_from_response(response)
    if not parsed:
        return None

    raw_states = parsed.get("world_states", [])
    if not raw_states:
        return None

    world_states = []
    for i, raw_state in enumerate(raw_states):
        state = WorldState(
            id=raw_state.get("id", f"S{i + 1}"),
            name=raw_state.get("name", f"State {i + 1}"),
            description=raw_state.get("description", ""),
            probability=float(raw_state.get("probability", 1.0 / len(raw_states))),
            key_drivers=raw_state.get("key_drivers", []),
        )
        world_states.append(state)

    return world_states


def generate_world_states(
    llm_client: LLMClient,
    cluster: dict,
) -> ClusterWorldStates | None:
    """
    Generate world states for a single cluster using LLM.

    With ensemble mode enabled, calls LLM multiple times and merges results.
    Also adds adversarial states and computes exhaustiveness.
    """
    cluster_id = cluster.get("cluster_id", "unknown")
    events = cluster.get("events", [])

    # Format prompt
    prompt = WORLD_STATES_PROMPT_TEMPLATE.format(
        cluster_id=cluster_id,
        domain=cluster.get("domain", "general"),
        shared_entities=", ".join(cluster.get("shared_entities", [])[:10]),
        event_count=cluster.get("event_count", len(events)),
        events_formatted=format_cluster_for_prompt(cluster),
        min_states=MIN_WORLD_STATES,
        max_states=MAX_WORLD_STATES,
    )

    # =========================================================================
    # ENSEMBLE: Call LLM multiple times with different temperatures
    # =========================================================================

    all_runs: list[list[WorldState]] = []
    ensemble_agreement = {}

    if ENSEMBLE_ENABLED and ENSEMBLE_CALLS > 1:
        logger.info(
            f"Generating world states for {cluster_id} (ensemble mode: {ENSEMBLE_CALLS} runs)..."
        )

        for run_idx in range(ENSEMBLE_CALLS):
            temp = ENSEMBLE_TEMPERATURE_VARIANCE[
                run_idx % len(ENSEMBLE_TEMPERATURE_VARIANCE)
            ]
            logger.info(f"  Run {run_idx + 1}/{ENSEMBLE_CALLS} (temp={temp})...")

            response = llm_client.complete(
                prompt, system=WORLD_STATES_SYSTEM_PROMPT, temperature=temp
            )
            states = parse_llm_world_states(response)

            if states:
                all_runs.append(states)
                logger.info(f"    Got {len(states)} states")
            else:
                logger.warning(f"    Failed to parse run {run_idx + 1}")

            # Rate limiting between runs
            if run_idx < ENSEMBLE_CALLS - 1:
                time.sleep(REQUEST_DELAY)

        if not all_runs:
            logger.error(f"All ensemble runs failed for {cluster_id}")
            return None

        # Merge ensemble results
        world_states, ensemble_agreement = merge_ensemble_states(
            all_runs, ENSEMBLE_MIN_AGREEMENT
        )
        logger.info(
            f"  Ensemble merged: {ensemble_agreement.get('high_agreement', 0)} high-agreement, "
            f"{ensemble_agreement.get('low_agreement', 0)} low-agreement states"
        )
        completeness_confidence = 0.7 + 0.1 * ensemble_agreement.get(
            "high_agreement", 0
        ) / max(len(world_states), 1)

    else:
        # Single LLM call (non-ensemble mode)
        logger.info(f"Generating world states for {cluster_id}...")
        response = llm_client.complete(prompt, system=WORLD_STATES_SYSTEM_PROMPT)

        world_states = parse_llm_world_states(response)
        if not world_states:
            logger.error(f"Failed to parse LLM response for {cluster_id}")
            return None

        completeness_confidence = 0.7

    # =========================================================================
    # ADVERSARIAL STATES: Add stress-test scenarios
    # =========================================================================

    adversarial_added = 0
    if ADVERSARIAL_STATES_ENABLED:
        adversarial_states = generate_adversarial_states(events, world_states)
        if adversarial_states:
            world_states.extend(adversarial_states)
            adversarial_added = len(adversarial_states)
            logger.info(f"  Added {adversarial_added} adversarial states")

            # Re-normalize probabilities after adding adversarial states
            total_prob = sum(s.probability for s in world_states)
            if total_prob > 0:
                for state in world_states:
                    state.probability = state.probability / total_prob

    # =========================================================================
    # EXHAUSTIVENESS: Estimate coverage of outcome combinations
    # =========================================================================

    exhaustiveness_details = {}
    exhaustiveness_score = 0.0

    if EXHAUSTIVENESS_ENABLED:
        exhaustiveness_details = analyze_exhaustiveness(
            world_states,
            events,
            resolution_matrix=None,  # No matrix yet at this stage
        )
        exhaustiveness_score = exhaustiveness_details.get("score", 0.0)

        if exhaustiveness_score < MIN_EXHAUSTIVENESS_SCORE:
            logger.warning(
                f"  Low exhaustiveness: {exhaustiveness_score:.1%} "
                f"({exhaustiveness_details.get('covered_count', 0)}/{exhaustiveness_details.get('total_combinations', 0)} combos)"
            )

    # =========================================================================
    # VALIDATION
    # =========================================================================

    errors, warnings = validate_world_states(world_states)

    # Build result
    result = ClusterWorldStates(
        cluster_id=cluster_id,
        cluster_theme=f"{cluster.get('domain', 'general')}: {', '.join(cluster.get('shared_entities', [])[:3])}",
        world_states=world_states,
        completeness_confidence=min(completeness_confidence, 0.95),
        reasoning=f"Ensemble of {len(all_runs)} runs"
        if all_runs
        else "Single LLM call",
        validation_errors=errors,
        validation_warnings=warnings,
        exhaustiveness_score=exhaustiveness_score,
        exhaustiveness_details=exhaustiveness_details,
        ensemble_agreement=ensemble_agreement,
        adversarial_states_added=adversarial_added,
    )

    return result


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("Starting 07_2_define_world_states")

    # Check API key
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY environment variable is required")

    # =========================================================================
    # Load clusters
    # =========================================================================

    if INPUT_RUN_FOLDER:
        clusters_folder = INPUT_CLUSTERS_DIR / INPUT_RUN_FOLDER
    else:
        clusters_folder = find_latest_run_folder(INPUT_CLUSTERS_DIR)

    if not clusters_folder or not clusters_folder.exists():
        raise FileNotFoundError(f"Clusters folder not found: {clusters_folder}")

    clusters_file = clusters_folder / "clusters.json"
    if not clusters_file.exists():
        raise FileNotFoundError(f"Clusters file not found: {clusters_file}")

    with open(clusters_file, encoding="utf-8") as f:
        clusters_data = json.load(f)

    clusters = clusters_data.get("clusters", [])
    logger.info(f"Loaded {len(clusters)} clusters from {clusters_folder}")

    if not clusters:
        logger.warning("No clusters found, nothing to process")
        return

    # =========================================================================
    # Initialize LLM client
    # =========================================================================

    llm_client = LLMClient(OPENROUTER_API_KEY, LLM_MODEL)
    logger.info(f"LLM client initialized: {LLM_MODEL}")

    # =========================================================================
    # Generate world states for each cluster
    # =========================================================================

    all_world_states: list[ClusterWorldStates] = []
    failed_clusters: list[str] = []

    for i, cluster in enumerate(clusters):
        cluster_id = cluster.get("cluster_id", f"cluster_{i}")
        logger.info(f"Processing cluster {i + 1}/{len(clusters)}: {cluster_id}")

        try:
            result = generate_world_states(llm_client, cluster)
            if result:
                all_world_states.append(result)
                if result.validation_errors:
                    logger.warning(f"  Validation errors: {result.validation_errors}")
                if result.validation_warnings:
                    logger.info(f"  Validation warnings: {result.validation_warnings}")
                logger.info(f"  Generated {len(result.world_states)} world states")
            else:
                failed_clusters.append(cluster_id)
                logger.error("  Failed to generate world states")

        except Exception as e:
            logger.error(f"  Error processing {cluster_id}: {e}")
            failed_clusters.append(cluster_id)

        # Rate limiting
        if i < len(clusters) - 1:
            time.sleep(REQUEST_DELAY)

    # =========================================================================
    # Save outputs
    # =========================================================================

    llm_client.close()

    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    output_folder = SCRIPT_OUTPUT_DIR / timestamp
    output_folder.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output folder: {output_folder}")

    # Save world_states.json
    world_states_output = {
        "_meta": {
            "description": "World states for cross-event clusters",
            "created_at": start_time.isoformat(),
            "llm_model": LLM_MODEL,
            "source_clusters": str(clusters_folder),
            "total_clusters": len(clusters),
            "successful_clusters": len(all_world_states),
            "failed_clusters": len(failed_clusters),
        },
        "cluster_world_states": [cws.to_dict() for cws in all_world_states],
    }

    with open(output_folder / "world_states.json", "w", encoding="utf-8") as f:
        json.dump(world_states_output, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved world states for {len(all_world_states)} clusters")

    # Save summary.json
    end_time = datetime.now(timezone.utc)

    valid_count = sum(1 for cws in all_world_states if not cws.validation_errors)
    total_states = sum(len(cws.world_states) for cws in all_world_states)
    avg_states = total_states / len(all_world_states) if all_world_states else 0

    summary = {
        "run_info": {
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat(),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "output_folder": str(output_folder),
        },
        "llm_stats": {
            "model": LLM_MODEL,
            "total_requests": llm_client.request_count,
            "total_tokens": llm_client.total_tokens,
        },
        "results": {
            "total_clusters": len(clusters),
            "successful_clusters": len(all_world_states),
            "valid_clusters": valid_count,
            "failed_clusters": failed_clusters,
            "total_world_states": total_states,
            "avg_states_per_cluster": round(avg_states, 2),
        },
        "validation_summary": {
            "clusters_with_errors": sum(
                1 for cws in all_world_states if cws.validation_errors
            ),
            "clusters_with_warnings": sum(
                1 for cws in all_world_states if cws.validation_warnings
            ),
        },
        "robustness_summary": {
            "ensemble_enabled": ENSEMBLE_ENABLED,
            "ensemble_calls": ENSEMBLE_CALLS if ENSEMBLE_ENABLED else 1,
            "adversarial_states_enabled": ADVERSARIAL_STATES_ENABLED,
            "total_adversarial_added": sum(
                cws.adversarial_states_added for cws in all_world_states
            ),
            "avg_exhaustiveness_score": (
                sum(cws.exhaustiveness_score for cws in all_world_states)
                / len(all_world_states)
                if all_world_states
                else 0
            ),
            "high_agreement_states": sum(
                cws.ensemble_agreement.get("high_agreement", 0)
                for cws in all_world_states
            ),
            "low_agreement_states": sum(
                cws.ensemble_agreement.get("low_agreement", 0)
                for cws in all_world_states
            ),
        },
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Saved summary.json")

    # Log final summary
    logger.info("=" * 60)
    logger.info("WORLD STATE GENERATION COMPLETE")
    logger.info(f"  Successful: {len(all_world_states)}/{len(clusters)} clusters")
    logger.info(f"  Valid: {valid_count}/{len(all_world_states)}")
    logger.info(f"  Total states: {total_states}")
    logger.info(f"  LLM requests: {llm_client.request_count}")
    logger.info(f"  Duration: {summary['run_info']['duration_seconds']:.2f}s")
    logger.info("-" * 60)
    logger.info("ROBUSTNESS METRICS:")
    logger.info(
        f"  Ensemble mode: {'ON' if ENSEMBLE_ENABLED else 'OFF'} ({ENSEMBLE_CALLS} calls)"
    )
    logger.info(
        f"  High-agreement states: {summary['robustness_summary']['high_agreement_states']}"
    )
    logger.info(
        f"  Low-agreement states: {summary['robustness_summary']['low_agreement_states']}"
    )
    logger.info(
        f"  Adversarial states added: {summary['robustness_summary']['total_adversarial_added']}"
    )
    logger.info(
        f"  Avg exhaustiveness: {summary['robustness_summary']['avg_exhaustiveness_score']:.1%}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

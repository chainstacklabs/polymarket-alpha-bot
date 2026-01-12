"""
Build resolution matrices using LLM causal reasoning.

For each cluster, predict how each event resolves (YES/NO/UNC) under each world state.
This is the core of the coverage model - it maps scenarios to event outcomes.

Why LLM is required:
- Resolution requires causal reasoning: "If Russia wins, does 'Ceasefire by 2026' resolve YES?"
- This is not semantic similarity — "ceasefire" and "Russia victory" aren't similar but are causally linked

Input:
- data/07_1_build_clusters/<latest>/clusters.json
- data/07_2_define_world_states/<latest>/world_states.json

Output:
- data/07_3_build_resolution_matrix/<timestamp>/resolution_matrices.json
- data/07_3_build_resolution_matrix/<timestamp>/summary.json

Pipeline: 07_2_define_world_states -> [07_3_build_resolution_matrix] -> 07_4_find_portfolios
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

# Input directories
INPUT_CLUSTERS_DIR = DATA_DIR / "07_1_build_clusters"
INPUT_WORLD_STATES_DIR = DATA_DIR / "07_2_define_world_states"
INPUT_RUN_FOLDER: str | None = None  # None = use latest

# Output directory
SCRIPT_OUTPUT_DIR = DATA_DIR / "07_3_build_resolution_matrix"

# OpenRouter API settings
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_MODEL = "xiaomi/mimo-v2-flash:free"

# LLM settings
MAX_RETRIES = 3
RETRY_DELAY = 2.0
REQUEST_TIMEOUT = 120.0
REQUEST_DELAY = 1.0  # Delay between requests

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
class ResolutionMatrix:
    """Resolution predictions for a cluster's events under each world state."""

    cluster_id: str
    matrix: dict[str, dict[str, str]]  # state_id → event_id → YES/NO/UNC
    confidence: float  # LLM's overall confidence
    reasoning: dict[str, dict[str, str]]  # state_id → event_id → explanation
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "matrix": self.matrix,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "validation": {
                "errors": self.validation_errors,
                "warnings": self.validation_warnings,
                "is_valid": len(self.validation_errors) == 0,
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


def validate_resolution_matrix(
    matrix: dict[str, dict[str, str]],
    state_ids: list[str],
    event_ids: list[str],
) -> tuple[list[str], list[str]]:
    """
    Validate resolution matrix without LLM.

    Returns (errors, warnings)
    """
    errors = []
    warnings = []

    # Check all states are present
    for state_id in state_ids:
        if state_id not in matrix:
            errors.append(f"Missing state {state_id} in matrix")

    # Check all events are present in each state
    for state_id, resolutions in matrix.items():
        for event_id in event_ids:
            if event_id not in resolutions:
                warnings.append(f"Missing event {event_id} in state {state_id}")
            else:
                value = resolutions[event_id]
                if value not in ["YES", "NO", "UNC"]:
                    errors.append(
                        f"Invalid resolution '{value}' for {state_id}/{event_id}"
                    )

    # Check for suspicious patterns
    for event_id in event_ids:
        resolutions = [matrix.get(s, {}).get(event_id) for s in state_ids]
        resolutions = [r for r in resolutions if r]

        # All YES → event should be ~100% market
        if resolutions and all(r == "YES" for r in resolutions):
            warnings.append(
                f"Event {event_id} resolves YES in all states - check if market is ~100%"
            )

        # All NO → event should be ~0% market
        if resolutions and all(r == "NO" for r in resolutions):
            warnings.append(
                f"Event {event_id} resolves NO in all states - check if market is ~0%"
            )

        # All UNC → event may not belong in cluster
        if resolutions and all(r == "UNC" for r in resolutions):
            warnings.append(
                f"Event {event_id} is UNC in all states - may not belong in cluster"
            )

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

    def complete(self, prompt: str, system: str | None = None) -> str:
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
                        "temperature": 0.1,  # Low temperature for consistent reasoning
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

RESOLUTION_SYSTEM_PROMPT = """You are an expert analyst predicting how prediction market events will resolve under different future scenarios.

Your task is to determine, for each (scenario, event) pair, whether the event resolves YES, NO, or UNC (uncertain).

Guidelines:
- YES: You are >75% confident the event resolves YES in this scenario
- NO: You are >75% confident the event resolves NO in this scenario
- UNC: The outcome is uncertain (25-75% range) even given the scenario

Consider:
- The EXACT resolution criteria of each event (deadlines, thresholds, conditions)
- Causal relationships between the scenario and the event outcome
- Some events may be largely independent of the scenario
"""

RESOLUTION_PROMPT_TEMPLATE = """You are predicting how prediction market events will resolve under different scenarios.

WORLD STATES (possible future scenarios):
{world_states_formatted}

EVENTS TO PREDICT:
{events_formatted}

YOUR TASK:
For each (state, event) pair, predict the resolution:
- YES: Event resolves YES in this state (>75% confident)
- NO: Event resolves NO in this state (>75% confident)
- UNC: Uncertain, could go either way (25-75% range)

IMPORTANT:
- Consider the EXACT resolution criteria of each event
- Pay attention to deadlines (e.g., "by end of 2026" means Dec 31, 2026)
- Some events may be independent of the world state

OUTPUT FORMAT (JSON only, no markdown):
{{
  "matrix": {{
    "{example_state}": {{
      "{example_event}": "YES",
      "another_event": "NO"
    }},
    "S2": {{...}}
  }},
  "confidence": 0.XX,
  "reasoning": {{
    "{example_state}": {{
      "{example_event}": "Brief explanation why YES/NO/UNC in this state"
    }}
  }}
}}

Remember: Output ONLY valid JSON, no markdown code blocks or extra text."""


def format_world_states_for_prompt(world_states: list[dict]) -> str:
    """Format world states for the LLM prompt."""
    formatted = []
    for state in world_states:
        formatted.append(
            f"[{state['id']}] {state['name']} (probability: {state['probability']:.0%})\n"
            f"   {state['description']}"
        )
    return "\n\n".join(formatted)


def format_events_for_prompt(events: list[dict]) -> str:
    """Format events for the LLM prompt."""
    formatted = []
    for event in events:
        formatted.append(
            f"[{event['id']}] {event['title']}\n"
            f"   Description: {event.get('description', 'N/A')[:400]}"
        )
    return "\n\n".join(formatted)


def generate_resolution_matrix(
    llm_client: LLMClient,
    cluster: dict,
    cluster_world_states: dict,
) -> ResolutionMatrix | None:
    """
    Generate resolution matrix for a single cluster using LLM.
    """
    cluster_id = cluster.get("cluster_id", "unknown")
    events = cluster.get("events", [])
    world_states = cluster_world_states.get("world_states", [])

    if not events or not world_states:
        logger.error(f"No events or world states for {cluster_id}")
        return None

    # Get IDs for template
    example_state = world_states[0]["id"] if world_states else "S1"
    example_event = events[0]["id"] if events else "event_1"

    # Format prompt
    prompt = RESOLUTION_PROMPT_TEMPLATE.format(
        world_states_formatted=format_world_states_for_prompt(world_states),
        events_formatted=format_events_for_prompt(events),
        example_state=example_state,
        example_event=example_event,
    )

    # Call LLM
    logger.info(f"Generating resolution matrix for {cluster_id}...")
    response = llm_client.complete(prompt, system=RESOLUTION_SYSTEM_PROMPT)

    # Parse response
    parsed = extract_json_from_response(response)
    if not parsed:
        logger.error(f"Failed to parse JSON from LLM response for {cluster_id}")
        logger.debug(f"Raw response: {response[:500]}...")
        return None

    # Extract matrix
    matrix = parsed.get("matrix", {})
    if not matrix:
        logger.error(f"No matrix in response for {cluster_id}")
        return None

    # Validate
    state_ids = [s["id"] for s in world_states]
    event_ids = [e["id"] for e in events]
    errors, warnings = validate_resolution_matrix(matrix, state_ids, event_ids)

    # Build result
    result = ResolutionMatrix(
        cluster_id=cluster_id,
        matrix=matrix,
        confidence=float(parsed.get("confidence", 0.7)),
        reasoning=parsed.get("reasoning", {}),
        validation_errors=errors,
        validation_warnings=warnings,
    )

    return result


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("Starting 07_3_build_resolution_matrix")

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
    with open(clusters_file, encoding="utf-8") as f:
        clusters_data = json.load(f)

    clusters = clusters_data.get("clusters", [])
    clusters_by_id = {c["cluster_id"]: c for c in clusters}
    logger.info(f"Loaded {len(clusters)} clusters")

    # =========================================================================
    # Load world states
    # =========================================================================

    if INPUT_RUN_FOLDER:
        world_states_folder = INPUT_WORLD_STATES_DIR / INPUT_RUN_FOLDER
    else:
        world_states_folder = find_latest_run_folder(INPUT_WORLD_STATES_DIR)

    if not world_states_folder or not world_states_folder.exists():
        raise FileNotFoundError(f"World states folder not found: {world_states_folder}")

    world_states_file = world_states_folder / "world_states.json"
    with open(world_states_file, encoding="utf-8") as f:
        world_states_data = json.load(f)

    cluster_world_states = world_states_data.get("cluster_world_states", [])
    world_states_by_cluster = {cws["cluster_id"]: cws for cws in cluster_world_states}
    logger.info(f"Loaded world states for {len(cluster_world_states)} clusters")

    # =========================================================================
    # Initialize LLM client
    # =========================================================================

    llm_client = LLMClient(OPENROUTER_API_KEY, LLM_MODEL)
    logger.info(f"LLM client initialized: {LLM_MODEL}")

    # =========================================================================
    # Generate resolution matrices
    # =========================================================================

    all_matrices: list[ResolutionMatrix] = []
    failed_clusters: list[str] = []

    for i, cluster in enumerate(clusters):
        cluster_id = cluster.get("cluster_id", f"cluster_{i}")

        # Get world states for this cluster
        cws = world_states_by_cluster.get(cluster_id)
        if not cws:
            logger.warning(f"No world states found for {cluster_id}, skipping")
            failed_clusters.append(cluster_id)
            continue

        logger.info(f"Processing cluster {i + 1}/{len(clusters)}: {cluster_id}")

        try:
            result = generate_resolution_matrix(llm_client, cluster, cws)
            if result:
                all_matrices.append(result)
                if result.validation_errors:
                    logger.warning(f"  Validation errors: {result.validation_errors}")
                if result.validation_warnings:
                    logger.info(f"  Validation warnings: {result.validation_warnings}")

                # Log matrix summary
                yes_count = sum(
                    1 for s in result.matrix.values() for r in s.values() if r == "YES"
                )
                no_count = sum(
                    1 for s in result.matrix.values() for r in s.values() if r == "NO"
                )
                unc_count = sum(
                    1 for s in result.matrix.values() for r in s.values() if r == "UNC"
                )
                logger.info(
                    f"  Matrix: {yes_count} YES, {no_count} NO, {unc_count} UNC"
                )
            else:
                failed_clusters.append(cluster_id)
                logger.error("  Failed to generate resolution matrix")

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

    # Save resolution_matrices.json
    matrices_output = {
        "_meta": {
            "description": "Resolution matrices for cross-event clusters",
            "created_at": start_time.isoformat(),
            "llm_model": LLM_MODEL,
            "source_clusters": str(clusters_folder),
            "source_world_states": str(world_states_folder),
            "total_clusters": len(clusters),
            "successful_clusters": len(all_matrices),
            "failed_clusters": len(failed_clusters),
        },
        "resolution_matrices": [m.to_dict() for m in all_matrices],
    }

    with open(output_folder / "resolution_matrices.json", "w", encoding="utf-8") as f:
        json.dump(matrices_output, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved resolution matrices for {len(all_matrices)} clusters")

    # Save summary.json
    end_time = datetime.now(timezone.utc)

    valid_count = sum(1 for m in all_matrices if not m.validation_errors)
    total_cells = sum(
        len(m.matrix) * len(next(iter(m.matrix.values()), {}))
        for m in all_matrices
        if m.matrix
    )

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
            "successful_clusters": len(all_matrices),
            "valid_clusters": valid_count,
            "failed_clusters": failed_clusters,
            "total_matrix_cells": total_cells,
        },
        "validation_summary": {
            "clusters_with_errors": sum(1 for m in all_matrices if m.validation_errors),
            "clusters_with_warnings": sum(
                1 for m in all_matrices if m.validation_warnings
            ),
        },
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Saved summary.json")

    # Log final summary
    logger.info("=" * 60)
    logger.info("RESOLUTION MATRIX GENERATION COMPLETE")
    logger.info(f"  Successful: {len(all_matrices)}/{len(clusters)} clusters")
    logger.info(f"  Valid: {valid_count}/{len(all_matrices)}")
    logger.info(f"  Total cells: {total_cells}")
    logger.info(f"  LLM requests: {llm_client.request_count}")
    logger.info(f"  Duration: {summary['run_info']['duration_seconds']:.2f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

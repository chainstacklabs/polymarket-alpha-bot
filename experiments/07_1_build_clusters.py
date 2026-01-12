"""
Build cross-event clusters for coverage portfolio analysis.

Groups events that share underlying real-world drivers using:
- Entity overlap (Jaccard similarity)
- Embedding similarity (cosine)
- HDBSCAN clustering

Filters out same-event variants (TIMEFRAME_VARIANT, THRESHOLD_VARIANT, SUBSET_VARIANT)
to focus only on truly cross-event relationships.

Input:
- data/_live/events.json          → Event metadata
- data/_live/embeddings.npy       → Event embeddings
- data/_live/embeddings_meta.json → Embedding event IDs
- data/_live/graph.json           → Structural relations (for exclusion)
- data/04_2_embed_events/<latest>/entity_sets.json → Entity sets per event

Output:
- data/07_1_build_clusters/<timestamp>/clusters.json
- data/07_1_build_clusters/<timestamp>/affinity_matrix.json
- data/07_1_build_clusters/<timestamp>/summary.json

Pipeline: 06_x -> [07_1_build_clusters] -> 07_2_define_world_states
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import hdbscan
import numpy as np
from numpy.typing import NDArray
from sklearn.cluster import AgglomerativeClustering  # noqa: E402

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"

# Inputs
EVENTS_FILE = LIVE_DIR / "events.json"
EMBEDDINGS_FILE = LIVE_DIR / "embeddings.npy"
EMBEDDINGS_META_FILE = LIVE_DIR / "embeddings_meta.json"
GRAPH_FILE = LIVE_DIR / "graph.json"
ENTITY_SETS_DIR = DATA_DIR / "04_2_embed_events"

# Output
SCRIPT_OUTPUT_DIR = DATA_DIR / "07_1_build_clusters"

# Clustering parameters
MIN_CLUSTER_SIZE = 3  # Minimum events in a cluster
MAX_CLUSTER_SIZE = 8  # Maximum events in a cluster (for tractable optimization)
MIN_ENTITY_OVERLAP = 1  # At least 1 shared entity required (relaxed for small datasets)

# Adaptive thresholds for small datasets
SMALL_DATASET_THRESHOLD = 50  # Use AgglomerativeClustering below this
AFFINITY_THRESHOLD = 0.35  # Minimum affinity to consider for clustering

# Affinity weights
ENTITY_OVERLAP_WEIGHT = 0.5
EMBEDDING_SIMILARITY_WEIGHT = 0.5

# Relation types to exclude (same-event variants)
EXCLUDED_RELATION_TYPES = {
    "TIMEFRAME_VARIANT",
    "THRESHOLD_VARIANT",
    "SUBSET_VARIANT",
    "HIERARCHICAL",  # Parent-child within same market
    "SERIES_MEMBER",  # Same series, different index
}

# Domain detection keywords
DOMAIN_KEYWORDS = {
    "geopolitics": [
        "war",
        "ceasefire",
        "military",
        "invasion",
        "territory",
        "troops",
        "nato",
        "sovereignty",
    ],
    "us_politics": [
        "trump",
        "biden",
        "congress",
        "senate",
        "election",
        "speaker",
        "cabinet",
        "deport",
    ],
    "crypto": ["bitcoin", "ethereum", "crypto", "btc", "eth"],
    "sports": ["championship", "win", "score", "match", "team"],
    "economics": [
        "gdp",
        "inflation",
        "rate",
        "fed",
        "recession",
        "deficit",
        "tariff",
        "budget",
    ],
    "europe": ["macron", "starmer", "eu", "france", "uk", "germany"],
}

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
class EventCluster:
    """A cluster of events sharing underlying drivers."""

    cluster_id: str
    event_ids: list[str]
    events: list[dict]  # Full event objects
    shared_entities: list[str]
    centroid_embedding: list[float]
    coherence_score: float
    domain: str | None
    avg_affinity: float

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "event_ids": self.event_ids,
            "event_count": len(self.event_ids),
            "events": [
                {
                    "id": e.get("id"),
                    "title": e.get("title"),
                    "description": e.get("description", "")[:200] + "..."
                    if len(e.get("description", "")) > 200
                    else e.get("description", ""),
                }
                for e in self.events
            ],
            "shared_entities": self.shared_entities,
            "centroid_embedding": self.centroid_embedding[
                :10
            ],  # First 10 dims for debugging
            "coherence_score": round(self.coherence_score, 4),
            "domain": self.domain,
            "avg_affinity": round(self.avg_affinity, 4),
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


def compute_jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def compute_cosine_similarity(vec_a: NDArray, vec_b: NDArray) -> float:
    """Compute cosine similarity between two vectors."""
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


def build_exclusion_set(graph_edges: list[dict]) -> set[tuple[str, str]]:
    """
    Build set of event pairs that should NOT be in the same cross-event cluster.

    These are pairs with structural relations indicating they're variants of
    the same underlying event (timeframe, threshold, subset variants).
    """
    exclusions = set()
    for edge in graph_edges:
        if edge.get("relation_type") in EXCLUDED_RELATION_TYPES:
            source = edge.get("source")
            target = edge.get("target")
            if source and target:
                # Store as sorted tuple for consistent lookup
                pair = tuple(sorted([source, target]))
                exclusions.add(pair)
    return exclusions


def compute_entity_overlap_matrix(
    event_ids: list[str],
    entity_sets: dict[str, list[str]],
) -> NDArray:
    """
    Compute pairwise Jaccard similarity matrix for entity overlap.
    """
    n = len(event_ids)
    matrix = np.zeros((n, n))

    for i, id_a in enumerate(event_ids):
        set_a = set(entity_sets.get(id_a, []))
        for j, id_b in enumerate(event_ids):
            if i == j:
                matrix[i, j] = 1.0
            elif j > i:
                set_b = set(entity_sets.get(id_b, []))
                sim = compute_jaccard_similarity(set_a, set_b)
                matrix[i, j] = sim
                matrix[j, i] = sim

    return matrix


def compute_embedding_similarity_matrix(embeddings: NDArray) -> NDArray:
    """
    Compute pairwise cosine similarity matrix for embeddings.
    """
    # Normalize embeddings
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)  # Avoid division by zero
    normalized = embeddings / norms

    # Compute cosine similarity matrix
    similarity_matrix = np.dot(normalized, normalized.T)

    return similarity_matrix


def compute_affinity_matrix(
    entity_matrix: NDArray,
    embedding_matrix: NDArray,
    entity_weight: float = ENTITY_OVERLAP_WEIGHT,
    embedding_weight: float = EMBEDDING_SIMILARITY_WEIGHT,
) -> NDArray:
    """
    Combine entity overlap and embedding similarity into a single affinity matrix.
    """
    return entity_weight * entity_matrix + embedding_weight * embedding_matrix


def apply_exclusions(
    affinity_matrix: NDArray,
    event_ids: list[str],
    exclusion_set: set[tuple[str, str]],
) -> NDArray:
    """
    Zero out affinity for excluded pairs (same-event variants).
    """
    matrix = affinity_matrix.copy()
    id_to_idx = {eid: i for i, eid in enumerate(event_ids)}

    for pair in exclusion_set:
        if pair[0] in id_to_idx and pair[1] in id_to_idx:
            i = id_to_idx[pair[0]]
            j = id_to_idx[pair[1]]
            matrix[i, j] = 0.0
            matrix[j, i] = 0.0

    return matrix


def compute_coherence_score(
    cluster_indices: list[int],
    affinity_matrix: NDArray,
    all_indices: list[int],
) -> float:
    """
    Compute coherence score for a cluster.

    Coherence = (mean intra-cluster affinity) / (mean inter-cluster affinity)
    High coherence = events are more similar to each other than to outsiders.
    """
    if len(cluster_indices) < 2:
        return 0.0

    # Intra-cluster affinity (excluding self-similarity)
    intra_affinities = []
    for i, idx_a in enumerate(cluster_indices):
        for idx_b in cluster_indices[i + 1 :]:
            intra_affinities.append(affinity_matrix[idx_a, idx_b])

    intra_mean = np.mean(intra_affinities) if intra_affinities else 0.0

    # Inter-cluster affinity
    non_cluster_indices = [i for i in all_indices if i not in cluster_indices]
    if not non_cluster_indices:
        return float("inf") if intra_mean > 0 else 1.0

    inter_affinities = []
    for idx_a in cluster_indices:
        for idx_b in non_cluster_indices:
            inter_affinities.append(affinity_matrix[idx_a, idx_b])

    inter_mean = np.mean(inter_affinities) if inter_affinities else 0.01

    return intra_mean / max(inter_mean, 0.01)


def find_shared_entities(
    event_ids: list[str],
    entity_sets: dict[str, list[str]],
    min_occurrences: int = 2,
) -> list[str]:
    """
    Find entities that appear in at least min_occurrences events.
    """
    entity_counts: dict[str, int] = {}
    for eid in event_ids:
        for entity in entity_sets.get(eid, []):
            entity_counts[entity] = entity_counts.get(entity, 0) + 1

    shared = [e for e, count in entity_counts.items() if count >= min_occurrences]
    return sorted(shared, key=lambda e: entity_counts[e], reverse=True)


def detect_domain(cluster_events: list[dict], shared_entities: list[str]) -> str | None:
    """
    Detect the domain of a cluster based on keywords in titles and entities.
    """
    # Collect all text for matching
    text_corpus = []
    for event in cluster_events:
        text_corpus.append(event.get("title", "").lower())
        text_corpus.append(event.get("description", "").lower()[:500])
    text_corpus.extend([e.lower() for e in shared_entities])

    combined_text = " ".join(text_corpus)

    # Score each domain
    domain_scores: dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in combined_text)
        if score > 0:
            domain_scores[domain] = score

    if not domain_scores:
        return None

    return max(domain_scores, key=lambda d: domain_scores[d])


def cluster_events_hdbscan(
    affinity_matrix: NDArray,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> NDArray:
    """
    Run HDBSCAN clustering on the affinity matrix.

    HDBSCAN expects a distance matrix, so we convert affinity to distance.
    """
    # Convert affinity to distance (1 - affinity)
    # Clip to ensure valid range [0, 1]
    affinity_clipped = np.clip(affinity_matrix, 0, 1)
    distance_matrix = 1 - affinity_clipped

    # Run HDBSCAN
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="precomputed",
        cluster_selection_method="eom",  # Excess of Mass
    )

    labels = clusterer.fit_predict(distance_matrix)

    return labels


def cluster_events_agglomerative(
    affinity_matrix: NDArray,
    distance_threshold: float = 1 - AFFINITY_THRESHOLD,
) -> NDArray:
    """
    Run AgglomerativeClustering on the affinity matrix.

    Better for small datasets where HDBSCAN struggles.
    Uses distance_threshold to determine cluster boundaries.
    """
    # Convert affinity to distance (1 - affinity)
    affinity_clipped = np.clip(affinity_matrix, 0, 1)
    distance_matrix = 1 - affinity_clipped

    # Run AgglomerativeClustering
    clusterer = AgglomerativeClustering(
        n_clusters=None,
        metric="precomputed",
        linkage="average",
        distance_threshold=distance_threshold,
    )

    labels = clusterer.fit_predict(distance_matrix)

    return labels


def run_clustering(
    affinity_matrix: NDArray,
    n_events: int,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> tuple[NDArray, str]:
    """
    Choose and run the appropriate clustering algorithm based on dataset size.

    Returns (labels, method_name)
    """
    if n_events < SMALL_DATASET_THRESHOLD:
        logger.info(f"Small dataset ({n_events} events), using AgglomerativeClustering")
        labels = cluster_events_agglomerative(affinity_matrix)
        return labels, "AgglomerativeClustering"
    else:
        logger.info(f"Large dataset ({n_events} events), using HDBSCAN")
        labels = cluster_events_hdbscan(affinity_matrix, min_cluster_size)
        return labels, "HDBSCAN"


def filter_clusters(
    labels: NDArray,
    event_ids: list[str],
    entity_sets: dict[str, list[str]],
    exclusion_set: set[tuple[str, str]],
    min_size: int = MIN_CLUSTER_SIZE,
    max_size: int = MAX_CLUSTER_SIZE,
    min_entities: int = MIN_ENTITY_OVERLAP,
) -> list[list[str]]:
    """
    Filter and validate clusters.

    Removes clusters that:
    - Are too small (< min_size)
    - Are too large (> max_size)
    - Contain excluded pairs
    - Don't have enough shared entities
    """
    # Group event IDs by cluster label
    cluster_groups: dict[int, list[str]] = {}
    for idx, label in enumerate(labels):
        if label == -1:  # Noise point
            continue
        if label not in cluster_groups:
            cluster_groups[label] = []
        cluster_groups[label].append(event_ids[idx])

    valid_clusters = []

    for label, members in cluster_groups.items():
        # Size check
        if len(members) < min_size or len(members) > max_size:
            logger.debug(
                f"Cluster {label} rejected: size {len(members)} outside [{min_size}, {max_size}]"
            )
            continue

        # Exclusion check
        has_excluded = False
        for i, id_a in enumerate(members):
            for id_b in members[i + 1 :]:
                pair = tuple(sorted([id_a, id_b]))
                if pair in exclusion_set:
                    has_excluded = True
                    break
            if has_excluded:
                break

        if has_excluded:
            logger.debug(f"Cluster {label} rejected: contains excluded pair")
            continue

        # Shared entities check
        shared = find_shared_entities(members, entity_sets, min_occurrences=2)
        if len(shared) < min_entities:
            logger.debug(
                f"Cluster {label} rejected: only {len(shared)} shared entities"
            )
            continue

        valid_clusters.append(members)

    return valid_clusters


# =============================================================================
# MAIN
# =============================================================================


def main() -> None:
    """Main entry point."""
    start_time = datetime.now(timezone.utc)
    logger.info("Starting 07_1_build_clusters")

    # =========================================================================
    # Load inputs
    # =========================================================================

    # Load events
    if not EVENTS_FILE.exists():
        raise FileNotFoundError(f"Events file not found: {EVENTS_FILE}")

    with open(EVENTS_FILE, encoding="utf-8") as f:
        events_data = json.load(f)
    events_list = events_data.get("events", [])
    events_by_id = {e["id"]: e for e in events_list}
    logger.info(f"Loaded {len(events_list)} events")

    # Load embeddings
    if not EMBEDDINGS_FILE.exists():
        raise FileNotFoundError(f"Embeddings file not found: {EMBEDDINGS_FILE}")

    embeddings = np.load(EMBEDDINGS_FILE)
    logger.info(f"Loaded embeddings: shape {embeddings.shape}")

    # Load embeddings metadata (event ID order)
    if not EMBEDDINGS_META_FILE.exists():
        raise FileNotFoundError(f"Embeddings meta not found: {EMBEDDINGS_META_FILE}")

    with open(EMBEDDINGS_META_FILE, encoding="utf-8") as f:
        embeddings_meta = json.load(f)
    embedding_event_ids = embeddings_meta.get("event_ids", [])
    logger.info(f"Embedding event IDs: {len(embedding_event_ids)}")

    # Validate alignment
    if len(embedding_event_ids) != embeddings.shape[0]:
        raise ValueError(
            f"Mismatch: {len(embedding_event_ids)} event IDs vs {embeddings.shape[0]} embeddings"
        )

    # Load graph (for exclusions)
    if not GRAPH_FILE.exists():
        raise FileNotFoundError(f"Graph file not found: {GRAPH_FILE}")

    with open(GRAPH_FILE, encoding="utf-8") as f:
        graph_data = json.load(f)
    graph_edges = graph_data.get("edges", [])
    logger.info(f"Loaded {len(graph_edges)} graph edges")

    # Load entity sets
    entity_sets_folder = find_latest_run_folder(ENTITY_SETS_DIR)
    if not entity_sets_folder:
        raise FileNotFoundError(f"No entity sets folder found in {ENTITY_SETS_DIR}")

    entity_sets_file = entity_sets_folder / "entity_sets.json"
    if not entity_sets_file.exists():
        raise FileNotFoundError(f"Entity sets file not found: {entity_sets_file}")

    with open(entity_sets_file, encoding="utf-8") as f:
        entity_data = json.load(f)
    entity_sets = entity_data.get("entity_sets", {})
    logger.info(f"Loaded entity sets for {len(entity_sets)} events")

    # =========================================================================
    # Build exclusion set
    # =========================================================================

    exclusion_set = build_exclusion_set(graph_edges)
    logger.info(f"Built exclusion set: {len(exclusion_set)} pairs")

    # =========================================================================
    # Compute affinity matrix
    # =========================================================================

    logger.info("Computing entity overlap matrix...")
    entity_matrix = compute_entity_overlap_matrix(embedding_event_ids, entity_sets)

    logger.info("Computing embedding similarity matrix...")
    embedding_matrix = compute_embedding_similarity_matrix(embeddings)

    logger.info("Combining into affinity matrix...")
    affinity_matrix = compute_affinity_matrix(
        entity_matrix,
        embedding_matrix,
        ENTITY_OVERLAP_WEIGHT,
        EMBEDDING_SIMILARITY_WEIGHT,
    )

    logger.info("Applying exclusions...")
    affinity_matrix = apply_exclusions(
        affinity_matrix, embedding_event_ids, exclusion_set
    )

    # Log some statistics
    upper_triangle = affinity_matrix[np.triu_indices(len(embedding_event_ids), k=1)]
    logger.info(
        f"Affinity stats: mean={np.mean(upper_triangle):.3f}, max={np.max(upper_triangle):.3f}"
    )

    # =========================================================================
    # Run clustering
    # =========================================================================

    logger.info("Running clustering...")
    labels, clustering_method = run_clustering(
        affinity_matrix,
        n_events=len(embedding_event_ids),
        min_cluster_size=MIN_CLUSTER_SIZE,
    )

    unique_labels = set(labels)
    n_clusters_raw = len(unique_labels) - (1 if -1 in unique_labels else 0)
    n_noise = sum(1 for l in labels if l == -1)
    logger.info(
        f"{clustering_method} found {n_clusters_raw} raw clusters, {n_noise} noise points"
    )

    # =========================================================================
    # Filter and validate clusters
    # =========================================================================

    logger.info("Filtering clusters...")
    valid_cluster_ids = filter_clusters(
        labels,
        embedding_event_ids,
        entity_sets,
        exclusion_set,
        min_size=MIN_CLUSTER_SIZE,
        max_size=MAX_CLUSTER_SIZE,
        min_entities=MIN_ENTITY_OVERLAP,
    )
    logger.info(f"Valid clusters after filtering: {len(valid_cluster_ids)}")

    # =========================================================================
    # Build EventCluster objects
    # =========================================================================

    clusters: list[EventCluster] = []
    id_to_idx = {eid: i for i, eid in enumerate(embedding_event_ids)}
    all_indices = list(range(len(embedding_event_ids)))

    for i, member_ids in enumerate(valid_cluster_ids):
        cluster_id = f"cluster_{i + 1:03d}"

        # Get events
        cluster_events = [
            events_by_id[eid] for eid in member_ids if eid in events_by_id
        ]

        # Compute centroid embedding
        cluster_indices = [id_to_idx[eid] for eid in member_ids if eid in id_to_idx]
        cluster_embeddings = embeddings[cluster_indices]
        centroid = np.mean(cluster_embeddings, axis=0)

        # Find shared entities
        shared_entities = find_shared_entities(
            member_ids, entity_sets, min_occurrences=2
        )

        # Compute coherence
        coherence = compute_coherence_score(
            cluster_indices, affinity_matrix, all_indices
        )

        # Detect domain
        domain = detect_domain(cluster_events, shared_entities)

        # Compute average affinity within cluster
        intra_affinities = []
        for j, idx_a in enumerate(cluster_indices):
            for idx_b in cluster_indices[j + 1 :]:
                intra_affinities.append(affinity_matrix[idx_a, idx_b])
        avg_affinity = float(np.mean(intra_affinities)) if intra_affinities else 0.0

        cluster = EventCluster(
            cluster_id=cluster_id,
            event_ids=member_ids,
            events=cluster_events,
            shared_entities=shared_entities,
            centroid_embedding=centroid.tolist(),
            coherence_score=coherence,
            domain=domain,
            avg_affinity=avg_affinity,
        )
        clusters.append(cluster)

    # Sort by coherence score (best first)
    clusters.sort(key=lambda c: c.coherence_score, reverse=True)

    # =========================================================================
    # Save outputs
    # =========================================================================

    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    output_folder = SCRIPT_OUTPUT_DIR / timestamp
    output_folder.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output folder: {output_folder}")

    # Save clusters.json
    clusters_output = {
        "_meta": {
            "description": "Cross-event clusters for coverage portfolio analysis",
            "created_at": start_time.isoformat(),
            "clustering_method": clustering_method,
            "affinity_weights": {
                "entity_overlap": ENTITY_OVERLAP_WEIGHT,
                "embedding_similarity": EMBEDDING_SIMILARITY_WEIGHT,
            },
            "parameters": {
                "min_cluster_size": MIN_CLUSTER_SIZE,
                "max_cluster_size": MAX_CLUSTER_SIZE,
                "min_entity_overlap": MIN_ENTITY_OVERLAP,
            },
        },
        "clusters": [c.to_dict() for c in clusters],
    }

    with open(output_folder / "clusters.json", "w", encoding="utf-8") as f:
        json.dump(clusters_output, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(clusters)} clusters to clusters.json")

    # Save affinity matrix (for debugging)
    affinity_output = {
        "_meta": {
            "description": "Pairwise affinity matrix (entity overlap + embedding similarity)",
            "created_at": start_time.isoformat(),
            "event_ids": embedding_event_ids,
        },
        "entity_matrix": entity_matrix.tolist(),
        "embedding_matrix": embedding_matrix.tolist(),
        "combined_matrix": affinity_matrix.tolist(),
    }

    with open(output_folder / "affinity_matrix.json", "w", encoding="utf-8") as f:
        json.dump(affinity_output, f, indent=2)
    logger.info("Saved affinity_matrix.json")

    # Save summary.json
    end_time = datetime.now(timezone.utc)

    domain_counts = {}
    for c in clusters:
        if c.domain:
            domain_counts[c.domain] = domain_counts.get(c.domain, 0) + 1

    summary = {
        "run_info": {
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat(),
            "duration_seconds": (end_time - start_time).total_seconds(),
            "output_folder": str(output_folder),
        },
        "input_statistics": {
            "total_events": len(events_list),
            "events_with_embeddings": len(embedding_event_ids),
            "events_with_entities": len(entity_sets),
            "exclusion_pairs": len(exclusion_set),
        },
        "clustering_statistics": {
            "raw_clusters": n_clusters_raw,
            "noise_points": n_noise,
            "valid_clusters": len(clusters),
            "total_events_clustered": sum(len(c.event_ids) for c in clusters),
            "avg_cluster_size": (
                sum(len(c.event_ids) for c in clusters) / len(clusters)
                if clusters
                else 0
            ),
            "avg_coherence": (
                sum(c.coherence_score for c in clusters) / len(clusters)
                if clusters
                else 0
            ),
        },
        "domain_breakdown": domain_counts,
        "config": {
            "min_cluster_size": MIN_CLUSTER_SIZE,
            "max_cluster_size": MAX_CLUSTER_SIZE,
            "min_entity_overlap": MIN_ENTITY_OVERLAP,
            "entity_overlap_weight": ENTITY_OVERLAP_WEIGHT,
            "embedding_similarity_weight": EMBEDDING_SIMILARITY_WEIGHT,
            "excluded_relation_types": list(EXCLUDED_RELATION_TYPES),
        },
    }

    with open(output_folder / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Saved summary.json")

    # Log final summary
    logger.info("=" * 60)
    logger.info("CLUSTERING COMPLETE")
    logger.info(f"  Valid clusters: {len(clusters)}")
    logger.info(f"  Events clustered: {sum(len(c.event_ids) for c in clusters)}")
    if clusters:
        logger.info(
            f"  Best cluster: {clusters[0].cluster_id} (coherence={clusters[0].coherence_score:.2f})"
        )
        logger.info(f"    Events: {clusters[0].event_ids}")
        logger.info(f"    Shared entities: {clusters[0].shared_entities[:5]}")
    logger.info(f"  Duration: {summary['run_info']['duration_seconds']:.2f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

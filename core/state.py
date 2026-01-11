"""
SQLite-backed pipeline state for O(1) lookups and persistence.

Manages:
- Processed event IDs
- Entity index and mappings
- Knowledge graph
- Embeddings metadata
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
LIVE_DIR = DATA_DIR / "_live"
STATE_DB_PATH = LIVE_DIR / "state.db"
EMBEDDINGS_PATH = LIVE_DIR / "embeddings.npy"
EMBEDDINGS_META_PATH = LIVE_DIR / "embeddings_meta.json"
GRAPH_PATH = LIVE_DIR / "graph.json"
EVENTS_PATH = LIVE_DIR / "events.json"
OPPORTUNITIES_PATH = LIVE_DIR / "opportunities.json"


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class GraphData:
    """In-memory representation of knowledge graph."""

    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    transitive_chains: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "transitive_chains": self.transitive_chains,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphData":
        return cls(
            nodes=data.get("nodes", []),
            edges=data.get("edges", []),
            transitive_chains=data.get("transitive_chains", []),
        )


@dataclass
class StateStats:
    """Statistics about current state."""

    total_events: int
    total_entities: int
    total_edges: int
    last_full_run: str | None
    last_refresh: str | None


# =============================================================================
# STATE MANAGER
# =============================================================================


class PipelineState:
    """
    SQLite-backed pipeline state manager.

    Provides O(1) lookups for processed events and efficient
    batch operations for adding new data.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or STATE_DB_PATH
        self.live_dir = self.db_path.parent
        self.live_dir.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

        # In-memory caches for fast access during pipeline run
        self._processed_ids_cache: set[str] | None = None
        self._entity_mappings_cache: dict[str, str] | None = None

    def _init_tables(self) -> None:
        """Initialize database schema."""
        self.conn.executescript("""
            -- Processed events
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                title TEXT,
                data JSON,
                processed_at TEXT
            );

            -- Extracted entities
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                text TEXT,
                canonical TEXT,
                entity_type TEXT,
                frequency INTEGER DEFAULT 1,
                data JSON
            );

            -- Entity normalization mappings (raw -> canonical)
            CREATE TABLE IF NOT EXISTS entity_mappings (
                raw_text TEXT PRIMARY KEY,
                canonical TEXT,
                mapping_type TEXT  -- 'fuzzy', 'llm', 'exact'
            );

            -- Graph edges (relations between events)
            CREATE TABLE IF NOT EXISTS graph_edges (
                source_id TEXT,
                target_id TEXT,
                relation_type TEXT,
                confidence REAL,
                data JSON,
                PRIMARY KEY (source_id, target_id, relation_type)
            );

            -- Pipeline run metadata
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT,  -- 'full', 'refresh'
                started_at TEXT,
                completed_at TEXT,
                events_processed INTEGER,
                new_events INTEGER,
                status TEXT  -- 'running', 'completed', 'failed'
            );

            -- Key-value metadata store
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            -- Indexes for fast lookups
            CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
            CREATE INDEX IF NOT EXISTS idx_entities_canonical ON entities(canonical);
            CREATE INDEX IF NOT EXISTS idx_entity_mappings_canonical ON entity_mappings(canonical);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id);
        """)
        self.conn.commit()

    # =========================================================================
    # EVENT MANAGEMENT
    # =========================================================================

    def get_processed_ids(self) -> set[str]:
        """Get all processed event IDs. Cached for performance."""
        if self._processed_ids_cache is None:
            cursor = self.conn.execute("SELECT id FROM events")
            self._processed_ids_cache = {row[0] for row in cursor.fetchall()}
        return self._processed_ids_cache

    def get_new_ids(self, all_ids: list[str]) -> set[str]:
        """Get IDs that haven't been processed yet."""
        processed = self.get_processed_ids()
        return set(all_ids) - processed

    def get_all_events(self) -> list[dict]:
        """Get all processed events."""
        cursor = self.conn.execute("SELECT data FROM events")
        return [json.loads(row[0]) for row in cursor.fetchall()]

    def get_event(self, event_id: str) -> dict | None:
        """Get a single event by ID."""
        cursor = self.conn.execute("SELECT data FROM events WHERE id = ?", (event_id,))
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def add_events(self, events: list[dict]) -> None:
        """Add new processed events."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO events (id, title, data, processed_at)
            VALUES (?, ?, ?, ?)
            """,
            [(e["id"], e.get("title", ""), json.dumps(e), now) for e in events],
        )
        self.conn.commit()
        # Invalidate cache
        self._processed_ids_cache = None

    def update_event_prices(self, price_updates: dict[str, float]) -> None:
        """Update prices for existing events."""
        for event_id, price in price_updates.items():
            cursor = self.conn.execute(
                "SELECT data FROM events WHERE id = ?", (event_id,)
            )
            row = cursor.fetchone()
            if row:
                event = json.loads(row[0])
                # Update price in markets
                for market in event.get("markets", []):
                    if "outcomePrices" in market:
                        market["outcomePrices"] = [price, 1 - price]
                self.conn.execute(
                    "UPDATE events SET data = ? WHERE id = ?",
                    (json.dumps(event), event_id),
                )
        self.conn.commit()

    # =========================================================================
    # ENTITY MANAGEMENT
    # =========================================================================

    def get_entity_mappings(self) -> dict[str, str]:
        """Get all entity mappings (raw -> canonical). Cached."""
        if self._entity_mappings_cache is None:
            cursor = self.conn.execute(
                "SELECT raw_text, canonical FROM entity_mappings"
            )
            self._entity_mappings_cache = {row[0]: row[1] for row in cursor.fetchall()}
        return self._entity_mappings_cache

    def add_entity_mappings(
        self, mappings: dict[str, str], mapping_type: str = "exact"
    ) -> None:
        """Add new entity mappings."""
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO entity_mappings (raw_text, canonical, mapping_type)
            VALUES (?, ?, ?)
            """,
            [(raw, canonical, mapping_type) for raw, canonical in mappings.items()],
        )
        self.conn.commit()
        self._entity_mappings_cache = None

    def get_all_entities(self) -> list[dict]:
        """Get all entities."""
        cursor = self.conn.execute("SELECT data FROM entities")
        return [json.loads(row[0]) for row in cursor.fetchall()]

    def add_entities(self, entities: list[dict]) -> None:
        """Add or update entities."""
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO entities
            (id, text, canonical, entity_type, frequency, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    e.get("id", e.get("text", "")),
                    e.get("text", ""),
                    e.get("canonical", e.get("text", "")),
                    e.get("type", "UNKNOWN"),
                    e.get("frequency", 1),
                    json.dumps(e),
                )
                for e in entities
            ],
        )
        self.conn.commit()

    # =========================================================================
    # GRAPH MANAGEMENT
    # =========================================================================

    def get_graph(self) -> GraphData:
        """Load graph from JSON file."""
        if GRAPH_PATH.exists():
            data = json.loads(GRAPH_PATH.read_text())
            return GraphData.from_dict(data)
        return GraphData()

    def save_graph(self, graph: GraphData) -> None:
        """Save graph to JSON file."""
        GRAPH_PATH.write_text(json.dumps(graph.to_dict(), indent=2))

    def add_graph_edges(self, edges: list[dict]) -> None:
        """Add edges to the graph (SQLite for queries, JSON for export)."""
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO graph_edges
            (source_id, target_id, relation_type, confidence, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    e["source_id"],
                    e["target_id"],
                    e["relation_type"],
                    e.get("confidence", 0.5),
                    json.dumps(e),
                )
                for e in edges
            ],
        )
        self.conn.commit()

    def get_edges_for_event(self, event_id: str) -> list[dict]:
        """Get all edges involving an event."""
        cursor = self.conn.execute(
            """
            SELECT data FROM graph_edges
            WHERE source_id = ? OR target_id = ?
            """,
            (event_id, event_id),
        )
        return [json.loads(row[0]) for row in cursor.fetchall()]

    # =========================================================================
    # EMBEDDINGS MANAGEMENT
    # =========================================================================

    def get_embeddings(self) -> tuple[np.ndarray | None, list[str]]:
        """Load embeddings and their event IDs."""
        if not EMBEDDINGS_PATH.exists():
            return None, []

        embeddings = np.load(EMBEDDINGS_PATH)

        if EMBEDDINGS_META_PATH.exists():
            meta = json.loads(EMBEDDINGS_META_PATH.read_text())
            event_ids = meta.get("event_ids", [])
        else:
            event_ids = []

        return embeddings, event_ids

    def save_embeddings(self, embeddings: np.ndarray, event_ids: list[str]) -> None:
        """Save embeddings and metadata."""
        np.save(EMBEDDINGS_PATH, embeddings)
        EMBEDDINGS_META_PATH.write_text(
            json.dumps({"event_ids": event_ids, "shape": list(embeddings.shape)})
        )

    def append_embeddings(
        self, new_embeddings: np.ndarray, new_event_ids: list[str]
    ) -> None:
        """Append new embeddings to existing."""
        existing, existing_ids = self.get_embeddings()

        if existing is not None and len(existing) > 0:
            combined = np.vstack([existing, new_embeddings])
            combined_ids = existing_ids + new_event_ids
        else:
            combined = new_embeddings
            combined_ids = new_event_ids

        self.save_embeddings(combined, combined_ids)

    # =========================================================================
    # RUN MANAGEMENT
    # =========================================================================

    def start_run(self, run_type: str) -> int:
        """Start a new pipeline run, return run ID."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """
            INSERT INTO runs (run_type, started_at, status)
            VALUES (?, ?, 'running')
            """,
            (run_type, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def complete_run(
        self,
        run_id: int,
        events_processed: int,
        new_events: int,
        status: str = "completed",
    ) -> None:
        """Mark a run as completed."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            UPDATE runs
            SET completed_at = ?, events_processed = ?, new_events = ?, status = ?
            WHERE id = ?
            """,
            (now, events_processed, new_events, status, run_id),
        )

        # Update metadata
        self.set_metadata(f"last_{self._get_run_type(run_id)}_run", now)
        self.conn.commit()

    def _get_run_type(self, run_id: int) -> str:
        cursor = self.conn.execute("SELECT run_type FROM runs WHERE id = ?", (run_id,))
        row = cursor.fetchone()
        return row[0] if row else "unknown"

    def get_last_run(self, run_type: str | None = None) -> dict | None:
        """Get info about the last run."""
        if run_type:
            cursor = self.conn.execute(
                """
                SELECT * FROM runs WHERE run_type = ?
                ORDER BY id DESC LIMIT 1
                """,
                (run_type,),
            )
        else:
            cursor = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_orphaned_runs(self) -> list[dict]:
        """
        Find runs that are stuck in 'running' status.

        These are runs that started but never completed, likely due to crashes.
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM runs
            WHERE status = 'running'
            ORDER BY id DESC
            """
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_run_failed(self, run_id: int, reason: str = "crashed") -> None:
        """Mark a run as failed (used for orphaned run cleanup)."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            UPDATE runs
            SET status = 'failed', completed_at = ?
            WHERE id = ?
            """,
            (now, run_id),
        )
        self.conn.commit()
        logger.warning(f"Marked run {run_id} as failed: {reason}")

    def cleanup_orphaned_runs(self) -> int:
        """
        Clean up any orphaned 'running' runs by marking them as failed.

        Returns the number of orphaned runs cleaned up.
        """
        orphaned = self.get_orphaned_runs()
        for run in orphaned:
            run_id = run["id"]
            started_at = run.get("started_at", "unknown")
            logger.warning(
                f"Found orphaned run {run_id} (started: {started_at}), marking as failed"
            )
            self.mark_run_failed(run_id, reason="orphaned_cleanup")
        return len(orphaned)

    # =========================================================================
    # METADATA
    # =========================================================================

    def get_metadata(self, key: str) -> str | None:
        """Get a metadata value."""
        cursor = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        """Set a metadata value."""
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    # =========================================================================
    # STATE OPERATIONS
    # =========================================================================

    def get_stats(self) -> StateStats:
        """Get current state statistics."""
        events_count = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        entities_count = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[
            0
        ]
        edges_count = self.conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[
            0
        ]

        return StateStats(
            total_events=events_count,
            total_entities=entities_count,
            total_edges=edges_count,
            last_full_run=self.get_metadata("last_full_run"),
            last_refresh=self.get_metadata("last_refresh_run"),
        )

    def reset(self) -> None:
        """Reset all state (for full reprocessing)."""
        logger.warning("Resetting pipeline state...")

        # Clear all tables
        self.conn.executescript("""
            DELETE FROM events;
            DELETE FROM entities;
            DELETE FROM entity_mappings;
            DELETE FROM graph_edges;
            DELETE FROM metadata;
        """)
        self.conn.commit()

        # Clear caches
        self._processed_ids_cache = None
        self._entity_mappings_cache = None

        # Remove ALL _live files (including events.json and opportunities.json)
        live_files = [
            EMBEDDINGS_PATH,
            EMBEDDINGS_META_PATH,
            GRAPH_PATH,
            EVENTS_PATH,
            OPPORTUNITIES_PATH,
        ]
        for path in live_files:
            if path.exists():
                path.unlink()
                logger.debug(f"Deleted {path.name}")

        logger.info("Pipeline state reset complete")

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()

    def __enter__(self) -> "PipelineState":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def load_state() -> PipelineState:
    """Load or create pipeline state."""
    return PipelineState()


def export_live_data(
    state: PipelineState,
    events: list[dict],
    opportunities: list[dict],
) -> None:
    """Export data to _live/ directory for API consumption."""
    export_timestamp = datetime.now(timezone.utc).isoformat()

    # Events with metadata
    events_data = {
        "_meta": {
            "exported_at": export_timestamp,
            "count": len(events),
            "source": "pipeline",
        },
        "events": events,
    }
    EVENTS_PATH.write_text(json.dumps(events_data, indent=2))

    # Opportunities with metadata
    opportunities_data = {
        "_meta": {
            "exported_at": export_timestamp,
            "count": len(opportunities),
            "source": "pipeline",
        },
        "opportunities": opportunities,
    }
    OPPORTUNITIES_PATH.write_text(json.dumps(opportunities_data, indent=2))

    # Graph is saved separately via state.save_graph()

    logger.info(
        f"Exported to _live/: {len(events)} events, {len(opportunities)} opportunities "
        f"(timestamp: {export_timestamp})"
    )

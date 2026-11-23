"""
SQLite-backed pipeline state for O(1) lookups and persistence.

Manages:
- Market groups and their metadata
- LLM-extracted implications (cached)
- Validated market pairs (cached)
- Covering portfolios with metrics
- Current market prices
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

from core.paths import LIVE_DIR, SEED_DIR

STATE_DB_PATH = LIVE_DIR / "state.db"

# Live data paths
GROUPS_PATH = LIVE_DIR / "groups.json"
PORTFOLIOS_PATH = LIVE_DIR / "portfolios.json"

# Seed data path
SEED_DATA_PATH = SEED_DIR / "seed.json"


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class StateStats:
    """Statistics about current state."""

    total_groups: int
    total_implications: int
    total_validated_pairs: int
    total_portfolios: int
    last_full_run: str | None
    last_refresh: str | None


# =============================================================================
# STATE MANAGER
# =============================================================================


class PipelineState:
    """
    SQLite-backed pipeline state manager.

    Provides O(1) lookups for groups, implications, validated pairs,
    and portfolios with efficient batch operations.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or STATE_DB_PATH
        self.live_dir = self.db_path.parent
        self.live_dir.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

        # In-memory cache for fast access during pipeline run
        self._processed_group_ids_cache: set[str] | None = None

        # Auto-import seed data if database is empty
        self._import_seed_if_empty()

    def _init_tables(self) -> None:
        """Initialize database schema."""
        self.conn.executescript("""
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

            -- Market groups
            CREATE TABLE IF NOT EXISTS groups (
                group_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                slug TEXT,
                partition_type TEXT,  -- 'candidate', 'threshold', 'timeframe'
                embedding_text TEXT,  -- Normalized for comparison
                data JSON,            -- Full group object with markets
                processed_at TEXT
            );

            -- Group-level implications (LLM results - CACHED FOREVER)
            CREATE TABLE IF NOT EXISTS implications (
                group_id TEXT PRIMARY KEY,
                title TEXT,
                yes_covered_by JSON,  -- Array of {group_id, probability, relationship}
                no_covered_by JSON,   -- Array of {group_id, probability, relationship}
                extracted_at TEXT,
                llm_model TEXT,       -- Track which model generated this
                FOREIGN KEY (group_id) REFERENCES groups(group_id)
            );

            -- Validated market pairs (LLM validated - CACHED)
            CREATE TABLE IF NOT EXISTS validated_pairs (
                pair_id TEXT PRIMARY KEY,  -- hash(target_market_id + cover_market_id + position)
                target_group_id TEXT,
                target_market_id TEXT,
                target_position TEXT,      -- 'YES' or 'NO'
                cover_group_id TEXT,
                cover_market_id TEXT,
                cover_position TEXT,       -- 'YES' or 'NO'
                cover_probability REAL,
                viability_score REAL,      -- 0.0-1.0 from LLM
                is_valid INTEGER DEFAULT 1, -- 1=valid, 0=invalid (explicit LLM judgment)
                validation_reason TEXT,
                validated_at TEXT,
                llm_model TEXT
            );

            -- Final portfolios with current prices
            CREATE TABLE IF NOT EXISTS portfolios (
                portfolio_id TEXT PRIMARY KEY,
                target_market_id TEXT,
                target_position TEXT,
                target_price REAL,
                cover_market_id TEXT,
                cover_position TEXT,
                cover_price REAL,
                total_cost REAL,
                coverage REAL,
                expected_profit REAL,
                tier INTEGER,              -- 1=HIGH, 2=GOOD, 3=MODERATE, 4=LOW
                tier_label TEXT,
                last_updated TEXT,
                data JSON                  -- Full portfolio details
            );

            -- Markets with current prices
            CREATE TABLE IF NOT EXISTS markets (
                market_id TEXT PRIMARY KEY,
                group_id TEXT,
                question TEXT,
                price_yes REAL,
                price_no REAL,
                resolution_date TEXT,
                bracket_label TEXT,
                last_updated TEXT,
                FOREIGN KEY (group_id) REFERENCES groups(group_id)
            );

            -- Indexes for new tables
            CREATE INDEX IF NOT EXISTS idx_groups_partition ON groups(partition_type);
            CREATE INDEX IF NOT EXISTS idx_validated_pairs_target ON validated_pairs(target_market_id);
            CREATE INDEX IF NOT EXISTS idx_validated_pairs_cover ON validated_pairs(cover_market_id);
            CREATE INDEX IF NOT EXISTS idx_portfolios_tier ON portfolios(tier);
            CREATE INDEX IF NOT EXISTS idx_portfolios_coverage ON portfolios(coverage DESC);
            CREATE INDEX IF NOT EXISTS idx_portfolios_profit ON portfolios(expected_profit DESC);
            CREATE INDEX IF NOT EXISTS idx_markets_group ON markets(group_id);
        """)
        self.conn.commit()

        # Migrations for existing databases
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Run database migrations for schema updates."""
        # Migration 1: Add is_valid column to validated_pairs (if not exists)
        cursor = self.conn.execute("PRAGMA table_info(validated_pairs)")
        columns = {row[1] for row in cursor.fetchall()}
        if "is_valid" not in columns:
            self.conn.execute(
                "ALTER TABLE validated_pairs ADD COLUMN is_valid INTEGER DEFAULT 1"
            )
            self.conn.commit()
            logger.info("Migration: Added is_valid column to validated_pairs")

        # Migration 2: prompt_version, so verdicts from an older validation
        # prompt are re-checked (NULL = before versioning).
        if "prompt_version" not in columns:
            self.conn.execute(
                "ALTER TABLE validated_pairs ADD COLUMN prompt_version INTEGER"
            )
            self.conn.commit()
            logger.info("Migration: Added prompt_version column to validated_pairs")

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
    # GROUP MANAGEMENT
    # =========================================================================

    def get_processed_group_ids(self) -> set[str]:
        """Get all processed group IDs."""
        if self._processed_group_ids_cache is None:
            cursor = self.conn.execute("SELECT group_id FROM groups")
            self._processed_group_ids_cache = {row[0] for row in cursor.fetchall()}
        return self._processed_group_ids_cache

    def get_new_group_ids(self, all_ids: list[str]) -> set[str]:
        """Get group IDs that haven't been processed yet."""
        processed = self.get_processed_group_ids()
        return set(all_ids) - processed

    def get_all_groups(self) -> list[dict]:
        """Get all processed groups."""
        cursor = self.conn.execute("SELECT data FROM groups")
        return [json.loads(row[0]) for row in cursor.fetchall()]

    def get_group(self, group_id: str) -> dict | None:
        """Get a single group by ID."""
        cursor = self.conn.execute(
            "SELECT data FROM groups WHERE group_id = ?", (group_id,)
        )
        row = cursor.fetchone()
        return json.loads(row[0]) if row else None

    def add_groups(self, groups: list[dict]) -> None:
        """Add new processed groups."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO groups
            (group_id, title, slug, partition_type, embedding_text, data, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    g["group_id"],
                    g.get("title", ""),
                    g.get("slug", ""),
                    g.get("partition_type", ""),
                    g.get("embedding_text", ""),
                    json.dumps(g),
                    now,
                )
                for g in groups
            ],
        )
        self.conn.commit()
        self._processed_group_ids_cache = None

    # =========================================================================
    # MARKET MANAGEMENT
    # =========================================================================

    def add_markets(self, markets: list[dict]) -> None:
        """Add or update markets with current prices."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO markets
            (market_id, group_id, question, price_yes, price_no,
             resolution_date, bracket_label, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    m["id"],
                    m.get("group_id", ""),
                    m.get("question", ""),
                    m.get("price_yes", 0.5),
                    m.get("price_no", 0.5),
                    m.get("resolution_date"),
                    m.get("bracket_label"),
                    now,
                )
                for m in markets
            ],
        )
        self.conn.commit()

    def update_market_prices(self, prices: dict[str, dict]) -> None:
        """
        Update market prices.

        Args:
            prices: Dict of market_id -> {price_yes, price_no}
        """
        now = datetime.now(timezone.utc).isoformat()
        for market_id, price_data in prices.items():
            self.conn.execute(
                """
                UPDATE markets
                SET price_yes = ?, price_no = ?, last_updated = ?
                WHERE market_id = ?
                """,
                (
                    price_data.get("price_yes", 0.5),
                    price_data.get("price_no", 0.5),
                    now,
                    market_id,
                ),
            )
        self.conn.commit()

    # =========================================================================
    # IMPLICATION MANAGEMENT (CACHED)
    # =========================================================================

    def get_implication(self, group_id: str) -> dict | None:
        """Get cached implication for a group."""
        cursor = self.conn.execute(
            """
            SELECT group_id, title, yes_covered_by, no_covered_by,
                   extracted_at, llm_model
            FROM implications WHERE group_id = ?
            """,
            (group_id,),
        )
        row = cursor.fetchone()
        if row:
            return {
                "group_id": row[0],
                "title": row[1],
                "yes_covered_by": json.loads(row[2]) if row[2] else [],
                "no_covered_by": json.loads(row[3]) if row[3] else [],
                "extracted_at": row[4],
                "llm_model": row[5],
            }
        return None

    def get_all_implications(self) -> list[dict]:
        """Get all cached implications."""
        cursor = self.conn.execute(
            """
            SELECT group_id, title, yes_covered_by, no_covered_by,
                   extracted_at, llm_model
            FROM implications
            """
        )
        return [
            {
                "group_id": row[0],
                "title": row[1],
                "yes_covered_by": json.loads(row[2]) if row[2] else [],
                "no_covered_by": json.loads(row[3]) if row[3] else [],
                "extracted_at": row[4],
                "llm_model": row[5],
            }
            for row in cursor.fetchall()
        ]

    def add_implications(self, implications: list[dict], llm_model: str) -> None:
        """
        Add LLM-extracted implications (CACHED FOREVER).

        These are immutable - once extracted, they never change.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO implications
            (group_id, title, yes_covered_by, no_covered_by, extracted_at, llm_model)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    impl["group_id"],
                    impl.get("title", ""),
                    json.dumps(impl.get("yes_covered_by", [])),
                    json.dumps(impl.get("no_covered_by", [])),
                    now,
                    llm_model,
                )
                for impl in implications
            ],
        )
        self.conn.commit()

    # =========================================================================
    # VALIDATED PAIRS MANAGEMENT (CACHED)
    # =========================================================================

    def get_validated_pair(self, pair_id: str) -> dict | None:
        """Get cached validated pair."""
        cursor = self.conn.execute(
            """
            SELECT pair_id, target_group_id, target_market_id, target_position,
                   cover_group_id, cover_market_id, cover_position,
                   cover_probability, viability_score, is_valid, validation_reason,
                   validated_at, llm_model, prompt_version
            FROM validated_pairs WHERE pair_id = ?
            """,
            (pair_id,),
        )
        row = cursor.fetchone()
        if row:
            return {
                "pair_id": row[0],
                "target_group_id": row[1],
                "target_market_id": row[2],
                "target_position": row[3],
                "cover_group_id": row[4],
                "cover_market_id": row[5],
                "cover_position": row[6],
                "cover_probability": row[7],
                "viability_score": row[8],
                "is_valid": bool(row[9]) if row[9] is not None else True,
                "validation_reason": row[10],
                "validated_at": row[11],
                "llm_model": row[12],
                "prompt_version": row[13],
            }
        return None

    def get_all_validated_pairs(self) -> list[dict]:
        """Get all cached validated pairs (only valid ones)."""
        cursor = self.conn.execute(
            """
            SELECT pair_id, target_group_id, target_market_id, target_position,
                   cover_group_id, cover_market_id, cover_position,
                   cover_probability, viability_score, is_valid, validation_reason,
                   validated_at, llm_model
            FROM validated_pairs
            WHERE viability_score >= 0.9 AND (is_valid = 1 OR is_valid IS NULL)
            """
        )
        return [
            {
                "pair_id": row[0],
                "target_group_id": row[1],
                "target_market_id": row[2],
                "target_position": row[3],
                "cover_group_id": row[4],
                "cover_market_id": row[5],
                "cover_position": row[6],
                "cover_probability": row[7],
                "viability_score": row[8],
                "is_valid": bool(row[9]) if row[9] is not None else True,
                "validation_reason": row[10],
                "validated_at": row[11],
                "llm_model": row[12],
            }
            for row in cursor.fetchall()
        ]

    def add_validated_pairs(
        self, pairs: list[dict], llm_model: str, prompt_version: int | None = None
    ) -> None:
        """
        Add LLM-validated pairs (CACHED).

        Reused until the validation prompt version changes.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO validated_pairs
            (pair_id, target_group_id, target_market_id, target_position,
             cover_group_id, cover_market_id, cover_position,
             cover_probability, viability_score, is_valid, validation_reason,
             validated_at, llm_model, prompt_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    p["pair_id"],
                    p.get("target_group_id", ""),
                    p["target_market_id"],
                    p["target_position"],
                    p.get("cover_group_id", ""),
                    p["cover_market_id"],
                    p["cover_position"],
                    p.get("cover_probability", 0.0),
                    p.get("viability_score", 0.0),
                    1 if p.get("is_valid", True) else 0,  # Store as integer
                    p.get("validation_reason", ""),
                    now,
                    llm_model,
                    prompt_version,
                )
                for p in pairs
            ],
        )
        self.conn.commit()

    def is_pair_validated(self, pair_id: str) -> bool:
        """Check if a pair is already validated (cached)."""
        cursor = self.conn.execute(
            "SELECT 1 FROM validated_pairs WHERE pair_id = ?", (pair_id,)
        )
        return cursor.fetchone() is not None

    # =========================================================================
    # PORTFOLIO MANAGEMENT
    # =========================================================================

    def get_portfolios(self) -> list[dict]:
        """Get all portfolios (alias for get_all_portfolios)."""
        return self.get_all_portfolios()

    def get_all_portfolios(self) -> list[dict]:
        """Get all portfolios."""
        cursor = self.conn.execute("SELECT data FROM portfolios")
        return [json.loads(row[0]) for row in cursor.fetchall()]

    def save_portfolios(self, portfolios: list[dict]) -> None:
        """Save portfolios (replaces all existing)."""
        now = datetime.now(timezone.utc).isoformat()

        # Deduplicate by pair_id (keep first occurrence, which has best coverage due to sorting)
        seen_ids: set[str] = set()
        unique_portfolios = []
        for p in portfolios:
            pair_id = p.get("pair_id", "")
            if pair_id and pair_id not in seen_ids:
                seen_ids.add(pair_id)
                unique_portfolios.append(p)

        if len(unique_portfolios) < len(portfolios):
            logger.warning(
                f"Deduplicated portfolios: {len(portfolios)} -> {len(unique_portfolios)}"
            )

        # Clear existing
        self.conn.execute("DELETE FROM portfolios")

        # Insert new
        self.conn.executemany(
            """
            INSERT INTO portfolios
            (portfolio_id, target_market_id, target_position, target_price,
             cover_market_id, cover_position, cover_price,
             total_cost, coverage, expected_profit, tier, tier_label,
             last_updated, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    p.get("pair_id", f"p_{i}"),
                    p["target_market_id"],
                    p["target_position"],
                    p.get("target_price", 0),
                    p["cover_market_id"],
                    p["cover_position"],
                    p.get("cover_price", 0),
                    p["total_cost"],
                    p["coverage"],
                    p["expected_profit"],
                    p["tier"],
                    p.get("tier_label", ""),
                    now,
                    json.dumps(p),
                )
                for i, p in enumerate(unique_portfolios)
            ],
        )
        self.conn.commit()

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
        groups_count = self.conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        implications_count = self.conn.execute(
            "SELECT COUNT(*) FROM implications"
        ).fetchone()[0]
        validated_pairs_count = self.conn.execute(
            "SELECT COUNT(*) FROM validated_pairs"
        ).fetchone()[0]
        portfolios_count = self.conn.execute(
            "SELECT COUNT(*) FROM portfolios"
        ).fetchone()[0]

        return StateStats(
            total_groups=groups_count,
            total_implications=implications_count,
            total_validated_pairs=validated_pairs_count,
            total_portfolios=portfolios_count,
            last_full_run=self.get_metadata("last_full_run"),
            last_refresh=self.get_metadata("last_refresh_run"),
        )

    def reset(self) -> None:
        """Reset all state (for full reprocessing)."""
        logger.warning("Resetting pipeline state...")

        # Clear all tables
        self.conn.executescript("""
            DELETE FROM metadata;
            DELETE FROM groups;
            DELETE FROM implications;
            DELETE FROM validated_pairs;
            DELETE FROM portfolios;
            DELETE FROM markets;
            DELETE FROM runs;
        """)
        self.conn.commit()

        # Clear caches
        self._processed_group_ids_cache = None

        # Remove _live files
        for path in [GROUPS_PATH, PORTFOLIOS_PATH]:
            if path.exists():
                path.unlink()
                logger.debug(f"Deleted {path.name}")

        logger.info("Pipeline state reset complete")

    # =========================================================================
    # SEED DATA MANAGEMENT
    # =========================================================================

    def _is_empty_db(self) -> bool:
        """Check if database has no groups (indicates fresh install)."""
        cursor = self.conn.execute("SELECT COUNT(*) FROM groups")
        return cursor.fetchone()[0] == 0

    def _import_seed_if_empty(self) -> None:
        """Auto-import seed data if database is empty and seed file exists."""
        if not self._is_empty_db():
            return

        if not SEED_DATA_PATH.exists():
            return

        logger.info("Empty database detected, importing seed data...")
        result = self.import_seed_data()
        if result["status"] == "imported":
            logger.info(
                f"Imported seed: {result['groups']} groups, "
                f"{result['implications']} implications, "
                f"{result['validated_pairs']} pairs"
            )

    def export_seed_data(self) -> dict:
        """
        Export current state to seed file for bootstrapping new installations.

        Exports: groups, implications, validated_pairs, markets
        Skips: portfolios (recalculated from prices), run history

        Returns:
            Dict with export statistics
        """
        logger.info("Exporting seed data...")

        # Fetch all tables
        groups = self.get_all_groups()
        implications = self.get_all_implications()
        validated_pairs = self.get_all_validated_pairs()

        # Get markets directly from DB
        cursor = self.conn.execute(
            """
            SELECT market_id, group_id, question, price_yes, price_no,
                   resolution_date, bracket_label
            FROM markets
            """
        )
        markets = [
            {
                "id": row[0],
                "group_id": row[1],
                "question": row[2],
                "price_yes": row[3],
                "price_no": row[4],
                "resolution_date": row[5],
                "bracket_label": row[6],
            }
            for row in cursor.fetchall()
        ]

        seed_data = {
            "_meta": {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "description": "Seed data for pipeline bootstrap",
                "counts": {
                    "groups": len(groups),
                    "implications": len(implications),
                    "validated_pairs": len(validated_pairs),
                    "markets": len(markets),
                },
            },
            "groups": groups,
            "implications": implications,
            "validated_pairs": validated_pairs,
            "markets": markets,
        }

        # Create seed directory and write file
        try:
            SEED_DIR.mkdir(parents=True, exist_ok=True)
            SEED_DATA_PATH.write_text(json.dumps(seed_data, indent=2))
        except OSError as e:
            logger.error(f"Failed to export seed data: {e}")
            return {"status": "error", "reason": f"write_failed: {e}"}

        result = {
            "status": "exported",
            "path": str(SEED_DATA_PATH),
            "groups": len(groups),
            "implications": len(implications),
            "validated_pairs": len(validated_pairs),
            "markets": len(markets),
        }

        logger.info(
            f"Exported seed: {len(groups)} groups, "
            f"{len(implications)} implications, "
            f"{len(validated_pairs)} pairs, "
            f"{len(markets)} markets → {SEED_DATA_PATH}"
        )

        return result

    def import_seed_data(self, force: bool = False) -> dict:
        """
        Import seed data from seed file.

        Args:
            force: If True, reset database before importing (for manual re-import)

        Returns:
            Dict with import statistics
        """
        if not SEED_DATA_PATH.exists():
            logger.warning(f"No seed file found at {SEED_DATA_PATH}")
            return {"status": "skipped", "reason": "no_seed_file"}

        if not force and not self._is_empty_db():
            logger.warning("Database not empty. Use force=True to reset and import.")
            return {"status": "skipped", "reason": "db_not_empty"}

        if force:
            logger.warning("Force import: resetting database...")
            self.reset()

        logger.info(f"Importing seed data from {SEED_DATA_PATH}...")

        # Load seed file
        try:
            seed_data = json.loads(SEED_DATA_PATH.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load seed data: {e}")
            return {"status": "error", "reason": f"invalid_seed_file: {e}"}

        # Import in order (respects foreign key relationships)
        groups = seed_data.get("groups", [])
        implications = seed_data.get("implications", [])
        validated_pairs = seed_data.get("validated_pairs", [])
        markets = seed_data.get("markets", [])

        if groups:
            self.add_groups(groups)

        if markets:
            self.add_markets(markets)

        if implications:
            # Use llm_model from first implication or "seed" as fallback
            llm_model = (
                implications[0].get("llm_model", "seed") if implications else "seed"
            )
            self.add_implications(implications, llm_model=llm_model)

        if validated_pairs:
            llm_model = (
                validated_pairs[0].get("llm_model", "seed")
                if validated_pairs
                else "seed"
            )
            self.add_validated_pairs(validated_pairs, llm_model=llm_model)

        result = {
            "status": "imported",
            "groups": len(groups),
            "implications": len(implications),
            "validated_pairs": len(validated_pairs),
            "markets": len(markets),
        }

        logger.info(
            f"Imported: {len(groups)} groups, "
            f"{len(implications)} implications, "
            f"{len(validated_pairs)} pairs, "
            f"{len(markets)} markets"
        )

        return result

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
    groups: list[dict],
    portfolios: list[dict],
) -> None:
    """
    Export data to _live/ directory for API consumption.

    Args:
        state: Pipeline state (for portfolios export method)
        groups: List of market groups
        portfolios: List of covering portfolios
    """
    export_timestamp = datetime.now(timezone.utc).isoformat()

    # Groups with metadata
    groups_data = {
        "_meta": {
            "exported_at": export_timestamp,
            "count": len(groups),
            "total_markets": sum(len(g.get("markets", [])) for g in groups),
            "source": "pipeline",
        },
        "groups": groups,
    }
    GROUPS_PATH.write_text(json.dumps(groups_data, indent=2))

    # Portfolios with metadata
    tier_counts = {}
    profitable_count = 0
    for p in portfolios:
        tier = p.get("tier", 4)
        tier_counts[f"tier_{tier}"] = tier_counts.get(f"tier_{tier}", 0) + 1
        if p.get("expected_profit", 0) > 0:
            profitable_count += 1

    portfolios_data = {
        "_meta": {
            "exported_at": export_timestamp,
            "count": len(portfolios),
            "by_tier": tier_counts,
            "profitable_count": profitable_count,
            "tier_thresholds": {
                "tier_1": ">=95% coverage",
                "tier_2": ">=90% coverage",
                "tier_3": ">=85% coverage",
                "tier_4": "<85% coverage",
            },
            "source": "pipeline",
        },
        "portfolios": portfolios,
    }
    PORTFOLIOS_PATH.write_text(json.dumps(portfolios_data, indent=2))

    logger.info(
        f"Exported to _live/: {len(groups)} groups, {len(portfolios)} portfolios "
        f"(timestamp: {export_timestamp})"
    )

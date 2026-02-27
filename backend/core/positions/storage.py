"""Position storage - JSON file with atomic writes and thread safety."""

import json
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from loguru import logger

from core.paths import LIVE_DIR

POSITIONS_FILE = LIVE_DIR / "positions.json"

# Global lock for thread-safe file operations
_storage_lock = threading.Lock()


@dataclass
class PositionEntry:
    """Position entry stored in JSON file."""

    position_id: str
    pair_id: str

    # Entry metadata
    entry_time: str  # ISO timestamp
    entry_amount_per_side: float
    entry_total_cost: float

    # Target market snapshot
    target_market_id: str
    target_position: str  # YES or NO
    target_token_id: str
    target_question: str
    target_entry_price: float

    # Cover market snapshot
    cover_market_id: str
    cover_position: str  # YES or NO
    cover_token_id: str
    cover_question: str
    cover_entry_price: float

    # Transaction records
    target_split_tx: str
    cover_split_tx: str

    # Optional fields with defaults
    target_group_slug: str = ""
    cover_group_slug: str = ""
    target_unwanted_token_id: str = ""
    cover_unwanted_token_id: str = ""
    target_ctf_token_ids: str = ""  # JSON array of actual on-chain CTF token IDs
    cover_ctf_token_ids: str = ""  # JSON array of actual on-chain CTF token IDs
    target_clob_order_id: Optional[str] = None
    cover_clob_order_id: Optional[str] = None
    target_clob_filled: bool = False
    cover_clob_filled: bool = False
    notes: Optional[str] = None
    selling_target: bool = False
    selling_cover: bool = False
    realized_proceeds: float = 0.0  # USDC recovered from exit sells/merges


class PositionStorage:
    """Manage positions.json file with atomic writes."""

    def __init__(self, path: Path = POSITIONS_FILE):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[dict]:
        """Load all positions from JSON file."""
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse positions.json: {e}")
            return []

    def save_all(self, positions: list[dict]) -> None:
        """Atomic write all positions to file."""
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(positions, indent=2))
        temp.replace(self.path)
        logger.debug(f"Saved {len(positions)} positions")

    def add(self, entry: PositionEntry) -> None:
        """Add new position entry (thread-safe)."""
        with _storage_lock:
            positions = self.load_all()
            positions.append(asdict(entry))
            self.save_all(positions)
            logger.info(f"Added position: {entry.position_id}")

    def get(self, position_id: str) -> Optional[dict]:
        """Get position by ID."""
        positions = self.load_all()
        for p in positions:
            if p.get("position_id") == position_id:
                return p
        return None

    def update_notes(self, position_id: str, notes: str) -> bool:
        """Update position notes (thread-safe). Returns True if found."""
        with _storage_lock:
            positions = self.load_all()
            for p in positions:
                if p.get("position_id") == position_id:
                    p["notes"] = notes
                    self.save_all(positions)
                    logger.info(f"Updated notes for position: {position_id}")
                    return True
            return False

    def update_clob_status(
        self,
        position_id: str,
        side: str,
        order_id: Optional[str],
        filled: bool,
    ) -> bool:
        """Update CLOB order status (thread-safe). Side is 'target' or 'cover'."""
        with _storage_lock:
            positions = self.load_all()
            for p in positions:
                if p.get("position_id") == position_id:
                    p[f"{side}_clob_order_id"] = order_id
                    p[f"{side}_clob_filled"] = filled
                    self.save_all(positions)
                    logger.info(f"Updated CLOB status for {position_id} ({side})")
                    return True
            return False

    def update_selling_status(
        self,
        position_id: str,
        side: str,
        selling: bool,
    ) -> bool:
        """Update selling status (thread-safe). Side is 'target' or 'cover'."""
        with _storage_lock:
            positions = self.load_all()
            for p in positions:
                if p.get("position_id") == position_id:
                    p[f"selling_{side}"] = selling
                    self.save_all(positions)
                    logger.debug(f"Selling {side}={selling} for {position_id}")
                    return True
            return False

    def add_realized_proceeds(
        self,
        position_id: str,
        amount: float,
    ) -> bool:
        """Add to realized proceeds (thread-safe). Returns True if found."""
        with _storage_lock:
            positions = self.load_all()
            for p in positions:
                if p.get("position_id") == position_id:
                    p["realized_proceeds"] = round(
                        p.get("realized_proceeds", 0.0) + amount, 4
                    )
                    self.save_all(positions)
                    logger.info(
                        f"Added ${amount:.4f} realized proceeds for {position_id} "
                        f"(total: ${p['realized_proceeds']:.4f})"
                    )
                    return True
            return False

    def delete(self, position_id: str) -> bool:
        """Delete position by ID (thread-safe). Returns True if found and deleted."""
        with _storage_lock:
            positions = self.load_all()
            filtered = [p for p in positions if p.get("position_id") != position_id]
            if len(filtered) < len(positions):
                self.save_all(filtered)
                logger.info(f"Deleted position: {position_id}")
                return True
            return False

    def count(self) -> int:
        """Get total position count."""
        return len(self.load_all())

"""
02: Build Market Groups from Raw Events.

WHAT IT DOES
    Organizes raw markets into logical "groups" - betting questions that
    share a common theme but differ by timeframe, threshold, or candidate.

    Example: "Fed rate cut by...?" is ONE group containing markets:
        - "...by March 2025"  (market 1)
        - "...by June 2025"   (market 2)
        - "...by December"    (market 3)

WHY WE NEED THIS
    Polymarket stores markets flat, but hedging requires understanding
    relationships. Markets in the SAME group are siblings (can't hedge
    each other). Markets in DIFFERENT groups might have logical relationships
    we can exploit for hedging.

HOW IT WORKS
    1. Group markets by event_id (Polymarket's grouping)
    2. Detect partition type by analyzing market question patterns:
       - "timeframe": differs by date ("by March" vs "by June")
       - "threshold": differs by number ("above 1M" vs "above 2M")
       - "candidate": differs by entity ("Trump wins" vs "Biden wins")
    3. Extract common title stem for LLM comparison later
    4. Skip single-market events (nothing to compare)

PIPELINE
    01_fetch_events → [02_build_groups] → 03a_extract_implications

INPUT
    data/01_fetch_events/<timestamp>/
        - events.json: Raw events with nested markets array

OUTPUT
    data/02_build_groups/<timestamp>/
        - groups.json  : Market groups with partition metadata
        - summary.json : Run stats (group counts by type)

RUNTIME
    <10 seconds (pure data transformation, no I/O)

CONFIGURATION
    - INPUT_RUN_FOLDER: Specific input timestamp (None = latest)
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
INPUT_SCRIPT_DIR = DATA_DIR / "01_fetch_events"
INPUT_RUN_FOLDER: str | None = None
SCRIPT_OUTPUT_DIR = DATA_DIR / "02_build_groups"

# Filter placeholder markets like "Person J"
FILTER_PLACEHOLDERS = True
PLACEHOLDER_PATTERN = re.compile(r"^Person [A-Z]$")

# Minimum markets for a group to be useful for covering
MIN_MARKETS_PER_GROUP = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class Market:
    """Individual market within a group."""

    id: str
    question: str
    slug: str
    bracket_label: str | None
    price_yes: float
    price_no: float
    liquidity: float
    volume: float
    resolution_date: str | None


@dataclass
class Group:
    """Market group representing an event with its outcome variants."""

    # Identity
    group_id: str  # Same as event_id
    title: str  # Event title - used for embedding
    slug: str

    # Metadata
    tags: list[str]
    resolution_date: str | None  # Earliest market resolution

    # Partition type (mutually exclusive)
    partition_type: str  # "candidate" | "threshold" | "timeframe" | "single"

    # Markets in this group
    markets: list[Market] = field(default_factory=list)

    # Aggregate stats
    total_liquidity: float = 0.0
    total_volume: float = 0.0
    sum_yes_prices: float = 0.0  # For quick arbitrage check

    # For embedding (cleaned title)
    embedding_text: str = ""


# =============================================================================
# EXTRACTION HELPERS
# =============================================================================


# Simplified patterns - no capture groups needed, only boolean detection
DATE_PATTERN = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}"
    r"|Q[1-4]\s*\d{4}"
    r"|^\d{4}$",
    re.IGNORECASE,
)

THRESHOLD_PATTERN = re.compile(
    r"[<>]?\$?\d+(?:\.\d+)?\s*[kmb]?"
    r"|\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*[kmb]?",
    re.IGNORECASE,
)


def has_date_bracket(label: str | None) -> bool:
    """Check if bracket label contains a date."""
    return bool(label and DATE_PATTERN.search(label))


def has_threshold_bracket(label: str | None) -> bool:
    """Check if bracket label contains a threshold/range (but not a date)."""
    return bool(
        label and not has_date_bracket(label) and THRESHOLD_PATTERN.search(label)
    )


def is_placeholder(market: dict) -> bool:
    """Check if market is a placeholder."""
    name = market.get("groupItemTitle") or ""
    return bool(PLACEHOLDER_PATTERN.match(str(name)))


def normalize_for_embedding(title: str) -> str:
    """Normalize event title for embedding."""
    text = title.lower()
    text = text.replace("...?", "").replace("...", "")
    text = re.sub(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s*\d{4}\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bq[1-4]\s*\d{4}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b20\d{2}\b", "", text)
    text = re.sub(r"\$?\d+(?:\.\d+)?[kmb]?\s*-\s*\$?\d+(?:\.\d+)?[kmb]?", "", text)
    text = re.sub(r"[<>]?\$?\d+(?:,\d+)*(?:\.\d+)?[kmb]?", "", text)
    text = re.sub(r"\d+(?:\.\d+)?%", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_partition_type(markets: list[dict]) -> str:
    """Detect what type of partition the markets form."""
    if len(markets) <= 1:
        return "single"

    bracket_labels = [m.get("groupItemTitle") for m in markets]

    # Check for date variation
    date_count = sum(1 for b in bracket_labels if has_date_bracket(b))
    if date_count > 1:
        return "timeframe"

    # Check for threshold variation
    threshold_count = sum(1 for b in bracket_labels if has_threshold_bracket(b))
    if threshold_count > 1:
        return "threshold"

    # Default: candidate partition (different options, same question type)
    return "candidate"


# =============================================================================
# GROUP BUILDING
# =============================================================================


def build_group_from_event(event: dict) -> Group | None:
    """Build a Group from a raw event."""
    raw_markets = event.get("markets", [])

    # Filter placeholders
    if FILTER_PLACEHOLDERS:
        raw_markets = [m for m in raw_markets if not is_placeholder(m)]

    if len(raw_markets) < MIN_MARKETS_PER_GROUP:
        return None

    event_id = str(event.get("id", ""))
    event_title = event.get("title", "")
    event_slug = event.get("slug", "")
    tags = [t.get("label") or t.get("slug", "") for t in event.get("tags", [])]

    # Detect partition type
    partition_type = detect_partition_type(raw_markets)

    # Build market list
    markets: list[Market] = []
    total_liquidity = 0.0
    total_volume = 0.0
    sum_yes_prices = 0.0
    earliest_resolution: str | None = None

    for m in raw_markets:
        prices = m.get("outcomePrices", [0, 0])
        if isinstance(prices, list) and len(prices) >= 2:
            price_yes, price_no = float(prices[0]), float(prices[1])
        else:
            price_yes, price_no = 0.0, 0.0

        liquidity = float(m.get("liquidityNum") or m.get("liquidity") or 0)
        volume = float(m.get("volumeNum") or m.get("volume") or 0)
        resolution = m.get("endDate")

        market = Market(
            id=str(m.get("id", "")),
            question=m.get("question", ""),
            slug=m.get("slug", ""),
            bracket_label=m.get("groupItemTitle"),
            price_yes=price_yes,
            price_no=price_no,
            liquidity=liquidity,
            volume=volume,
            resolution_date=resolution,
        )
        markets.append(market)

        total_liquidity += liquidity
        total_volume += volume
        sum_yes_prices += price_yes

        if resolution:
            if earliest_resolution is None or resolution < earliest_resolution:
                earliest_resolution = resolution

    return Group(
        group_id=event_id,
        title=event_title,
        slug=event_slug,
        tags=tags,
        resolution_date=earliest_resolution,
        partition_type=partition_type,
        markets=markets,
        total_liquidity=total_liquidity,
        total_volume=total_volume,
        sum_yes_prices=round(sum_yes_prices, 4),
        embedding_text=normalize_for_embedding(event_title),
    )


# =============================================================================
# MAIN
# =============================================================================


def get_input_folder() -> Path | None:
    """Get input folder (specified or latest)."""
    if INPUT_RUN_FOLDER:
        return INPUT_SCRIPT_DIR / INPUT_RUN_FOLDER
    if not INPUT_SCRIPT_DIR.exists():
        return None
    folders = [f for f in INPUT_SCRIPT_DIR.iterdir() if f.is_dir()]
    return max(folders, key=lambda f: f.stat().st_mtime) if folders else None


def main() -> None:
    """Main entry point."""
    logger.info("Building Market Groups from Events")

    input_folder = get_input_folder()
    if not input_folder or not input_folder.exists():
        logger.error(f"Input folder not found: {input_folder}")
        return

    events_file = input_folder / "events.json"
    if not events_file.exists():
        logger.error(f"Events file not found: {events_file}")
        return

    logger.info(f"Loading from: {events_file}")
    with open(events_file, encoding="utf-8") as f:
        events = json.load(f)
    logger.info(f"Loaded {len(events)} events")

    # Build groups
    groups: list[Group] = []
    skipped_single = 0
    partition_counts: dict[str, int] = {}

    for event in events:
        group = build_group_from_event(event)
        if group:
            groups.append(group)
            partition_counts[group.partition_type] = (
                partition_counts.get(group.partition_type, 0) + 1
            )
        else:
            skipped_single += 1

    logger.info(f"Built {len(groups)} groups (skipped {skipped_single} single-market)")

    # Prepare output
    input_run_timestamp = input_folder.name
    run_timestamp = datetime.now(timezone.utc).isoformat()
    output_folder = SCRIPT_OUTPUT_DIR / input_run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    # Save groups
    groups_output = {
        "_meta": {
            "source": str(events_file),
            "input_run": input_run_timestamp,
            "created_at": run_timestamp,
            "description": "Market groups with partition structure for embedding",
        },
        "groups": [asdict(g) for g in groups],
    }

    groups_file = output_folder / "groups.json"
    with open(groups_file, "w", encoding="utf-8") as f:
        json.dump(groups_output, f, indent=2, ensure_ascii=False)

    # Compute unique embedding texts
    unique_texts = len({g.embedding_text for g in groups})

    # Save summary
    total_markets = sum(len(g.markets) for g in groups)
    summary = {
        "script": "02_build_groups",
        "run_at": run_timestamp,
        "input": {
            "script": "01_fetch_events",
            "run": input_run_timestamp,
            "file": str(events_file),
            "events_count": len(events),
        },
        "output": {
            "folder": str(output_folder),
            "groups_count": len(groups),
            "total_markets": total_markets,
            "unique_embedding_texts": unique_texts,
        },
        "stats": {
            "skipped_single_market": skipped_single,
            "partition_types": partition_counts,
            "avg_markets_per_group": round(total_markets / len(groups), 1)
            if groups
            else 0,
        },
    }

    summary_file = output_folder / "summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved {len(groups)} groups to: {groups_file}")
    logger.info(f"Partition types: {partition_counts}")
    logger.info(f"Unique embedding texts: {unique_texts}")

    # Print sample
    logger.info("=" * 55)
    logger.info("Sample groups:")
    for g in groups[:3]:
        logger.info(f"  {g.group_id}: {g.title}")
        logger.info(f"    Type: {g.partition_type}, Markets: {len(g.markets)}")


if __name__ == "__main__":
    main()

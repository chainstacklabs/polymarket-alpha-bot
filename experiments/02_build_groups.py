"""
02: Build Market Groups from Raw Events.

WHAT IT DOES
    Turns each Polymarket event into a "group": one betting question whose
    markets differ by date, threshold or candidate. Mirrors
    backend/core/steps/groups.py; groups.json equals production
    build_groups(events) output for the same events.

    Example: "Fed rate cut by...?" is ONE group with markets
    "...by March", "...by June", "...by December".

WHY WE NEED THIS
    Markets in the SAME group are siblings (they can't hedge each other);
    markets in DIFFERENT groups may have logical links the next steps exploit.

HOW IT WORKS
    1. One group per event; drop placeholder markets ("Person J")
    2. Keep events with >= MIN_MARKETS_PER_GROUP markets (1: single-market
       events can still be hedge legs)
    3. Partition type from bracket labels (groupItemTitle):
       "timeframe" (2+ dates), "threshold" (2+ numbers), "candidate", "single"
    4. Resolution date per market: date in the label, then in the
       description, then label date + inferred year, then API endDate.
       Group resolution_date = earliest market date
    5. embedding_text = title with dates/numbers stripped

INPUT
    data/01_fetch_events/<run_timestamp>/events.json (latest run, or --run)

OUTPUT
    data/02_build_groups/<run_timestamp>/
        - groups.json  : List of group dicts (same as production)
        - summary.json : Counts by partition type

USAGE
    uv run --frozen --project backend experiments/02_build_groups.py [--run <timestamp>]
"""

import argparse
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
SCRIPT_OUTPUT_DIR = DATA_DIR / "02_build_groups"

# Filter placeholder markets like "Person J"
FILTER_PLACEHOLDERS = True
PLACEHOLDER_PATTERN = re.compile(r"^Person [A-Z]$")

# Single-market events are valid hedge participants (Polymarket sometimes
# splits timeframe brackets into separate single-market events)
MIN_MARKETS_PER_GROUP = 1

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
    group_id: str = ""


@dataclass
class Group:
    """Market group representing an event with its outcome variants."""

    group_id: str  # Same as event_id
    title: str
    slug: str
    tags: list[str]
    resolution_date: str | None  # Earliest market resolution
    partition_type: str  # "candidate" | "threshold" | "timeframe" | "single"
    markets: list[Market] = field(default_factory=list)
    total_liquidity: float = 0.0
    total_volume: float = 0.0
    sum_yes_prices: float = 0.0  # Quick arbitrage check
    embedding_text: str = ""  # Cleaned title


# =============================================================================
# PARTITION DETECTION
# =============================================================================

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"

DATE_PATTERN = re.compile(
    rf"(?:{_MONTHS})\s+\d{{1,2}},?\s*\d{{4}}|Q[1-4]\s*\d{{4}}|^\d{{4}}$",
    re.IGNORECASE,
)

THRESHOLD_PATTERN = re.compile(
    r"[<>]?\$?\d+(?:\.\d+)?\s*[kmb]?|\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*[kmb]?",
    re.IGNORECASE,
)


def has_date_bracket(label: str | None) -> bool:
    """Check if bracket label contains a date."""
    return bool(label and DATE_PATTERN.search(label))


def has_threshold_bracket(label: str | None) -> bool:
    """Check if bracket label contains a threshold/range (but not a date)."""
    return bool(label and not has_date_bracket(label) and THRESHOLD_PATTERN.search(label))


def is_placeholder(market: dict) -> bool:
    """Check if market is a placeholder."""
    name = market.get("groupItemTitle") or ""
    return bool(PLACEHOLDER_PATTERN.match(str(name)))


def detect_partition_type(markets: list[dict]) -> str:
    """Detect what type of partition the markets form."""
    if len(markets) <= 1:
        return "single"
    bracket_labels = [m.get("groupItemTitle") for m in markets]
    if sum(1 for b in bracket_labels if has_date_bracket(b)) > 1:
        return "timeframe"
    if sum(1 for b in bracket_labels if has_threshold_bracket(b)) > 1:
        return "threshold"
    return "candidate"


# =============================================================================
# RESOLUTION DATE EXTRACTION
# =============================================================================

MONTH_MAP = {m.lower(): i for i, m in enumerate(_MONTHS.split("|"), start=1)}

# "June 30, 2026"
FULL_DATE_PATTERN = re.compile(rf"({_MONTHS})\s+(\d{{1,2}}),?\s*(\d{{4}})", re.IGNORECASE)
# "June 30" (no year)
PARTIAL_DATE_PATTERN = re.compile(rf"({_MONTHS})\s+(\d{{1,2}})(?:,|\s|$)", re.IGNORECASE)
# "by June 30, 2026, 11:59 PM ET" in descriptions
DESCRIPTION_DATE_PATTERN = re.compile(
    rf"(?:by|and)\s+({_MONTHS})\s+(\d{{1,2}}),?\s*(\d{{4}})", re.IGNORECASE
)


def parse_date_string(month_str: str, day: int, year: int) -> str | None:
    """Date parts → "YYYY-MM-DDT23:59:59Z", or None if invalid."""
    month = MONTH_MAP.get(month_str.lower())
    if not month:
        return None
    try:
        return datetime(year, month, day, 23, 59, 59).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def extract_resolution_date(market: dict, event_end_date: str | None = None) -> str | None:
    """Resolution date from label, description, label + inferred year, or endDate."""
    bracket_label = market.get("groupItemTitle", "")
    description = market.get("description", "")
    api_end_date = market.get("endDate")

    # 1. Full date in bracket label
    if bracket_label:
        match = FULL_DATE_PATTERN.search(bracket_label)
        if match:
            month_str, day_str, year_str = match.groups()
            parsed = parse_date_string(month_str, int(day_str), int(year_str))
            if parsed:
                return parsed

    # 2. Explicit date in description
    if description:
        match = DESCRIPTION_DATE_PATTERN.search(description)
        if match:
            month_str, day_str, year_str = match.groups()
            parsed = parse_date_string(month_str, int(day_str), int(year_str))
            if parsed:
                return parsed

    # 3. Partial date in bracket label, year from event/market endDate
    if bracket_label:
        match = PARTIAL_DATE_PATTERN.search(bracket_label)
        if match:
            month_str, day_str = match.groups()
            month = MONTH_MAP.get(month_str.lower())
            inferred_year = None
            for date_source in [event_end_date, api_end_date]:
                if date_source and len(date_source) >= 4:
                    try:
                        inferred_year = int(date_source[:4])
                        break
                    except ValueError:
                        pass
            if inferred_year and month:
                parsed = parse_date_string(month_str, int(day_str), inferred_year)
                if parsed:
                    return parsed

    # 4. API endDate
    return api_end_date


def normalize_for_embedding(title: str) -> str:
    """Strip dates, numbers and thresholds to keep the core question."""
    text = title.lower()
    text = text.replace("...?", "").replace("...", "")
    text = re.sub(
        rf"\b({_MONTHS.lower()})\s+\d{{1,2}},?\s*\d{{4}}\b", "", text, flags=re.IGNORECASE
    )
    text = re.sub(r"\bq[1-4]\s*\d{4}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b20\d{2}\b", "", text)
    text = re.sub(r"\$?\d+(?:\.\d+)?[kmb]?\s*-\s*\$?\d+(?:\.\d+)?[kmb]?", "", text)
    text = re.sub(r"[<>]?\$?\d+(?:,\d+)*(?:\.\d+)?[kmb]?", "", text)
    text = re.sub(r"\d+(?:\.\d+)?%", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# =============================================================================
# GROUP BUILDING
# =============================================================================


def build_group_from_event(event: dict) -> Group | None:
    """Build a Group from a raw event, or None if it has too few markets."""
    raw_markets = event.get("markets", [])
    if FILTER_PLACEHOLDERS:
        raw_markets = [m for m in raw_markets if not is_placeholder(m)]
    if len(raw_markets) < MIN_MARKETS_PER_GROUP:
        return None

    event_id = str(event.get("id", ""))
    event_title = event.get("title", "")
    event_end_date = event.get("endDate")  # For year inference
    tags = [t.get("label") or t.get("slug", "") for t in event.get("tags", [])]

    markets: list[Market] = []
    total_liquidity = total_volume = sum_yes_prices = 0.0
    earliest_resolution: str | None = None

    for m in raw_markets:
        prices = m.get("outcomePrices", [0, 0])
        if isinstance(prices, list) and len(prices) >= 2:
            price_yes, price_no = float(prices[0]), float(prices[1])
        else:
            price_yes, price_no = 0.0, 0.0

        liquidity = float(m.get("liquidityNum") or m.get("liquidity") or 0)
        volume = float(m.get("volumeNum") or m.get("volume") or 0)
        resolution = extract_resolution_date(m, event_end_date)

        markets.append(
            Market(
                id=str(m.get("id", "")),
                question=m.get("question", ""),
                slug=m.get("slug", ""),
                bracket_label=m.get("groupItemTitle"),
                price_yes=price_yes,
                price_no=price_no,
                liquidity=liquidity,
                volume=volume,
                resolution_date=resolution,
                group_id=event_id,
            )
        )
        total_liquidity += liquidity
        total_volume += volume
        sum_yes_prices += price_yes
        if resolution and (earliest_resolution is None or resolution < earliest_resolution):
            earliest_resolution = resolution

    return Group(
        group_id=event_id,
        title=event_title,
        slug=event.get("slug", ""),
        tags=tags,
        resolution_date=earliest_resolution,
        partition_type=detect_partition_type(raw_markets),
        markets=markets,
        total_liquidity=total_liquidity,
        total_volume=total_volume,
        sum_yes_prices=round(sum_yes_prices, 4),
        embedding_text=normalize_for_embedding(event_title),
    )


def build_groups(events: list[dict]) -> tuple[list[dict], dict]:
    """Build groups from raw events → (group dicts, summary stats)."""
    groups: list[Group] = []
    skipped = 0
    partition_counts: dict[str, int] = {}

    for event in events:
        group = build_group_from_event(event)
        if group:
            groups.append(group)
            partition_counts[group.partition_type] = (
                partition_counts.get(group.partition_type, 0) + 1
            )
        else:
            skipped += 1

    total_markets = sum(len(g.markets) for g in groups)
    summary = {
        "groups_count": len(groups),
        "total_markets": total_markets,
        "unique_embedding_texts": len({g.embedding_text for g in groups}),
        "skipped_single_market": skipped,  # production key name; = events skipped
        "partition_types": partition_counts,
        "avg_markets_per_group": round(total_markets / len(groups), 1) if groups else 0,
    }
    return [asdict(g) for g in groups], summary


# =============================================================================
# MAIN
# =============================================================================


def get_input_folder(run: str | None) -> Path | None:
    """Input folder for --run, or the latest run (timestamps sort by name)."""
    if run:
        return INPUT_SCRIPT_DIR / run
    if not INPUT_SCRIPT_DIR.exists():
        return None
    folders = [f for f in INPUT_SCRIPT_DIR.iterdir() if f.is_dir()]
    return max(folders, key=lambda f: f.name) if folders else None


def main(run: str | None) -> None:
    input_folder = get_input_folder(run)
    events_file = input_folder / "events.json" if input_folder else None
    if not events_file or not events_file.exists():
        logger.error(f"Events file not found: {events_file}")
        return

    events = json.loads(events_file.read_text(encoding="utf-8"))
    logger.info(f"Loaded {len(events)} events from {events_file}")

    groups, stats = build_groups(events)
    logger.info(f"Built {len(groups)} groups (skipped {stats['skipped_single_market']})")

    run_timestamp = input_folder.name
    output_folder = SCRIPT_OUTPUT_DIR / run_timestamp
    output_folder.mkdir(parents=True, exist_ok=True)

    groups_file = output_folder / "groups.json"
    groups_file.write_text(json.dumps(groups, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "script": "02_build_groups",
        "run_timestamp": run_timestamp,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {"file": str(events_file), "events_count": len(events)},
        **stats,
    }
    (output_folder / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    logger.info(f"Saved {len(groups)} groups to: {groups_file}")
    logger.info(f"Partition types: {stats['partition_types']}")
    for g in groups[:3]:
        logger.info(
            f"  {g['group_id']}: {g['title']} ({g['partition_type']}, {len(g['markets'])} markets)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build market groups from events")
    parser.add_argument("--run", help="Input run timestamp (default: latest)")
    main(parser.parse_args().run)

"""
Build market groups from raw events.

Organizes raw markets into logical "groups" - betting questions that
share a common theme but differ by timeframe, threshold, or candidate.

Example: "Fed rate cut by...?" is ONE group containing markets:
    - "...by March 2025"  (market 1)
    - "...by June 2025"   (market 2)
    - "...by December"    (market 3)

Why we need this:
    Polymarket stores markets flat, but hedging requires understanding
    relationships. Markets in the SAME group are siblings (can't hedge
    each other). Markets in DIFFERENT groups might have logical relationships
    we can exploit for hedging.

Partition types:
    - "timeframe": differs by date ("by March" vs "by June")
    - "threshold": differs by number ("above 1M" vs "above 2M")
    - "candidate": differs by entity ("Trump wins" vs "Biden wins")
    - "single": only one market (skipped)
"""

import re
from dataclasses import asdict, dataclass, field

from loguru import logger

# =============================================================================
# CONFIGURATION
# =============================================================================

# Filter placeholder markets like "Person J"
FILTER_PLACEHOLDERS = True
PLACEHOLDER_PATTERN = re.compile(r"^Person [A-Z]$")

# Minimum markets for a group to be useful for covering
MIN_MARKETS_PER_GROUP = 2


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
    group_id: str = ""  # Added for state management


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
    """
    Normalize event title for embedding comparison.

    Removes dates, numbers, thresholds to get the core semantic meaning.
    """
    text = title.lower()
    text = text.replace("...?", "").replace("...", "")

    # Remove dates
    text = re.sub(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s*\d{4}\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bq[1-4]\s*\d{4}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b20\d{2}\b", "", text)

    # Remove thresholds and numbers
    text = re.sub(r"\$?\d+(?:\.\d+)?[kmb]?\s*-\s*\$?\d+(?:\.\d+)?[kmb]?", "", text)
    text = re.sub(r"[<>]?\$?\d+(?:,\d+)*(?:\.\d+)?[kmb]?", "", text)
    text = re.sub(r"\d+(?:\.\d+)?%", "", text)

    # Clean up
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
    """
    Build a Group from a raw event.

    Args:
        event: Raw event dict from Polymarket API

    Returns:
        Group object or None if not enough markets
    """
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
            group_id=event_id,
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


def build_groups(events: list[dict]) -> tuple[list[dict], dict]:
    """
    Build groups from raw events.

    Args:
        events: List of raw events from Polymarket API

    Returns:
        Tuple of (groups as dicts, summary stats)
    """
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

    # Convert to dicts for storage
    groups_data = [asdict(g) for g in groups]

    # Build summary
    total_markets = sum(len(g.markets) for g in groups)
    unique_texts = len({g.embedding_text for g in groups})

    summary = {
        "groups_count": len(groups),
        "total_markets": total_markets,
        "unique_embedding_texts": unique_texts,
        "skipped_single_market": skipped_single,
        "partition_types": partition_counts,
        "avg_markets_per_group": round(total_markets / len(groups), 1) if groups else 0,
    }

    return groups_data, summary


def extract_markets_from_groups(groups: list[dict]) -> list[dict]:
    """
    Extract flat list of markets from groups for state storage.

    Args:
        groups: List of group dicts

    Returns:
        List of market dicts with group_id added
    """
    markets = []
    for group in groups:
        group_id = group["group_id"]
        for market in group.get("markets", []):
            market_dict = {
                "id": market["id"],
                "group_id": group_id,
                "question": market.get("question", ""),
                "slug": market.get("slug", ""),
                "bracket_label": market.get("bracket_label"),
                "price_yes": market.get("price_yes", 0.5),
                "price_no": market.get("price_no", 0.5),
                "resolution_date": market.get("resolution_date"),
            }
            markets.append(market_dict)
    return markets

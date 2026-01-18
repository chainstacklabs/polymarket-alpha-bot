"""
Expand group implications to market-level pairs.

This module explodes group-level relationships into ALL possible market
combinations. Each group has multiple markets (different deadlines), so
one group relationship becomes N × M market pairs.

Key features:
- Two-way expansion for incremental processing:
  - new_target × all_covers
  - all_targets × new_covers
- Pure cartesian product expansion (no filtering)
- Attaches prices, deadlines, and confidence scores

Example:
    Groups "Election called by...?" and "Election held by...?"
    Each has 4 markets (March, June, Sept, Dec). One implication becomes
    4 × 4 = 16 candidate pairs to evaluate.

Note:
    This step generates ALL combinations. The validate.py step will filter
    invalid ones based on timing and logic.
"""

import hashlib
from typing import Literal

from loguru import logger

from core.state import PipelineState

# =============================================================================
# CONFIGURATION
# =============================================================================

# Minimum probability threshold for covers to include
# 0.88 keeps only "necessary" (0.98), filters out lower confidence relationships
MIN_COVER_PROBABILITY = 0.88


# =============================================================================
# MUTUALLY EXCLUSIVE GROUP DETECTION
# =============================================================================


def is_mutually_exclusive_group(group: dict) -> bool:
    """
    Detect if a group has mutually exclusive outcomes (vs temporal progression).

    Mutually exclusive groups have multiple markets with the SAME resolution date
    but different brackets (e.g., "Presidential Election Winner" with 30 candidates).

    For these groups, betting NO on one bracket (Dimon loses) does NOT mean
    betting NO on the group outcome (no winner exists). Someone else wins.

    Temporal groups have different resolution dates (e.g., "Captured by March/June/Sept").
    For these, NO on a later date CAN validly relate to group-level implications.

    Returns:
        True if group has mutually exclusive outcomes (skip NO expansion)
        False if temporal or single-market group (NO expansion may be valid)
    """
    markets = group.get("markets", [])

    if len(markets) <= 1:
        return False  # Single market - NO on market = NO on group

    # Count markets per resolution date
    resolution_dates = {}
    for m in markets:
        res_date = m.get("resolution_date", "unknown")
        resolution_dates[res_date] = resolution_dates.get(res_date, 0) + 1

    # If multiple markets share any resolution date, they're mutually exclusive
    # (e.g., 30 candidates all resolving on election day)
    max_same_date = max(resolution_dates.values()) if resolution_dates else 0

    return max_same_date > 1


# =============================================================================
# PAIR ID GENERATION
# =============================================================================


def generate_pair_id(
    target_market_id: str,
    target_position: Literal["YES", "NO"],
    cover_market_id: str,
    cover_position: Literal["YES", "NO"],
) -> str:
    """
    Generate unique pair ID for caching.

    Uses hash of components to ensure uniqueness.
    """
    components = (
        f"{target_market_id}:{target_position}:{cover_market_id}:{cover_position}"
    )
    return hashlib.md5(components.encode()).hexdigest()[:16]


# =============================================================================
# EXPANSION HELPERS
# =============================================================================


def get_cover_markets(
    cover_info: dict,
    groups_by_id: dict[str, dict],
    target_group_id: str,
) -> list[dict]:
    """
    Get all markets from a covering group.

    Returns list of cover market dicts with attached metadata.
    """
    if cover_info["group_id"] == target_group_id:
        return []  # Skip self-references

    cover_group = groups_by_id.get(cover_info["group_id"])
    if not cover_group:
        return []

    probability = cover_info.get("probability", 0.0)
    if probability < MIN_COVER_PROBABILITY:
        return []

    cover_position = cover_info.get("cover_position", "YES")
    relationship = cover_info.get("relationship", "")
    relationship_type = cover_info.get("relationship_type", "causal")

    markets = []
    for m in cover_group.get("markets", []):
        markets.append(
            {
                "market_id": m["id"],
                "market_slug": m.get("slug", ""),
                "question": m.get("question", ""),
                "resolution_date": m.get("resolution_date"),
                "price_yes": m.get("price_yes", 0.5),
                "price_no": m.get("price_no", 0.5),
                "bracket_label": m.get("bracket_label"),
                "source_group_id": cover_info["group_id"],
                "source_group_title": cover_info.get("title", ""),
                "source_group_slug": cover_group.get("slug", ""),
                "cover_position": cover_position,
                "relationship": relationship,
                "relationship_type": relationship_type,
                "probability": probability,
            }
        )
    return markets


def expand_implication_to_pairs(
    impl: dict,
    groups_by_id: dict[str, dict],
) -> list[dict]:
    """
    Expand a single group implication to market-level candidate pairs.

    Returns list of expanded pairs ready for validation.
    """
    target_group_id = impl["group_id"]
    target_group = groups_by_id.get(target_group_id)

    if not target_group:
        return []

    target_title = impl.get("title", target_group.get("title", ""))

    # Collect all covering markets
    yes_covering_groups = impl.get("yes_covered_by", [])
    no_covering_groups = impl.get("no_covered_by", [])

    # Check if this is a mutually exclusive group (skip NO expansion if so)
    skip_no_expansion = is_mutually_exclusive_group(target_group)
    if skip_no_expansion and no_covering_groups:
        logger.debug(
            f"Skipping NO expansion for mutually exclusive group: {target_title}"
        )

    pairs = []

    target_group_slug = target_group.get("slug", "")

    for target_market in target_group.get("markets", []):
        target_market_id = target_market["id"]
        target_market_slug = target_market.get("slug", "")
        target_question = target_market.get("question", "")
        target_resolution = target_market.get("resolution_date")
        target_price_yes = target_market.get("price_yes", 0.5)
        target_price_no = target_market.get("price_no", 0.5)
        target_bracket = target_market.get("bracket_label")

        # Expand YES covers (for target_YES position)
        for cover_info in yes_covering_groups:
            cover_markets = get_cover_markets(cover_info, groups_by_id, target_group_id)
            for cm in cover_markets:
                pair_id = generate_pair_id(
                    target_market_id, "YES", cm["market_id"], cm["cover_position"]
                )
                pairs.append(
                    {
                        "pair_id": pair_id,
                        "target_group_id": target_group_id,
                        "target_group_title": target_title,
                        "target_group_slug": target_group_slug,
                        "target_market_id": target_market_id,
                        "target_market_slug": target_market_slug,
                        "target_question": target_question,
                        "target_position": "YES",
                        "target_resolution": target_resolution,
                        "target_price": target_price_yes,
                        "target_bracket": target_bracket,
                        "cover_group_id": cm["source_group_id"],
                        "cover_group_title": cm["source_group_title"],
                        "cover_group_slug": cm["source_group_slug"],
                        "cover_market_id": cm["market_id"],
                        "cover_market_slug": cm["market_slug"],
                        "cover_question": cm["question"],
                        "cover_position": cm["cover_position"],
                        "cover_resolution": cm["resolution_date"],
                        "cover_price_yes": cm["price_yes"],
                        "cover_price_no": cm["price_no"],
                        "cover_bracket": cm["bracket_label"],
                        "cover_probability": cm["probability"],
                        "relationship": cm["relationship"],
                        "relationship_type": cm["relationship_type"],
                    }
                )

        # Expand NO covers (for target_NO position)
        # Skip for mutually exclusive groups where NO on one bracket
        # doesn't mean NO on the group outcome (e.g., candidates)
        if not skip_no_expansion:
            for cover_info in no_covering_groups:
                cover_markets = get_cover_markets(
                    cover_info, groups_by_id, target_group_id
                )
                for cm in cover_markets:
                    pair_id = generate_pair_id(
                        target_market_id, "NO", cm["market_id"], cm["cover_position"]
                    )
                    pairs.append(
                        {
                            "pair_id": pair_id,
                            "target_group_id": target_group_id,
                            "target_group_title": target_title,
                            "target_group_slug": target_group_slug,
                            "target_market_id": target_market_id,
                            "target_market_slug": target_market_slug,
                            "target_question": target_question,
                            "target_position": "NO",
                            "target_resolution": target_resolution,
                            "target_price": target_price_no,
                            "target_bracket": target_bracket,
                            "cover_group_id": cm["source_group_id"],
                            "cover_group_title": cm["source_group_title"],
                            "cover_group_slug": cm["source_group_slug"],
                            "cover_market_id": cm["market_id"],
                            "cover_market_slug": cm["market_slug"],
                            "cover_question": cm["question"],
                            "cover_position": cm["cover_position"],
                            "cover_resolution": cm["resolution_date"],
                            "cover_price_yes": cm["price_yes"],
                            "cover_price_no": cm["price_no"],
                            "cover_bracket": cm["bracket_label"],
                            "cover_probability": cm["probability"],
                            "relationship": cm["relationship"],
                            "relationship_type": cm["relationship_type"],
                        }
                    )

    return pairs


# =============================================================================
# MAIN EXPANSION (TWO-WAY INCREMENTAL)
# =============================================================================


def expand_to_pairs(
    implications: list[dict],
    groups: list[dict],
    state: PipelineState,
    new_group_ids: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """
    Expand implications to market-level pairs.

    Supports two-way incremental processing:
    - If new_group_ids is provided, only expands pairs involving new groups
    - If new_group_ids is None, expands all pairs

    Args:
        implications: List of implications with covers
        groups: List of all groups with markets
        state: Pipeline state (for checking validated pairs)
        new_group_ids: Optional set of new group IDs for incremental processing

    Returns:
        Tuple of (candidate_pairs, summary_stats)
    """
    groups_by_id = {g["group_id"]: g for g in groups}

    all_pairs = []
    new_pairs = []
    cached_pairs = []

    # Determine which pairs to generate based on new groups
    if new_group_ids:
        logger.info(
            f"Two-way incremental expansion for {len(new_group_ids)} new groups"
        )

        # Strategy 1: new targets × all covers
        for impl in implications:
            if impl["group_id"] in new_group_ids:
                pairs = expand_implication_to_pairs(impl, groups_by_id)
                for p in pairs:
                    if state.is_pair_validated(p["pair_id"]):
                        cached_pairs.append(p)
                    else:
                        new_pairs.append(p)
                        all_pairs.append(p)

        # Strategy 2: existing targets × new covers
        for impl in implications:
            if impl["group_id"] not in new_group_ids:
                # Check if this target has any covers from new groups
                yes_covers = impl.get("yes_covered_by", [])
                no_covers = impl.get("no_covered_by", [])

                has_new_cover = False
                for cov in yes_covers + no_covers:
                    if cov["group_id"] in new_group_ids:
                        has_new_cover = True
                        break

                if has_new_cover:
                    pairs = expand_implication_to_pairs(impl, groups_by_id)
                    for p in pairs:
                        # Only include pairs where cover is from new group
                        if p["cover_group_id"] in new_group_ids:
                            if state.is_pair_validated(p["pair_id"]):
                                cached_pairs.append(p)
                            else:
                                new_pairs.append(p)
                                all_pairs.append(p)
    else:
        # Full expansion
        logger.info("Full expansion for all groups")
        for impl in implications:
            pairs = expand_implication_to_pairs(impl, groups_by_id)
            for p in pairs:
                if state.is_pair_validated(p["pair_id"]):
                    cached_pairs.append(p)
                else:
                    new_pairs.append(p)
                    all_pairs.append(p)

    # Compute stats (compute set once for efficiency and safety)
    yes_pairs = len([p for p in all_pairs if p["target_position"] == "YES"])
    no_pairs = len([p for p in all_pairs if p["target_position"] == "NO"])
    unique_groups = set(p["target_group_id"] for p in all_pairs)
    num_groups = len(unique_groups)

    summary = {
        "total_pairs": len(all_pairs),
        "new_pairs": len(new_pairs),
        "cached_pairs": len(cached_pairs),
        "yes_position_pairs": yes_pairs,
        "no_position_pairs": no_pairs,
        "groups_with_pairs": num_groups,
        "avg_pairs_per_group": round(len(all_pairs) / num_groups, 2)
        if num_groups > 0
        else 0,
    }

    logger.info(
        f"Expanded to {len(all_pairs)} pairs "
        f"({len(new_pairs)} new, {len(cached_pairs)} cached)"
    )

    return new_pairs, summary


def expand_all_to_pairs(
    implications: list[dict],
    groups: list[dict],
) -> tuple[list[dict], dict]:
    """
    Simple full expansion without caching check.

    Used for initial run or when cache is not needed.

    Args:
        implications: List of implications with covers
        groups: List of all groups with markets

    Returns:
        Tuple of (candidate_pairs, summary_stats)
    """
    groups_by_id = {g["group_id"]: g for g in groups}

    all_pairs = []

    for impl in implications:
        pairs = expand_implication_to_pairs(impl, groups_by_id)
        all_pairs.extend(pairs)

    # Compute stats (compute set once for efficiency and safety)
    yes_pairs = len([p for p in all_pairs if p["target_position"] == "YES"])
    no_pairs = len([p for p in all_pairs if p["target_position"] == "NO"])
    unique_groups = set(p["target_group_id"] for p in all_pairs)
    num_groups = len(unique_groups)

    summary = {
        "total_pairs": len(all_pairs),
        "yes_position_pairs": yes_pairs,
        "no_position_pairs": no_pairs,
        "groups_with_pairs": num_groups,
        "avg_pairs_per_group": round(len(all_pairs) / num_groups, 2)
        if num_groups > 0
        else 0,
    }

    logger.info(f"Expanded to {len(all_pairs)} pairs")

    return all_pairs, summary

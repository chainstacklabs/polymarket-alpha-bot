"""
04: Expand Group Implications into Market-Level Pairs.

WHAT IT DOES
    Turns each group-level cover from step 03 into concrete market pairs:
    (target market, target position) + (cover market, cover position).

    Example: "Election called by...?" covers "Election held by...?". Each group
    has 4 deadlines, so one implication becomes up to 4 × 4 = 16 pairs.

WHY WE NEED THIS
    Trades happen on markets, not groups. Step 05 checks each pair's deadlines
    and logic, so this step lists every candidate pair, minus the ones that can
    never be a sound hedge.

HOW IT WORKS (mirrors expand_all_to_pairs in backend/core/steps/expand.py)
    1. Target YES pairs use yes_covered_by; target NO pairs use no_covered_by.
    2. Drop covers below MIN_COVER_PROBABILITY (0.88 keeps only "necessary").
    3. Skip negRisk-exclusive groups (candidate group, YES prices sum ~1) as target
       AND as cover: one outcome can lose to a sibling, leaving the hedge open.
    4. Skip NO expansion when the target's markets share a resolution date
       (mutually exclusive outcomes: NO on one candidate ≠ NO on the event).
    5. bracket_alignment == "match": keep only cover markets whose bracket names
       the same entity as the target's (accents and "(DC)"-style suffixes ignored).
    6. pair_id = md5("target:pos:cover:pos")[:16]; attach slugs, prices, fees.
    Production's incremental mode also skips pairs already validated (cached);
    this script always does the full expansion.

INPUT
    data/02_build_groups/<run>/groups.json
    data/03_extract_implications/<run>/implications.json  (latest, or --run <run>)

OUTPUT
    data/04_expand_pairs/<run>/
        - pairs.json   : flat pair dicts, exactly what expand_all_to_pairs returns
        - summary.json : its summary stats

USAGE
    uv run --frozen --project backend experiments/04_expand_pairs.py [--run <run>]
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
GROUPS_DIR = DATA_DIR / "02_build_groups"
IMPLICATIONS_DIR = DATA_DIR / "03_extract_implications"
OUTPUT_DIR = DATA_DIR / "04_expand_pairs"

# 0.88 keeps only "necessary" covers (0.98)
MIN_COVER_PROBABILITY = 0.88

# A candidate group whose YES prices sum to ~1 is true negRisk (exactly one YES)
NEGRISK_PRICE_SUM_MIN = 0.85
NEGRISK_PRICE_SUM_MAX = 1.15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# GROUP CHECKS
# =============================================================================


def is_negrisk_exclusive(group: dict) -> bool:
    """Candidate group with >1 market whose YES prices sum to ~1 (exactly one wins).

    Multi-select candidate groups ("Which cities will Russia enter?") sum far from 1.
    """
    if group.get("partition_type") != "candidate":
        return False
    markets = group.get("markets", [])
    if len(markets) <= 1:
        return False
    price_sum = sum(m.get("price_yes", 0.0) for m in markets)
    return NEGRISK_PRICE_SUM_MIN <= price_sum <= NEGRISK_PRICE_SUM_MAX


def is_mutually_exclusive_group(group: dict) -> bool:
    """True if 2+ markets share a resolution date (candidates), not a deadline ladder."""
    markets = group.get("markets", [])
    if len(markets) <= 1:
        return False
    per_date: dict = {}
    for m in markets:
        date = m.get("resolution_date", "unknown")
        per_date[date] = per_date.get(date, 0) + 1
    return max(per_date.values()) > 1


# =============================================================================
# BRACKET ALIGNMENT
# =============================================================================


def normalize_bracket(bracket: str | None) -> str:
    """Strip accents and a trailing "(...)" suffix, lowercase."""
    if not bracket:
        return ""
    nfkd = unicodedata.normalize("NFKD", bracket)
    text = "".join(c for c in nfkd if not unicodedata.combining(c))
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    return text.lower().strip()


def brackets_match(a: str | None, b: str | None) -> bool:
    na, nb = normalize_bracket(a), normalize_bracket(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def generate_pair_id(
    target_id: str, target_pos: str, cover_id: str, cover_pos: str
) -> str:
    components = f"{target_id}:{target_pos}:{cover_id}:{cover_pos}"
    return hashlib.md5(components.encode(), usedforsecurity=False).hexdigest()[:16]


# =============================================================================
# EXPANSION
# =============================================================================


def get_cover_markets(
    cover_info: dict, groups_by_id: dict, target_group_id: str
) -> list[dict]:
    """All markets of a covering group, with cover metadata, or [] if it can't cover."""
    if cover_info["group_id"] == target_group_id:
        return []
    cover_group = groups_by_id.get(cover_info["group_id"])
    if not cover_group or is_negrisk_exclusive(cover_group):
        return []
    probability = cover_info.get("probability", 0.0)
    if probability < MIN_COVER_PROBABILITY:
        return []

    markets = []
    for m in cover_group.get("markets", []):
        cm = {
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
            "cover_position": cover_info.get("cover_position", "YES"),
            "relationship": cover_info.get("relationship", ""),
            "relationship_type": cover_info.get("relationship_type", "causal"),
            "probability": probability,
        }
        if m.get("feesEnabled"):
            cm["feesEnabled"] = True
            if "feeSchedule" in m:
                cm["feeSchedule"] = m["feeSchedule"]
        markets.append(cm)
    return markets


def expand_implication_to_pairs(impl: dict, groups_by_id: dict) -> list[dict]:
    target_group_id = impl["group_id"]
    target_group = groups_by_id.get(target_group_id)
    if not target_group or is_negrisk_exclusive(target_group):
        return []

    target_title = impl.get("title", target_group.get("title", ""))
    sides = [("YES", impl.get("yes_covered_by", []))]
    if not is_mutually_exclusive_group(target_group):
        sides.append(("NO", impl.get("no_covered_by", [])))

    pairs = []
    for tm in target_group.get("markets", []):
        target_bracket = tm.get("bracket_label")
        for target_pos, covers in sides:
            for cover_info in covers:
                cover_markets = get_cover_markets(
                    cover_info, groups_by_id, target_group_id
                )
                if cover_info.get("bracket_alignment") == "match":
                    cover_markets = [
                        cm
                        for cm in cover_markets
                        if brackets_match(target_bracket, cm["bracket_label"])
                    ]
                for cm in cover_markets:
                    pairs.append(
                        {
                            "pair_id": generate_pair_id(
                                tm["id"],
                                target_pos,
                                cm["market_id"],
                                cm["cover_position"],
                            ),
                            "target_group_id": target_group_id,
                            "target_group_title": target_title,
                            "target_group_slug": target_group.get("slug", ""),
                            "target_market_id": tm["id"],
                            "target_market_slug": tm.get("slug", ""),
                            "target_question": tm.get("question", ""),
                            "target_position": target_pos,
                            "target_resolution": tm.get("resolution_date"),
                            "target_price": tm.get(
                                "price_yes" if target_pos == "YES" else "price_no", 0.5
                            ),
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
                            "target_feesEnabled": tm.get("feesEnabled", False),
                            "target_feeSchedule": tm.get("feeSchedule"),
                            "cover_feesEnabled": cm.get("feesEnabled", False),
                            "cover_feeSchedule": cm.get("feeSchedule"),
                        }
                    )
    return pairs


def expand_all_to_pairs(
    implications: list[dict], groups: list[dict]
) -> tuple[list, dict]:
    groups_by_id = {g["group_id"]: g for g in groups}
    pairs = [
        p
        for impl in implications
        for p in expand_implication_to_pairs(impl, groups_by_id)
    ]
    num_groups = len({p["target_group_id"] for p in pairs})
    summary = {
        "total_pairs": len(pairs),
        "yes_position_pairs": sum(p["target_position"] == "YES" for p in pairs),
        "no_position_pairs": sum(p["target_position"] == "NO" for p in pairs),
        "groups_with_pairs": num_groups,
        "avg_pairs_per_group": round(len(pairs) / num_groups, 2) if num_groups else 0,
    }
    return pairs, summary


# =============================================================================
# MAIN
# =============================================================================


def find_run(run: str | None) -> str:
    if run:
        return run
    runs = sorted(
        p.name for p in IMPLICATIONS_DIR.glob("*") if (p / "implications.json").exists()
    )
    if not runs:
        sys.exit(f"No runs in {IMPLICATIONS_DIR}; run 03_extract_implications.py first")
    return runs[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--run", help="run timestamp (default: latest 03 run)")
    args = parser.parse_args()

    run = find_run(args.run)
    groups_file = GROUPS_DIR / run / "groups.json"
    impl_file = IMPLICATIONS_DIR / run / "implications.json"
    for f in (groups_file, impl_file):
        if not f.exists():
            sys.exit(f"Missing {f}")
    groups = json.loads(groups_file.read_text(encoding="utf-8"))
    implications = json.loads(impl_file.read_text(encoding="utf-8"))
    log.info(f"Run {run}: {len(groups)} groups, {len(implications)} implications")

    pairs, summary = expand_all_to_pairs(implications, groups)

    out = OUTPUT_DIR / run
    out.mkdir(parents=True, exist_ok=True)
    (out / "pairs.json").write_text(
        json.dumps(pairs, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out / "summary.json").write_text(
        json.dumps({"run": run, **summary}, indent=2), encoding="utf-8"
    )
    log.info(f"Saved to {out}: {summary}")


if __name__ == "__main__":
    main()

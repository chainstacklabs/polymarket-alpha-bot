"""
Step definitions for user-friendly pipeline display.

Each step has:
- title: Short, action-oriented name shown during execution
- description: What's happening (shown to user while step runs)
- emoji: Visual indicator for terminal display
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StepDef:
    """Definition for a single pipeline step."""

    number: int
    title: str
    description: str
    emoji: str


# =============================================================================
# STEP DEFINITIONS
# =============================================================================
# These are shown to users during pipeline execution

STEPS: dict[int, StepDef] = {
    1: StepDef(
        number=1,
        title="Fetch Markets",
        description="Pulling active prediction markets from Polymarket API",
        emoji="📡",
    ),
    2: StepDef(
        number=2,
        title="Build Groups",
        description="Grouping markets by event, partitioned by timeframe/threshold/candidate",
        emoji="📦",
    ),
    3: StepDef(
        number=3,
        title="Detect New",
        description="Comparing against processed data to find new groups",
        emoji="🔍",
    ),
    4: StepDef(
        number=4,
        title="Find Implications",
        description="Using AI to discover logical implications between market groups",
        emoji="🧠",
    ),
    5: StepDef(
        number=5,
        title="Expand Pairs",
        description="Generating all market-level hedging combinations",
        emoji="🔗",
    ),
    6: StepDef(
        number=6,
        title="Validate Logic",
        description="AI verification of temporal consistency and logic",
        emoji="✅",
    ),
    7: StepDef(
        number=7,
        title="Build Portfolios",
        description="Calculating coverage metrics and opportunity tiers",
        emoji="💼",
    ),
    8: StepDef(
        number=8,
        title="Export Data",
        description="Saving results for API and dashboard access",
        emoji="💾",
    ),
}

TOTAL_STEPS = len(STEPS)


def get_step(number: int) -> StepDef:
    """Get step definition by number."""
    if number not in STEPS:
        raise ValueError(f"Unknown step number: {number}")
    return STEPS[number]


def get_step_header(number: int, total: int | None = None) -> str:
    """
    Get formatted step header for display.

    Example: "📡 [1/8] Fetch Markets"
    """
    step = get_step(number)
    total = total or TOTAL_STEPS
    return f"{step.emoji} [{number}/{total}] {step.title}"


def get_step_description(number: int) -> str:
    """Get step description."""
    return get_step(number).description

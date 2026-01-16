"""
Portfolio service for real-time price monitoring.

Manages portfolio state and calculates price-induced changes.
Used by the portfolio WebSocket to push updates to clients.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from core.paths import LIVE_DIR

# =============================================================================
# CONFIGURATION
# =============================================================================
RELOAD_INTERVAL_SECONDS = 60  # Reload portfolios.json periodically

# Tier thresholds (coverage, tier_number, label)
TIER_THRESHOLDS = [
    (0.95, 1, "HIGH_COVERAGE"),
    (0.90, 2, "GOOD_COVERAGE"),
    (0.85, 3, "MODERATE_COVERAGE"),
    (0.00, 4, "LOW_COVERAGE"),
]


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class PortfolioDelta:
    """Changes to portfolios from a price update."""

    changed: list[dict] = field(default_factory=list)
    tier_changes: list[dict] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    full_reload: bool = False  # True if clients should refetch all data
    all_portfolios: list[dict] | None = None  # All portfolios if full_reload

    def is_empty(self) -> bool:
        """Check if there are no changes."""
        return len(self.changed) == 0 and not self.full_reload


# =============================================================================
# PORTFOLIO SERVICE
# =============================================================================


class PortfolioService:
    """
    Service for managing portfolio state and calculating price-induced changes.

    Responsibilities:
    - Load and cache portfolios from portfolios.json
    - Build market_id → portfolio index mapping
    - Recalculate metrics when prices change
    - Detect tier changes and generate deltas
    """

    def __init__(self):
        self._portfolios: list[dict] = []
        self._market_to_portfolios: dict[str, list[int]] = {}
        self._last_load: datetime | None = None
        self._file_mtime: float | None = None  # Track file modification time
        self._loaded = False

    def load_portfolios(self) -> None:
        """Load portfolios from portfolios.json and build index."""
        portfolios_path = LIVE_DIR / "portfolios.json"

        if not portfolios_path.exists():
            logger.warning("portfolios.json not found, clearing cache")
            self._portfolios = []
            self._market_to_portfolios = {}
            self._file_mtime = None
            self._loaded = True  # Mark as loaded (with empty data)
            self._last_load = datetime.now(timezone.utc)
            return

        try:
            # Track file modification time
            self._file_mtime = portfolios_path.stat().st_mtime

            data = json.loads(portfolios_path.read_text())

            if isinstance(data, dict) and "portfolios" in data:
                portfolios = data["portfolios"]
            elif isinstance(data, list):
                portfolios = data
            else:
                portfolios = []

            self._portfolios = portfolios
            self._build_market_index()
            self._last_load = datetime.now(timezone.utc)
            self._loaded = True

            logger.info(
                f"PortfolioService loaded {len(portfolios)} portfolios, "
                f"tracking {len(self._market_to_portfolios)} markets"
            )

        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Error loading portfolios: {e}")

    def _build_market_index(self) -> None:
        """Build mapping from market_id to portfolio indices."""
        self._market_to_portfolios = {}

        for idx, portfolio in enumerate(self._portfolios):
            target_id = portfolio.get("target_market_id")
            cover_id = portfolio.get("cover_market_id")

            if target_id:
                if target_id not in self._market_to_portfolios:
                    self._market_to_portfolios[target_id] = []
                self._market_to_portfolios[target_id].append(idx)

            if cover_id:
                if cover_id not in self._market_to_portfolios:
                    self._market_to_portfolios[cover_id] = []
                self._market_to_portfolios[cover_id].append(idx)

    def should_reload(self) -> bool:
        """Check if portfolios should be reloaded from disk."""
        if not self._loaded:
            return True
        if self._last_load is None:
            return True

        portfolios_path = LIVE_DIR / "portfolios.json"

        # Check if file was deleted (and we have cached data)
        if not portfolios_path.exists():
            if self._portfolios:  # We have stale data
                return True
            return False  # Already empty, no need to reload

        # Check if file was modified since last load
        try:
            current_mtime = portfolios_path.stat().st_mtime
            if self._file_mtime is not None and current_mtime > self._file_mtime:
                logger.debug("portfolios.json modified, triggering reload")
                return True
        except OSError:
            pass

        # Fall back to time-based reload
        elapsed = (datetime.now(timezone.utc) - self._last_load).total_seconds()
        return elapsed > RELOAD_INTERVAL_SECONDS

    def update_prices(self, market_prices: dict[str, dict]) -> PortfolioDelta:
        """
        Update portfolio metrics with new prices and return changes.

        Args:
            market_prices: {market_id: {yes: float, no: float, ...}}

        Returns:
            PortfolioDelta with changed portfolios and tier changes
        """
        # Track current count to detect reloads
        old_count = len(self._portfolios)

        # Reload from disk periodically to pick up pipeline changes
        if self.should_reload():
            self.load_portfolios()

            # If portfolio count changed significantly, signal full reload
            new_count = len(self._portfolios)
            if old_count != new_count:
                logger.info(
                    f"Portfolio count changed: {old_count} -> {new_count}, triggering full reload"
                )
                return PortfolioDelta(
                    full_reload=True,
                    all_portfolios=self._portfolios.copy(),
                )

        if not self._portfolios:
            return PortfolioDelta()

        delta = PortfolioDelta()
        affected_indices: set[int] = set()

        # Find portfolios affected by price changes
        for market_id in market_prices.keys():
            if market_id in self._market_to_portfolios:
                affected_indices.update(self._market_to_portfolios[market_id])

        # Recalculate affected portfolios
        for idx in affected_indices:
            portfolio = self._portfolios[idx]
            updated, tier_change = self._recalculate_portfolio(portfolio, market_prices)

            if updated:
                delta.changed.append(updated)
                self._portfolios[idx] = updated

                if tier_change:
                    delta.tier_changes.append(tier_change)

        # Re-sort if there were tier changes
        if delta.tier_changes:
            self._portfolios.sort(key=lambda p: (p["tier"], -p["coverage"]))

        if delta.changed:
            logger.debug(
                f"Portfolio update: {len(delta.changed)} changed, "
                f"{len(delta.tier_changes)} tier changes"
            )

        return delta

    def _recalculate_portfolio(
        self,
        portfolio: dict,
        market_prices: dict[str, dict],
    ) -> tuple[dict | None, dict | None]:
        """
        Recalculate a single portfolio with new prices.

        Returns:
            Tuple of (updated_portfolio, tier_change_info) or (None, None) if no change
        """
        target_id = portfolio.get("target_market_id")
        cover_id = portfolio.get("cover_market_id")
        target_position = portfolio.get("target_position", "YES")
        cover_position = portfolio.get("cover_position", "YES")

        # Get current prices
        old_target_price = portfolio.get("target_price", 0.5)
        old_cover_price = portfolio.get("cover_price", 0.5)
        cover_probability = portfolio.get("cover_probability", 0.9)
        old_tier = portfolio.get("tier", 4)

        # Get new prices
        target_data = market_prices.get(target_id, {})
        cover_data = market_prices.get(cover_id, {})

        if target_position == "YES":
            new_target_price = target_data.get("yes", old_target_price)
        else:
            new_target_price = target_data.get("no", old_target_price)

        if cover_position == "YES":
            new_cover_price = cover_data.get("yes", old_cover_price)
        else:
            new_cover_price = cover_data.get("no", old_cover_price)

        # Skip if prices are invalid (zero or near-zero)
        if new_target_price <= 0.001 or new_cover_price <= 0.001:
            return None, None

        # Check if prices actually changed
        price_changed = (
            abs(new_target_price - old_target_price) > 0.001
            or abs(new_cover_price - old_cover_price) > 0.001
        )

        if not price_changed:
            return None, None

        # Recalculate metrics
        total_cost = new_target_price + new_cover_price
        p_target = new_target_price
        p_not_target = 1 - new_target_price
        coverage = p_target + p_not_target * cover_probability
        expected_profit = coverage - total_cost
        loss_probability = p_not_target * (1 - cover_probability)

        # Classify tier
        new_tier = 4
        new_tier_label = "LOW_COVERAGE"
        for threshold, tier, label in TIER_THRESHOLDS:
            if coverage >= threshold:
                new_tier = tier
                new_tier_label = label
                break

        # Build updated portfolio
        updated = {
            **portfolio,
            "target_price": round(new_target_price, 4),
            "cover_price": round(new_cover_price, 4),
            "total_cost": round(total_cost, 4),
            "profit": round(1.0 - total_cost, 4),
            "profit_pct": (
                round((1.0 - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0
            ),
            "coverage": round(coverage, 4),
            "loss_probability": round(loss_probability, 4),
            "expected_profit": round(expected_profit, 4),
            "tier": new_tier,
            "tier_label": new_tier_label,
        }

        # Check for tier change
        tier_change = None
        if new_tier != old_tier:
            tier_change = {
                "pair_id": portfolio.get("pair_id"),
                "old_tier": old_tier,
                "new_tier": new_tier,
                "coverage": round(coverage, 4),
            }

        return updated, tier_change

    def get_portfolios(
        self,
        max_tier: int = 3,
        profitable_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """
        Get filtered portfolios.

        Args:
            max_tier: Maximum tier to include (1-4)
            profitable_only: Only return portfolios with positive expected profit
            limit: Maximum number to return
            offset: Number to skip

        Returns:
            List of filtered portfolios
        """
        if self.should_reload():
            self.load_portfolios()

        filtered = self._portfolios

        # Apply tier filter
        if max_tier < 4:
            filtered = [p for p in filtered if p.get("tier", 4) <= max_tier]

        # Apply profitable filter
        if profitable_only:
            filtered = [p for p in filtered if p.get("expected_profit", 0) > 0.001]

        # Apply pagination
        if offset:
            filtered = filtered[offset:]
        if limit:
            filtered = filtered[:limit]

        return filtered

    def get_summary(self) -> dict:
        """Get summary statistics about portfolios."""
        if self.should_reload():
            self.load_portfolios()

        if not self._portfolios:
            return {
                "total": 0,
                "by_tier": {},
                "profitable_count": 0,
            }

        by_tier = {}
        profitable_count = 0

        for p in self._portfolios:
            tier = p.get("tier", 4)
            tier_key = f"tier_{tier}"
            by_tier[tier_key] = by_tier.get(tier_key, 0) + 1

            if p.get("expected_profit", 0) > 0.001:
                profitable_count += 1

        return {
            "total": len(self._portfolios),
            "by_tier": by_tier,
            "profitable_count": profitable_count,
            "market_count": len(self._market_to_portfolios),
        }


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

portfolio_service = PortfolioService()

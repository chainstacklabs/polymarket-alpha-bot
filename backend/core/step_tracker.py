"""
Step progress tracking with user-friendly console output.

Provides both programmatic tracking (for API) and rich terminal display.
"""

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core.step_defs import TOTAL_STEPS, get_step, get_step_header


@dataclass
class StepProgress:
    """Progress information for a single pipeline step."""

    step_number: int
    step_name: str
    status: str  # 'running', 'completed', 'failed'
    started_at: str
    elapsed_seconds: float = 0.0
    details: str | None = None  # "Processing 50/150 events"
    description: str | None = None  # "Pulling active prediction markets..."
    emoji: str | None = None  # "📡"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StepTracker:
    """
    Thread-safe context manager for tracking pipeline step progress.

    Displays user-friendly output to console while tracking for API.

    Usage:
        tracker = StepTracker()

        with tracker.step(1, "Fetch Events"):
            # do work
            tracker.update_details("Fetched 150 events")
    """

    def __init__(self, console: Console | None = None, quiet: bool = False):
        """
        Initialize step tracker.

        Args:
            console: Rich console for output (creates new if None)
            quiet: If True, suppress console output (API mode)
        """
        self._lock = threading.Lock()
        self._console = console or Console()
        self._quiet = quiet

        self.current_step: StepProgress | None = None
        self.completed_steps: list[StepProgress] = []
        self.pipeline_start: datetime = datetime.now(timezone.utc)
        self.total_steps: int = TOTAL_STEPS

    def step(self, step_number: int, step_name: str):
        """Context manager for tracking a step."""
        return _StepContext(self, step_number, step_name)

    def update_details(self, details: str) -> None:
        """Update details for current step (thread-safe)."""
        with self._lock:
            if self.current_step:
                self.current_step.details = details
                # Show update in console
                if not self._quiet:
                    self._print_details(details)

    def _print_header(self, step_number: int) -> None:
        """Print step header with description."""
        if self._quiet:
            return

        step = get_step(step_number)
        header = get_step_header(step_number, self.total_steps)

        # Print step header
        self._console.print()
        self._console.print(f"[bold cyan]{header}[/]")
        self._console.print(f"[dim]  └─ {step.description}[/]")

    def _print_details(self, details: str) -> None:
        """Print progress details update."""
        if self._quiet:
            return
        # Overwrite previous line with new details
        self._console.print(f"[dim]     → {details}[/]")

    def _print_completion(self, step_number: int, elapsed: float, failed: bool) -> None:
        """Print step completion status."""
        if self._quiet:
            return

        if failed:
            self._console.print(f"[bold red]     ✗ Failed after {elapsed:.1f}s[/]")
        else:
            self._console.print(f"[green]     ✓ Done in {elapsed:.1f}s[/]")

    def print_pipeline_start(self, mode: str) -> None:
        """Print pipeline start banner."""
        if self._quiet:
            return

        mode_color = "yellow" if mode == "full" else "blue"
        mode_label = "FULL REPROCESS" if mode == "full" else "INCREMENTAL"

        panel = Panel(
            Text.from_markup(
                f"[bold]Alphapoly Pipeline[/]\n"
                f"[{mode_color}]Mode: {mode_label}[/] • "
                f"[dim]{self.total_steps} steps[/]"
            ),
            border_style="dim",
            padding=(0, 2),
        )
        self._console.print()
        self._console.print(panel)

    def print_pipeline_complete(self, stats: dict) -> None:
        """Print pipeline completion summary."""
        if self._quiet:
            return

        elapsed = stats.get("elapsed_seconds", 0)
        portfolios = stats.get("portfolios", 0)
        new_groups = stats.get("new_groups", 0)

        self._console.print()
        self._console.print("[bold green]━━━ Pipeline Complete ━━━[/]")
        self._console.print(
            f"[dim]  Time: {elapsed:.1f}s • "
            f"New groups: {new_groups} • "
            f"Portfolios: {portfolios}[/]"
        )
        self._console.print()

    def print_price_update(self, stats: dict) -> None:
        """Print price-only update summary."""
        if self._quiet:
            return

        elapsed = stats.get("elapsed_seconds", 0)
        prices_updated = stats.get("prices_updated", 0)

        self._console.print()
        self._console.print("[bold blue]━━━ Prices Updated ━━━[/]")
        self._console.print(
            f"[dim]  Time: {elapsed:.1f}s • Prices updated: {prices_updated}[/]"
        )
        self._console.print()

    def print_skip(self, reason: str) -> None:
        """Print skip message."""
        if self._quiet:
            return

        self._console.print()
        self._console.print(f"[yellow]Pipeline skipped: {reason}[/]")
        self._console.print()

    def get_state(self) -> dict[str, Any]:
        """Get current tracker state as dict (thread-safe)."""
        with self._lock:
            elapsed = (datetime.now(timezone.utc) - self.pipeline_start).total_seconds()

            # Update current step elapsed time
            current_step_dict = None
            if self.current_step:
                self.current_step.elapsed_seconds = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(self.current_step.started_at)
                ).total_seconds()
                current_step_dict = self.current_step.to_dict()

            return {
                "current_step": current_step_dict,
                "completed_steps": [s.to_dict() for s in self.completed_steps],
                "pipeline_elapsed_seconds": elapsed,
                "total_steps": self.total_steps,
                "completed_count": len(self.completed_steps),
            }


class _StepContext:
    """Internal context manager for step tracking (thread-safe)."""

    def __init__(self, tracker: StepTracker, step_number: int, step_name: str):
        self.tracker = tracker
        self.step_number = step_number
        self.step_name = step_name
        self.start_time: datetime | None = None

    def __enter__(self):
        self.start_time = datetime.now(timezone.utc)

        # Print header before starting
        self.tracker._print_header(self.step_number)

        # Get step metadata from definitions
        try:
            step_def = get_step(self.step_number)
            description = step_def.description
            emoji = step_def.emoji
        except ValueError:
            description = None
            emoji = None

        with self.tracker._lock:
            self.tracker.current_step = StepProgress(
                step_number=self.step_number,
                step_name=self.step_name,
                status="running",
                started_at=self.start_time.isoformat(),
                description=description,
                emoji=emoji,
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = 0.0
        failed = exc_type is not None

        with self.tracker._lock:
            if self.tracker.current_step and self.start_time:
                elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
                self.tracker.current_step.elapsed_seconds = elapsed
                self.tracker.current_step.status = "failed" if failed else "completed"

                # Move to completed
                self.tracker.completed_steps.append(self.tracker.current_step)
                self.tracker.current_step = None

        # Print completion after releasing lock
        self.tracker._print_completion(self.step_number, elapsed, failed)

        # Don't suppress exceptions
        return False

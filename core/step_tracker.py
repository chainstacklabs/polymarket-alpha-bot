"""Lightweight step progress tracking for pipeline monitoring."""

import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class StepProgress:
    """Progress information for a single pipeline step."""

    step_number: int
    step_name: str
    status: str  # 'running', 'completed', 'failed'
    started_at: str
    elapsed_seconds: float = 0.0
    details: str | None = None  # "Processing 50/150 events"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StepTracker:
    """
    Thread-safe context manager for tracking pipeline step progress.

    Usage:
        tracker = StepTracker()

        with tracker.step(1, "Fetch Events"):
            # do work
            tracker.update_details("Fetched 150 events")
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.current_step: StepProgress | None = None
        self.completed_steps: list[StepProgress] = []
        self.pipeline_start: datetime = datetime.now(timezone.utc)
        self.total_steps: int = 13  # Default for normal pipeline (with new events)

    def step(self, step_number: int, step_name: str):
        """Context manager for tracking a step."""
        return _StepContext(self, step_number, step_name)

    def update_details(self, details: str) -> None:
        """Update details for current step (thread-safe)."""
        with self._lock:
            if self.current_step:
                self.current_step.details = details

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
        with self.tracker._lock:
            self.tracker.current_step = StepProgress(
                step_number=self.step_number,
                step_name=self.step_name,
                status="running",
                started_at=self.start_time.isoformat(),
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with self.tracker._lock:
            if self.tracker.current_step and self.start_time:
                elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
                self.tracker.current_step.elapsed_seconds = elapsed
                self.tracker.current_step.status = "failed" if exc_type else "completed"

                # Move to completed
                self.tracker.completed_steps.append(self.tracker.current_step)
                self.tracker.current_step = None

        # Don't suppress exceptions
        return False

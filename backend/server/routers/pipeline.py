"""Pipeline status and control endpoints."""

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from core.paths import DATA_DIR

router = APIRouter()

# Paths
MANIFEST_PATH = DATA_DIR / "manifest.json"

# Pipeline state
_running_pipeline: dict[str, Any] | None = None
_step_tracker: Any = None  # StepTracker instance during pipeline run


class ProductionRunRequest(BaseModel):
    """Request to run production pipeline."""

    full: bool = False  # If True, reset and reprocess all
    max_events: int | None = Field(
        default=None, gt=0, description="Limit number of events fetched (must be > 0)"
    )


def load_manifest() -> dict:
    """Load manifest file."""
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def get_step_info() -> list[dict[str, Any]]:
    """Get info about all pipeline steps.

    Note: Returns empty list as CLI was simplified and script registry removed.
    Step progress is now tracked via production pipeline state instead.
    """
    return []


@router.get("/status")
async def get_status() -> dict[str, Any]:
    """Get pipeline status including latest runs for each step."""
    manifest = load_manifest()
    steps = get_step_info()

    # Get production pipeline state
    try:
        from core.state import load_state

        state = load_state()
        stats = state.get_stats()
        last_run = state.get_last_run()
        state.close()
        production_state = {
            "total_events": stats.total_events,
            "total_entities": stats.total_entities,
            "total_edges": stats.total_edges,
            "last_full_run": stats.last_full_run,
            "last_refresh": stats.last_refresh,
            "last_run": last_run,
        }
    except Exception:
        production_state = None

    is_running = (
        _running_pipeline is not None and _running_pipeline.get("status") == "running"
    )

    # Get step progress from tracker (or final state if completed)
    step_progress = None
    if is_running and _step_tracker is not None:
        step_progress = _step_tracker.get_state()
    elif _running_pipeline and "final_step_progress" in _running_pipeline:
        step_progress = _running_pipeline["final_step_progress"]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "running": is_running,
        "current_step": _running_pipeline.get("step") if is_running else None,
        "production": production_state,
        "steps": steps,
        "manifest": manifest,
        "step_progress": step_progress,
    }


# =============================================================================
# PRODUCTION PIPELINE ENDPOINTS
# =============================================================================


def run_production_pipeline_task(full: bool, max_events: int | None = None):
    """Background task to run the production pipeline."""
    global _running_pipeline, _step_tracker

    try:
        # Create step tracker for progress monitoring
        from core.step_tracker import StepTracker

        _step_tracker = StepTracker()

        # Demo mode (max_events set) always runs as full reset to ensure fresh state
        # This allows running demo multiple times without stale data
        is_demo = max_events is not None
        effective_full = full or is_demo

        mode = "demo" if is_demo else ("full" if full else "incremental")
        _running_pipeline = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_type": "production",
            "mode": mode,
            "max_events": max_events,
            "status": "running",
        }

        from core.runner import run

        result = run(
            full=effective_full, step_tracker=_step_tracker, max_events=max_events
        )

        _running_pipeline["status"] = "completed"
        _running_pipeline["completed_at"] = datetime.now(timezone.utc).isoformat()
        _running_pipeline["result"] = result

    except Exception as e:
        if _running_pipeline:
            _running_pipeline["status"] = "error"
            _running_pipeline["error"] = str(e)
            _running_pipeline["completed_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        # Preserve final step progress for frontend before clearing tracker
        if _step_tracker and _running_pipeline:
            _running_pipeline["final_step_progress"] = _step_tracker.get_state()
        _step_tracker = None


@router.post("/run/production")
async def run_production(
    request: ProductionRunRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """
    Trigger the production pipeline.

    Modes:
    - Demo (max_events set): Always resets state first, fetches limited events
    - Full (full=True): Resets state and reprocesses all events
    - Incremental (default): Only processes new events not yet in state

    Args:
        request.full: If True, reset state and reprocess all events
        request.max_events: Limit events fetched (enables demo mode, always resets)
    """
    global _running_pipeline

    if _running_pipeline and _running_pipeline.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="Pipeline is already running",
        )

    # Start pipeline in background
    background_tasks.add_task(
        run_production_pipeline_task, request.full, request.max_events
    )

    mode = "demo" if request.max_events else ("full" if request.full else "incremental")
    return {
        "status": "started",
        "pipeline_type": "production",
        "mode": mode,
        "max_events": request.max_events,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/reset")
async def reset_production_state() -> dict[str, Any]:
    """Reset the production pipeline state (clear all accumulated data)."""
    global _running_pipeline

    if _running_pipeline and _running_pipeline.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="Cannot reset while pipeline is running",
        )

    try:
        from core.state import load_state

        state = load_state()
        state.reset()
        state.close()

        return {
            "status": "reset",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": "Production pipeline state has been reset",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

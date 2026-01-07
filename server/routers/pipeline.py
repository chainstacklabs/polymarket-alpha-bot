"""Pipeline status and control endpoints."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

router = APIRouter()

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MANIFEST_PATH = DATA_DIR / "manifest.json"

# Pipeline state
_running_pipeline: dict[str, Any] | None = None
_step_tracker: Any = None  # StepTracker instance during pipeline run


class PipelineRunRequest(BaseModel):
    """Request to run pipeline (legacy script-based)."""

    from_step: str = "01"
    to_step: str = "06_3"


class ProductionRunRequest(BaseModel):
    """Request to run production pipeline."""

    full: bool = False  # If True, reset and reprocess all


def load_manifest() -> dict:
    """Load manifest file."""
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())
    return {}


def get_step_info() -> list[dict[str, Any]]:
    """Get info about all pipeline steps."""
    from cli.script_registry import PIPELINE_ORDER, SCRIPTS

    steps = []
    for step_id in PIPELINE_ORDER:
        script = SCRIPTS[step_id]
        dir_name = f"{script.step}_{script.name}"
        step_dir = DATA_DIR / dir_name

        latest_run = None
        if step_dir.exists():
            runs = sorted(
                [d for d in step_dir.iterdir() if d.is_dir() and d.name[0].isdigit()],
                reverse=True,
            )
            if runs:
                latest_run = runs[0].name

        steps.append(
            {
                "step": step_id,
                "name": script.name,
                "description": script.description,
                "latest_run": latest_run,
                "has_data": latest_run is not None,
            }
        )

    return steps


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


@router.get("/state")
async def get_production_state() -> dict[str, Any]:
    """Get production pipeline state (from _live/)."""
    try:
        from core.state import load_state

        state = load_state()
        stats = state.get_stats()
        last_run = state.get_last_run()
        state.close()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": {
                "total_events": stats.total_events,
                "total_entities": stats.total_entities,
                "total_edges": stats.total_edges,
                "last_full_run": stats.last_full_run,
                "last_refresh": stats.last_refresh,
            },
            "last_run": last_run,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/steps")
async def list_steps() -> dict[str, Any]:
    """List all pipeline steps with metadata."""
    from cli.script_registry import PIPELINE_ORDER, SCRIPTS

    steps = []
    for step_id in PIPELINE_ORDER:
        script = SCRIPTS[step_id]
        steps.append(
            {
                "step": step_id,
                "name": script.name,
                "description": script.description,
                "inputs": script.inputs,
                "outputs": script.outputs,
                "config_vars": script.config_vars,
            }
        )

    return {"steps": steps}


def run_pipeline_task(from_step: str, to_step: str):
    """Background task to run the pipeline."""
    global _running_pipeline

    try:
        _running_pipeline = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "from_step": from_step,
            "to_step": to_step,
            "step": from_step,
            "status": "running",
        }

        # Run the pipeline using the CLI
        result = subprocess.run(
            [
                "uv",
                "run",
                "poly",
                "run",
                "pipeline",
                "--from-step",
                from_step,
                "--to-step",
                to_step,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )

        _running_pipeline["status"] = (
            "completed" if result.returncode == 0 else "failed"
        )
        _running_pipeline["exit_code"] = result.returncode
        _running_pipeline["completed_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        if _running_pipeline:
            _running_pipeline["status"] = "error"
            _running_pipeline["error"] = str(e)
    finally:
        # Clear after a delay to allow status check
        pass


@router.post("/run")
async def run_pipeline(
    request: PipelineRunRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Trigger a pipeline run in the background."""
    global _running_pipeline

    if _running_pipeline and _running_pipeline.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="Pipeline is already running",
        )

    # Validate steps
    from cli.script_registry import PIPELINE_ORDER

    if request.from_step not in PIPELINE_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid from_step: {request.from_step}",
        )
    if request.to_step not in PIPELINE_ORDER:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid to_step: {request.to_step}",
        )

    # Start pipeline in background
    background_tasks.add_task(run_pipeline_task, request.from_step, request.to_step)

    return {
        "status": "started",
        "from_step": request.from_step,
        "to_step": request.to_step,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/running")
async def get_running() -> dict[str, Any]:
    """Get info about currently running pipeline."""
    if not _running_pipeline:
        return {"running": False}

    return {
        "running": _running_pipeline.get("status") == "running",
        **_running_pipeline,
    }


# =============================================================================
# PRODUCTION PIPELINE ENDPOINTS
# =============================================================================


def run_production_pipeline_task(full: bool):
    """Background task to run the production pipeline."""
    global _running_pipeline, _step_tracker

    try:
        # Create step tracker for progress monitoring
        from core.step_tracker import StepTracker

        _step_tracker = StepTracker()

        _running_pipeline = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_type": "production",
            "mode": "full" if full else "incremental",
            "status": "running",
        }

        from core.runner import run

        result = run(full=full, step_tracker=_step_tracker)

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
    Trigger the production pipeline (incremental by default).

    The production pipeline:
    - Fetches all events from Polymarket API
    - Identifies new events not yet processed
    - Processes only new events (incremental) or all (if full=True)
    - Updates the _live/ directory with latest data

    Args:
        request.full: If True, reset state and reprocess all events
    """
    global _running_pipeline

    if _running_pipeline and _running_pipeline.get("status") == "running":
        raise HTTPException(
            status_code=409,
            detail="Pipeline is already running",
        )

    # Start pipeline in background
    background_tasks.add_task(run_production_pipeline_task, request.full)

    return {
        "status": "started",
        "pipeline_type": "production",
        "mode": "full" if request.full else "incremental",
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

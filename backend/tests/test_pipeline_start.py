"""The start endpoint registers the run before replying (no "not running" gap)."""

import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

from server.routers import pipeline


@pytest.fixture(autouse=True)
def _reset_run_state(monkeypatch):
    monkeypatch.setattr(pipeline, "_running_pipeline", None)


def _start(**kwargs):
    tasks = BackgroundTasks()  # not executed: models the gap before the task runs
    req = pipeline.ProductionRunRequest(**kwargs)
    return asyncio.run(pipeline.run_production(req, tasks)), tasks


def test_run_is_marked_running_before_the_task_starts():
    reply, tasks = _start(full=False)

    assert reply["status"] == "started"
    assert reply["mode"] == "incremental"
    assert len(tasks.tasks) == 1
    assert pipeline._running_pipeline["status"] == "running"
    assert pipeline._running_pipeline["started_at"] == reply["started_at"]


def test_second_start_in_the_gap_is_rejected():
    _start(full=True)
    with pytest.raises(HTTPException) as exc:
        _start(full=True)
    assert exc.value.status_code == 409


def test_task_keeps_the_record_registered_by_the_endpoint(monkeypatch):
    reply, _ = _start(max_events=5)
    record = pipeline._running_pipeline
    monkeypatch.setattr("core.runner.run", lambda **_: {"ok": True})

    pipeline.run_production_pipeline_task(False, 5)

    assert pipeline._running_pipeline is record
    assert record["mode"] == "demo"
    assert record["started_at"] == reply["started_at"]
    assert record["status"] == "completed"

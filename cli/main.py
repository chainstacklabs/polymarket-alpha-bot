"""Minimal CLI for Alphapoly - pipeline automation and server control."""

import subprocess
from pathlib import Path

import typer
from dotenv import load_dotenv
from rich.console import Console

# Load .env file (same as server)
load_dotenv()

from cli import __version__

app = typer.Typer(
    name="poly",
    help="Alphapoly CLI - run pipeline and start server",
    no_args_is_help=True,
)
console = Console()

PROJECT_ROOT = Path(__file__).parent.parent


@app.command()
def run(
    full: bool = typer.Option(
        False,
        "--full",
        "-f",
        help="Force full reprocessing (clear state first)",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        "-l",
        help="Limit number of events to process (for demo/testing)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress step-by-step output (only show errors)",
    ),
):
    """Run the production pipeline.

    Examples:
        poly run            # Incremental - process new events only
        poly run --full     # Full - reprocess everything from scratch
        poly run --limit 20 # Demo mode - only process 20 events
        poly run --quiet    # Silent mode for automation
    """
    from core.runner import run as run_pipeline

    try:
        run_pipeline(full=full, max_events=limit, quiet=quiet)
    except Exception as e:
        console.print(f"[red]Pipeline failed:[/] {e}")
        raise typer.Exit(1)


@app.command()
def reset(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
):
    """Reset pipeline state (clear all accumulated data)."""
    if not yes:
        yes = typer.confirm("Delete all pipeline data?")
        if not yes:
            raise typer.Exit(0)

    from core.state import load_state

    state = load_state()
    state.reset()
    state.close()
    console.print("[green]Reset complete[/]")


@app.command()
def serve(
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind to"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable auto-reload"),
):
    """Start the FastAPI server.

    Examples:
        poly serve              # Start on localhost:8000
        poly serve --port 3001  # Custom port
        poly serve --reload     # Dev mode with auto-reload
    """
    cmd = ["uvicorn", "server.main:app", "--host", "localhost", "--port", str(port)]
    if reload:
        cmd.append("--reload")

    console.print(f"[bold]Server[/] http://localhost:{port}")
    try:
        subprocess.run(cmd, cwd=PROJECT_ROOT)
    except KeyboardInterrupt:
        pass
    except FileNotFoundError:
        console.print("[red]uvicorn not found - run 'uv sync'[/]")
        raise typer.Exit(1)


@app.command()
def status():
    """Show pipeline status and detect issues.

    Displays:
    - Current state statistics (events, entities, edges)
    - Last run information
    - Orphaned runs (crashes/incomplete runs)
    - Live data file status
    """
    from datetime import datetime

    from core.state import (
        EVENTS_PATH,
        GRAPH_PATH,
        OPPORTUNITIES_PATH,
        load_state,
    )

    state = load_state()

    # Stats
    stats = state.get_stats()
    console.print("[bold]Pipeline State[/]")
    console.print(f"  Events: {stats.total_events}")
    console.print(f"  Entities: {stats.total_entities}")
    console.print(f"  Graph edges: {stats.total_edges}")

    # Last runs
    console.print("\n[bold]Last Runs[/]")
    last_full = state.get_last_run("full")
    last_refresh = state.get_last_run("refresh")

    if last_full:
        console.print(
            f"  Full: {last_full['status']} "
            f"({last_full.get('events_processed', 'N/A')} events) "
            f"at {last_full.get('completed_at', last_full.get('started_at', 'N/A'))}"
        )
    if last_refresh:
        console.print(
            f"  Refresh: {last_refresh['status']} "
            f"({last_refresh.get('events_processed', 'N/A')} events) "
            f"at {last_refresh.get('completed_at', last_refresh.get('started_at', 'N/A'))}"
        )

    # Orphaned runs
    orphaned = state.get_orphaned_runs()
    if orphaned:
        console.print(f"\n[bold yellow]Orphaned Runs ({len(orphaned)})[/]")
        for run in orphaned:
            console.print(
                f"  [yellow]Run {run['id']}[/]: {run['run_type']}, "
                f"started: {run['started_at']}"
            )
        console.print("[dim]Run 'poly cleanup' to mark these as failed[/]")
    else:
        console.print("\n[green]No orphaned runs detected[/]")

    # Live files
    console.print("\n[bold]Live Data Files[/]")
    for name, path in [
        ("events.json", EVENTS_PATH),
        ("opportunities.json", OPPORTUNITIES_PATH),
        ("graph.json", GRAPH_PATH),
    ]:
        if path.exists():
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            size = path.stat().st_size
            console.print(f"  {name}: {size:,} bytes, modified {mtime}")
        else:
            console.print(f"  {name}: [red]missing[/]")

    state.close()


@app.command()
def cleanup():
    """Clean up orphaned runs (crashed/incomplete runs).

    Marks any runs stuck in 'running' status as 'failed'.
    This happens automatically on pipeline start, but can be run manually.
    """
    from core.state import load_state

    state = load_state()

    orphaned = state.get_orphaned_runs()
    if not orphaned:
        console.print("[green]No orphaned runs to clean up[/]")
        state.close()
        return

    console.print(f"Found {len(orphaned)} orphaned run(s):")
    for run in orphaned:
        console.print(
            f"  Run {run['id']}: {run['run_type']}, started: {run['started_at']}"
        )

    cleaned = state.cleanup_orphaned_runs()
    console.print(f"[green]Cleaned up {cleaned} orphaned run(s)[/]")
    state.close()


@app.command()
def version():
    """Show version."""
    console.print(f"alphapoly {__version__}")


if __name__ == "__main__":
    app()

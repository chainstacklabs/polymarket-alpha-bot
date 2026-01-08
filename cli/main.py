"""Minimal CLI for Alphapoly - pipeline automation and server control."""

import subprocess
from pathlib import Path

import typer
from rich.console import Console

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
):
    """Run the production pipeline.

    Examples:
        poly run            # Incremental - process new events only
        poly run --full     # Full - reprocess everything from scratch
    """
    from core.runner import run as run_pipeline
    from core.state import load_state

    state = load_state()
    stats = state.get_stats()
    state.close()

    mode = "full" if full else "incremental"
    console.print(f"[bold]Pipeline[/] ({mode}) - {stats.total_events} events in state")

    try:
        result = run_pipeline(full=full)
        console.print(
            f"[green]Done[/] - {result.get('new_events', 0)} new events, "
            f"{result.get('opportunities', 0)} opportunities, "
            f"{result.get('elapsed_seconds', 0):.1f}s"
        )
    except Exception as e:
        console.print(f"[red]Failed:[/] {e}")
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
def version():
    """Show version."""
    console.print(f"alphapoly {__version__}")


if __name__ == "__main__":
    app()

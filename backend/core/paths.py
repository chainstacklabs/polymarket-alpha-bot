"""Shared path constants for the backend."""

from pathlib import Path

# Backend root (where pyproject.toml lives)
BACKEND_ROOT = Path(__file__).parent.parent

# Project root (parent of backend/)
PROJECT_ROOT = BACKEND_ROOT.parent

# Data directory (at project root, shared)
DATA_DIR = PROJECT_ROOT / "data"
LIVE_DIR = DATA_DIR / "_live"

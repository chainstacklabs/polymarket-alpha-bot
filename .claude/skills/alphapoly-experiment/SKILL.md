---
name: Creating Alphapoly Experiments
description: Scaffolds a standalone experiment script in experiments/ using shared backend dependencies. Use when creating a new experiment or prototype outside the main backend. Also use when the user says "prototype", "try something", "test an idea", "spike", "scratch script", "quick experiment", or wants to explore something without modifying the main codebase.
---

# Creating Alphapoly Experiments

Scaffold a new standalone script in `experiments/`.

## Rules
- **Completely standalone** — no shared modules; no imports from `backend/` or other `experiments/` files
- **Sequential numbering** — check existing files in `experiments/` and use the next number
- **Dependencies from backend** — experiments share `backend/pyproject.toml` deps (httpx, polars, etc.); no inline PEP 723 headers needed
- **Run with** `uv run experiments/<script>.py` from the project root

## Template

```python
"""NN: What this experiment explores.

WHAT IT DOES
    Brief description of what the script does.

WHY WE NEED THIS
    What question or hypothesis this experiment addresses.
"""

import json
import logging
from pathlib import Path

import httpx

DATA_DIR = Path(__file__).parent.parent / "data"
log = logging.getLogger(__name__)

# ... experiment code
```

Scripts needing API keys should load `.env` with an explicit path:
```python
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
```

## Naming
`experiments/NN_short_description.py` — e.g. `experiments/09_test_llm_model.py`

Subdirectories (e.g. `experiments/trading/`) have their own numbering sequence.

## Running
```bash
uv run experiments/NN_script.py
```
Run from the project root. Dependencies resolve from `backend/pyproject.toml` via `uv`.

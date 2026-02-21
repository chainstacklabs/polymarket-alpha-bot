---
name: alphapoly-experiment
description: Use when creating a new standalone experiment script in the alphapoly experiments/ directory
---

# Alphapoly Experiment

Scaffold a new standalone script in `experiments/`.

## Rules
- **Completely standalone** — no imports from `backend/` modules
- **Sequential numbering** — next after existing `experiments/08_*.py`
- **uv inline dependencies** — declare all deps in script header

## Template

```python
# /// script
# dependencies = [
#   "httpx",
#   "polars",
# ]
# ///
"""What this experiment explores."""

import httpx
import polars as pl

# ... experiment code
```

## Naming
`experiments/NN_short_description.py` — e.g. `experiments/09_test_llm_model.py`

## Running
```bash
cd backend && uv run ../experiments/NN_script.py
```

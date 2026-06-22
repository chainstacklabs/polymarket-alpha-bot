#!/usr/bin/env python3
"""Pre-edit guard: block writes to sensitive files."""

import json
import os
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")
proj = os.environ.get("CLAUDE_PROJECT_DIR", "")

# Patterns to protect (relative to project root)
BLOCKED_SUFFIXES = (".pem", ".key", ".secret", ".enc")

rel = os.path.relpath(file_path, proj) if proj else file_path
base = os.path.basename(rel)

# .env and any variant (.env.local, .env.production, …) — but keep the template editable
if base.startswith(".env") and base != ".env.example":
    print(f"BLOCKED: {rel} is a secrets file — edit manually", file=sys.stderr)
    sys.exit(2)

# Pipeline output + encrypted keystores under any data/ dir (root data/ or backend/data/)
if rel.startswith("data/") or "/data/" in rel:
    print(f"BLOCKED: {rel} is pipeline output / keystore — don't edit directly", file=sys.stderr)
    sys.exit(2)

if rel.endswith(BLOCKED_SUFFIXES):
    print(f"BLOCKED: {rel} looks like a credential file", file=sys.stderr)
    sys.exit(2)

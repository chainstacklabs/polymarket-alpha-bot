#!/usr/bin/env python3
"""Auto-lint hook: ruff for Python files, prettier + eslint for TypeScript/CSS files.

Captures output and exits non-zero on unfixable errors so Claude can see and fix them.
"""

import json
import os
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")
proj = os.environ.get("CLAUDE_PROJECT_DIR", "")
backend = os.path.join(proj, "backend")
frontend = os.path.join(proj, "frontend")

exit_code = 0

if file_path.startswith(backend) and file_path.endswith(".py"):
    subprocess.run(["uvx", "ruff", "format", backend])
    result = subprocess.run(
        ["uvx", "ruff", "check", "--fix", backend], capture_output=True, text=True
    )
    if result.returncode != 0:
        print(result.stdout, end="", file=sys.stderr)
        print(result.stderr, end="", file=sys.stderr)
        exit_code = 2  # Exit 2 feeds stderr back to Claude as an error message

elif file_path.startswith(frontend) and file_path.endswith(
    (".ts", ".tsx", ".js", ".jsx", ".css", ".json")
):
    subprocess.run(["node_modules/.bin/prettier", "--write", file_path], cwd=frontend)
    if file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
        result = subprocess.run(
            ["node_modules/.bin/eslint", "--fix", file_path],
            capture_output=True,
            text=True,
            cwd=frontend,
        )
        if result.returncode != 0:
            print(result.stdout, end="", file=sys.stderr)
            print(result.stderr, end="", file=sys.stderr)
            exit_code = 2  # Exit 2 feeds stderr back to Claude as an error message

sys.exit(exit_code)

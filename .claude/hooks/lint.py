#!/usr/bin/env python3
"""Auto-lint hook: ruff for Python files, prettier + eslint for TypeScript/CSS files."""
import json
import os
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")
proj = os.environ.get("CLAUDE_PROJECT_DIR", "")
backend = os.path.join(proj, "backend")
frontend = os.path.join(proj, "frontend")

if file_path.startswith(backend) and file_path.endswith(".py"):
    subprocess.run(["uvx", "ruff", "format", backend])
    subprocess.run(["uvx", "ruff", "check", "--fix", backend])
elif file_path.startswith(frontend) and file_path.endswith(
    (".ts", ".tsx", ".js", ".jsx", ".css", ".json")
):
    # Prettier handles formatting; ESLint handles React/Next.js rules
    subprocess.run(["node_modules/.bin/prettier", "--write", file_path], cwd=frontend)
    if file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
        subprocess.run(
            ["node_modules/.bin/eslint", "--fix", file_path], cwd=frontend
        )

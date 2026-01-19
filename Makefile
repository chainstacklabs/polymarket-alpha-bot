# Alphapoly Development Commands
# ================================

.PHONY: help install dev backend frontend pipeline pipeline-full lint clean check-node export-seed import-seed

# Node.js detection - supports fnm, nvm, volta, and system node
# Searches common installation paths and adds to PATH
NODE_PATHS := $(HOME)/.local/share/fnm/node-versions/*/installation/bin
NODE_PATHS += $(HOME)/.fnm/node-versions/*/installation/bin
NODE_PATHS += $(HOME)/.nvm/versions/node/*/bin
NODE_PATHS += $(HOME)/.volta/bin
NODE_PATHS += /usr/local/bin
NODE_PATHS += /opt/homebrew/bin
EXTRA_PATH := $(subst $(eval ) ,:,$(wildcard $(NODE_PATHS)))
export PATH := $(EXTRA_PATH):$(PATH)

help:
	@echo "Alphapoly Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install     Install all dependencies (backend + frontend)"
	@echo ""
	@echo "Development:"
	@echo "  make dev         Start backend + frontend servers"
	@echo "  make backend     Start backend API only (localhost:8000)"
	@echo "  make frontend    Start frontend only (localhost:3000)"
	@echo ""
	@echo "Pipeline:"
	@echo "  make pipeline       Run ML pipeline (incremental)"
	@echo "  make pipeline-full  Run ML pipeline (full reprocess)"
	@echo ""
	@echo "Seed Data:"
	@echo "  make export-seed    Export current state as seed data"
	@echo "  make import-seed    Import seed data (resets DB first)"
	@echo ""
	@echo "Quality:"
	@echo "  make lint        Lint and format all code"
	@echo ""
	@echo "Utilities:"
	@echo "  make check-node  Verify Node.js is available"
	@echo "  make clean       Remove build artifacts and caches"

# =============================================================================
# Setup
# =============================================================================

check-node:
	@which node > /dev/null 2>&1 || (echo "Error: Node.js not found. Install via:" && \
		echo "  brew install node" && \
		echo "  or: curl -fsSL https://fnm.vercel.app/install | bash" && \
		exit 1)
	@echo "Node.js: $$(node --version)"
	@echo "npm: $$(npm --version)"

install: check-node
	cd backend && uv sync
	cd frontend && npm install
	@echo ""
	@echo "Setup complete! Run 'make dev' to start."

# =============================================================================
# Development
# =============================================================================

dev:
	@echo "Starting backend (8000) and frontend (3000)..."
	@make -j2 backend frontend

backend:
	cd backend && uv run python -m uvicorn server.main:app --reload --port 8000

frontend: check-node
	cd frontend && npm run dev

# =============================================================================
# Pipeline
# =============================================================================

pipeline:
	cd backend && uv run python -c "from core.runner import run; run()"

pipeline-full:
	cd backend && uv run python -c "from core.runner import run; run(full=True)"

# =============================================================================
# Seed Data
# =============================================================================

export-seed:
	cd backend && uv run python -c "from core.state import load_state; load_state().export_seed_data()"

import-seed:
	cd backend && uv run python -c "from core.state import load_state; load_state().import_seed_data(force=True)"

# =============================================================================
# Quality
# =============================================================================

lint: check-node
	cd backend && uvx ruff check . --fix && uvx ruff format .
	cd frontend && npm run lint

# =============================================================================
# Cleanup
# =============================================================================

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.venv frontend/node_modules 2>/dev/null || true
	@echo "Cleaned build artifacts"

#!/usr/bin/env bash
# Start a local Polygon fork via Anvil for OTC experiments.
#
# Usage:
#   ./experiments/onchain-otc/anvil.sh          # uses CHAINSTACK_NODE from .env
#   FORK_URL=https://... ./experiments/onchain-otc/anvil.sh  # override RPC
#
# Anvil exposes JSON-RPC on http://127.0.0.1:8545
# Pre-funded accounts (10k ETH each) are printed at startup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Load .env for CHAINSTACK_NODE
if [[ -f "$ROOT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "$ROOT_DIR/.env"
    set +a
fi

FORK_URL="${FORK_URL:-${CHAINSTACK_NODE:?Set CHAINSTACK_NODE in .env (must be archive node)}}"

echo "=== Anvil Polygon Fork ==="
echo "RPC source: ${FORK_URL%%\?*}"
echo "Local RPC:  http://127.0.0.1:8545"
echo "Chain ID:   137 (Polygon mainnet)"
echo ""

exec anvil \
    --fork-url "$FORK_URL" \
    --chain-id 137 \
    --accounts 5 \
    --balance 10000 \
    --host 127.0.0.1 \
    --port 8545

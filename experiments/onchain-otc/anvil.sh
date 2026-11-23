#!/usr/bin/env bash
# Start a local Polygon fork via Anvil for the on-chain OTC experiments.
#
# Usage (from the repo root):
#   ./experiments/onchain-otc/anvil.sh                        # CHAINSTACK_NODE from .env
#   FORK_URL=https://... ./experiments/onchain-otc/anvil.sh   # override the RPC
#
# Needs an archive-capable Polygon RPC. Anvil serves http://127.0.0.1:8545 with
# 5 test accounts (Anvil's public default keys). The fork keeps chain ID 137
# and the real contract addresses, so never put a real private key here.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Read only CHAINSTACK_NODE from .env (don't export every secret in it).
if [[ -z "${FORK_URL:-}" && -f "$ROOT_DIR/.env" ]]; then
    FORK_URL="$(grep -E '^CHAINSTACK_NODE=' "$ROOT_DIR/.env" | tail -n 1 | cut -d= -f2- | tr -d '"'"'")"
fi
: "${FORK_URL:?Set CHAINSTACK_NODE in .env or FORK_URL (archive Polygon RPC)}"

# Chainstack keys live in the URL path, so print the host only.
RPC_HOST="${FORK_URL#*://}"
RPC_HOST="${RPC_HOST%%/*}"

echo "=== Anvil Polygon fork ==="
echo "Forking:   $RPC_HOST"
echo "Local RPC: http://127.0.0.1:8545 (chain ID 137)"

# --quiet: Anvil's own banner would print the full fork URL, key included.
exec anvil \
    --quiet \
    --fork-url "$FORK_URL" \
    --chain-id 137 \
    --accounts 5 \
    --balance 10000 \
    --host 127.0.0.1 \
    --port 8545

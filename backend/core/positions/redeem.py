"""Payout of resolved markets, read on-chain.

v1 (CTF) markets: payoutNumerators / payoutDenominator on the Conditional
Tokens contract (denominator 0 = not resolved). v2 (Protocol V2) markets:
PositionManager.getPayout(positionId, amount), which reverts until resolved.
Same reads as polyclaw's `scripts/redeem.py`.
"""

from typing import Optional

from web3.exceptions import ContractLogicError

# Polymarket binary markets: outcome index 0 = YES, 1 = NO.
OUTCOME_INDEX = {"YES": 0, "NO": 1}

CTF_PAYOUT_ABI = [
    {
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "name": "payoutDenominator",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "conditionId", "type": "bytes32"},
            {"name": "index", "type": "uint256"},
        ],
        "name": "payoutNumerators",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

POSITION_MANAGER_PAYOUT_ABI = [
    {
        "inputs": [
            {"name": "positionId", "type": "uint256"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "getPayout",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

ONE_SHARE = 1_000_000  # 6 decimals


def v1_payout_rates(ctf, condition_id: str) -> Optional[tuple[float, float]]:
    """(YES, NO) pUSD per share of a resolved CTF market; None if unresolved."""
    cid = bytes.fromhex(condition_id.removeprefix("0x"))
    denominator = ctf.functions.payoutDenominator(cid).call()
    if not denominator:
        return None
    yes, no = (ctf.functions.payoutNumerators(cid, i).call() for i in range(2))
    return yes / denominator, no / denominator


def v2_payout_rate(position_manager, position_id: str) -> Optional[float]:
    """pUSD per share of a Protocol V2 position; None while unresolved."""
    try:
        paid = position_manager.functions.getPayout(int(position_id), ONE_SHARE).call()
    except ContractLogicError:
        return None
    return paid / ONE_SHARE

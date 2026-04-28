"""Polymarket contract addresses and ABIs (V2-only post-2026-04-28 cutover)."""

# Polygon mainnet — Polymarket V2 contracts (pUSD collateral).
# Confirmed against Polymarket docs 2026-04-26. CollateralOnramp wraps USDC.e
# -> pUSD; CollateralOfframp unwraps. PUSD is a proxy; PUSD_IMPL is impl.
CONTRACTS = {
    "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "CTF_EXCHANGE": "0xE111180000d2663C0091e4f400237545B87B996B",
    "NEG_RISK_CTF_EXCHANGE": "0xe2222d279d744050d28e00520010520000310F59",
    "PUSD": "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",
    "PUSD_IMPL": "0x6bBCef9f7ef3B6C592c99e0f206a0DE94Ad0925f",
    "COLLATERAL_ONRAMP": "0x93070a847efEf7F70739046A929D47a521F5B8ee",
    "COLLATERAL_OFFRAMP": "0x2957922Eb93258b93368531d39fAcCA3B4dC5854",
}

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

CTF_ABI = [
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_operator", "type": "address"},
            {"name": "_approved", "type": "bool"},
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "splitPosition",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "mergePositions",
        "outputs": [],
        "type": "function",
    },
]

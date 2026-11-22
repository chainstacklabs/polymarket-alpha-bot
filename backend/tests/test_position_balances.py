"""Position balances must be read from the deposit wallet, not the signing EOA.

Splits and fills run through the sigtype-3 deposit wallet, so the outcome
tokens live there. Reading the EOA made every position show 0 and blocked
sell / merge / exit.
"""

from unittest.mock import MagicMock

from web3 import Web3

from core.positions.service import PositionService

EOA = "0x9206e4a093ca21b23a770a96cb223e83e968f151"
DEPOSIT = "0x21c79811788d075ed5c701a0a4875d73c5c92c4f"


def _service(deposit: str | None = DEPOSIT) -> tuple[PositionService, MagicMock]:
    wallet = MagicMock()
    wallet.address = EOA
    wallet.deposit_wallet_address = deposit
    svc = PositionService(storage=MagicMock(), wallet=wallet)
    ctf = MagicMock()
    ctf.functions.balanceOf.return_value.call.return_value = 2_500_000
    ctf.functions.balanceOf.return_value._encode_transaction_data.return_value = b""
    svc._get_ctf_contract = lambda: ctf
    w3 = MagicMock()
    w3.eth.contract.return_value = ctf
    svc._get_web3 = lambda: w3
    return svc, ctf


def _owners(ctf: MagicMock) -> set[str]:
    return {c.args[0] for c in ctf.functions.balanceOf.call_args_list}


def test_single_balance_reads_deposit_wallet():
    svc, ctf = _service()
    assert svc._get_token_balance_single("123") == 2.5
    assert _owners(ctf) == {Web3.to_checksum_address(DEPOSIT)}


def test_batch_balance_reads_deposit_wallet():
    svc, ctf = _service()
    multicall = MagicMock()
    multicall.functions.aggregate.return_value.call.return_value = (
        1,
        [(3_000_000).to_bytes(32, "big"), (0).to_bytes(32, "big")],
    )
    svc._get_multicall_contract = lambda: multicall

    assert svc._get_balances_batch(["1", "2"]) == [3.0, 0.0]
    assert _owners(ctf) == {Web3.to_checksum_address(DEPOSIT)}


def test_no_deposit_wallet_means_zero_without_rpc():
    svc, ctf = _service(deposit=None)
    assert svc._get_balances_batch(["1", "2"]) == [0.0, 0.0]
    assert svc._get_token_balance_single("1") == 0.0
    ctf.functions.balanceOf.assert_not_called()


def test_batch_reads_each_leg_from_its_protocol_ledger():
    """v1 legs read the CTF, v2 legs read the PositionManager."""
    from core.wallet.contracts import CONTRACTS

    svc, _ = _service()
    multicall = MagicMock()
    multicall.functions.aggregate.return_value.call.return_value = (
        1,
        [(1_000_000).to_bytes(32, "big"), (2_000_000).to_bytes(32, "big")],
    )
    svc._get_multicall_contract = lambda: multicall

    assert svc._get_balances_batch(["1", "2"], ["v1", "v2"]) == [1.0, 2.0]
    calls = multicall.functions.aggregate.call_args.args[0]
    assert [target for target, _ in calls] == [
        Web3.to_checksum_address(CONTRACTS["CTF"]),
        Web3.to_checksum_address(CONTRACTS["POSITION_MANAGER"]),
    ]


def test_single_balance_v2_uses_position_manager():
    from core.wallet.contracts import CONTRACTS

    svc, _ = _service()
    w3 = MagicMock()
    w3.eth.contract.return_value.functions.balanceOf.return_value.call.return_value = 5_000_000
    svc._get_web3 = lambda: w3

    assert svc._get_token_balance_single("9", "v2") == 5.0
    assert w3.eth.contract.call_args.kwargs["address"] == Web3.to_checksum_address(
        CONTRACTS["POSITION_MANAGER"]
    )

"""Positions belong to the wallet that received their split tokens.

Live 2026-11-23: after importing a different wallet, the Positions page still
listed 9 positions from an older wallet (treasury EOA) with made-up P&L, and
their share claims took part in splitting shared balances.
"""

from unittest.mock import MagicMock

from hexbytes import HexBytes
from web3 import Web3

from core.positions.service import PositionService, holder_of, split_receivers
from core.positions.storage import PositionStorage

DEPOSIT = "0x20b5c328b651ef3d9696ab7152051bf23313b222"
ADAPTER = "0xada100db00ca00073811820692005400218fce1f"
OLD_EOA = "0x1a0d3129cf6bba90adc95c1048b60a9981cafe71"

TRANSFER_SINGLE = Web3.keccak(
    text="TransferSingle(address,address,address,uint256,uint256)"
)
TRANSFER_BATCH = Web3.keccak(
    text="TransferBatch(address,address,address,uint256[],uint256[])"
)


def _topic(address: str) -> HexBytes:
    return HexBytes("0x" + "0" * 24 + address.removeprefix("0x"))


def _log(event: HexBytes, to: str) -> dict:
    return {"topics": [event, _topic(ADAPTER), _topic("0x" + "0" * 40), _topic(to)]}


def test_split_receivers_reads_erc1155_recipients():
    receipt = {
        "logs": [
            _log(TRANSFER_BATCH, ADAPTER),
            _log(TRANSFER_SINGLE, DEPOSIT),
            {"topics": [HexBytes(b"\x01" * 32)]},  # unrelated event
        ]
    }
    assert split_receivers(receipt) == {ADAPTER, DEPOSIT}


def test_holder_ignores_polymarket_adapters():
    # A v1 split from an earlier deposit wallet: adapter + that wallet.
    old_deposit = "0x21c79811788d075ed5c701a0a4875d73c5c92c4f"
    assert holder_of({ADAPTER, old_deposit}, DEPOSIT) == old_deposit


def test_undecided_tag_is_not_stored(tmp_path):
    svc, storage = _service(
        tmp_path, [_entry("a")], {"0xa": {OLD_EOA, "0x" + "99" * 20}}
    )
    svc.get_all_live()
    assert storage.get("a").get("wallet") is None  # retried next time


def test_holder_prefers_the_current_wallet():
    # v1 deposit-wallet splits mint to an adapter, which forwards to the wallet.
    assert holder_of({ADAPTER, DEPOSIT}, DEPOSIT) == DEPOSIT
    assert holder_of({OLD_EOA}, DEPOSIT) == OLD_EOA
    assert holder_of({OLD_EOA, "0x" + "99" * 20}, DEPOSIT) == ""  # can't tell
    assert holder_of(set(), DEPOSIT) == ""


def _entry(pid: str, **extra) -> dict:
    return {
        "position_id": pid,
        "target_split_tx": f"0x{pid}",
        "cover_split_tx": "",
        **extra,
    }


def _service(tmp_path, entries, receivers: dict[str, set[str]]):
    storage = PositionStorage(tmp_path / "positions.json")
    storage.save_all(entries)
    wallet = MagicMock()
    wallet.deposit_wallet_address = Web3.to_checksum_address(DEPOSIT)
    svc = PositionService(storage=storage, wallet=wallet)
    svc._split_receivers = lambda tx: receivers[tx]
    svc._enrich_positions = lambda es: [
        MagicMock(position_id=e["position_id"]) for e in es
    ]
    return svc, storage


def test_list_shows_only_this_wallets_positions(tmp_path):
    svc, _ = _service(
        tmp_path,
        [
            _entry("mine", wallet=DEPOSIT),
            _entry("old", wallet=OLD_EOA),
            _entry("unknown", wallet=""),  # tagging couldn't tell: keep showing
        ],
        {"0xunknown": {OLD_EOA, ADAPTER}},
    )
    assert {p.position_id for p in svc.get_all_live()} == {"mine", "unknown"}


def test_untagged_positions_are_tagged_once_from_the_split(tmp_path):
    svc, storage = _service(
        tmp_path,
        [_entry("a"), _entry("b")],
        {"0xa": {ADAPTER, DEPOSIT}, "0xb": {OLD_EOA}},
    )
    assert {p.position_id for p in svc.get_all_live()} == {"a"}
    assert storage.get("a")["wallet"] == DEPOSIT
    assert storage.get("b")["wallet"] == OLD_EOA

    svc._split_receivers = MagicMock(side_effect=AssertionError("re-read"))
    svc.invalidate_cache()
    svc.get_all_live()  # tags are stored: no second receipt read


def test_other_wallets_claims_do_not_split_shared_balances(tmp_path):
    svc, _ = _service(
        tmp_path,
        [
            _entry("mine", wallet=DEPOSIT, target_wanted_shares=5.0),
            _entry("old", wallet=OLD_EOA, target_wanted_shares=5.0),
        ],
        {},
    )
    svc.leg_token_id = lambda entry, side, role: (
        "tok" if (side, role) == ("target", "wanted") else ""
    )
    refs = svc._token_refs()
    assert [c[0] for c in refs["tok"]] == ["mine"]


def test_no_wallet_set_up_shows_everything(tmp_path):
    svc, _ = _service(tmp_path, [_entry("old", wallet=OLD_EOA)], {})
    svc.wallet.deposit_wallet_address = None
    assert [p.position_id for p in svc.get_all_live()] == ["old"]

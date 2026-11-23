"""Replacing a wallet whose password is lost.

Live 2026-11-23: wallet.enc's password was lost, and import/generate both
refuse with "Wallet already exists", so the dashboard was stuck. Replace moves
the encrypted file aside (never deletes it) so a new wallet can be set up.
"""

import pytest

from core.wallet.manager import WalletManager
from core.wallet.storage import WalletStorage


def _manager(tmp_path) -> WalletManager:
    return WalletManager(WalletStorage(tmp_path / "wallet.enc"), rpc_url="")


def test_replace_moves_the_wallet_aside_and_locks(tmp_path):
    wm = _manager(tmp_path)
    wm.generate("old-password")
    wm.unlock("old-password")
    original = (tmp_path / "wallet.enc").read_bytes()

    backup = wm.replace()

    assert not wm.is_unlocked
    assert not wm.storage.exists()
    assert backup.parent == tmp_path
    assert backup.name.startswith("wallet.enc.replaced-")
    assert backup.read_bytes() == original  # kept, byte for byte
    # A new wallet can now be set up.
    wm.generate("new-password")
    assert wm.storage.exists()


def test_replace_twice_keeps_both_backups(tmp_path):
    wm = _manager(tmp_path)
    wm.generate("one-password")
    first = wm.replace()
    wm.generate("two-password")
    second = wm.replace()
    assert first != second
    assert first.exists() and second.exists()


def test_replace_without_a_wallet_is_refused(tmp_path):
    with pytest.raises(ValueError, match="No wallet"):
        _manager(tmp_path).replace()

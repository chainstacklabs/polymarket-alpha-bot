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


# ── HTTP route: needs an explicit confirm, refused while unlocked ─────────


def _client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.routers import wallet as wallet_router

    wm = _manager(tmp_path)
    monkeypatch.setattr(wallet_router, "get_wallet_manager", lambda: wm)
    app = FastAPI()
    app.include_router(wallet_router.router, prefix="/wallet")
    return TestClient(app), wm


def test_route_needs_confirm(tmp_path, monkeypatch):
    client, wm = _client(tmp_path, monkeypatch)
    wm.generate("pw-123456")
    assert client.post("/wallet/replace").status_code == 422
    assert client.post("/wallet/replace", json={"confirm": False}).status_code == 400
    assert wm.storage.exists()
    assert client.post("/wallet/replace", json={"confirm": True}).status_code == 200
    assert not wm.storage.exists()


def test_route_refused_while_unlocked(tmp_path, monkeypatch):
    # Replacing mid-trade would lock the key between the legs of an exit.
    client, wm = _client(tmp_path, monkeypatch)
    wm.generate("pw-123456")
    wm.unlock("pw-123456")
    r = client.post("/wallet/replace", json={"confirm": True})
    assert r.status_code == 409
    assert wm.storage.exists() and wm.is_unlocked


def test_route_without_a_wallet_is_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.post("/wallet/replace", json={"confirm": True}).status_code == 400


def test_replace_clears_the_relayer_key(tmp_path):
    wm = _manager(tmp_path)
    wm.generate("pw-123456")
    wm._relayer_key = "rk"  # as after unlock with relayer creds
    wm.replace()
    assert wm._relayer_key is None

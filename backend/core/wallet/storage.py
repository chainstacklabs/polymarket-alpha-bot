"""Encrypted wallet file storage."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

from core.wallet.encryption import encrypt_private_key, decrypt_private_key


class WalletData(TypedDict):
    address: str
    encrypted_key: str
    salt: str
    created_at: str
    # Deposit-wallet (sigtype-3) fields. Absent in legacy wallet.enc files —
    # the EOA stays the signer; the deposit wallet is the trading account.
    relayer_address: NotRequired[str]
    deposit_wallet_address: NotRequired[str]
    encrypted_relayer_key: NotRequired[str]
    relayer_salt: NotRequired[str]


class WalletStorage:
    """Manages encrypted wallet storage on disk."""

    def __init__(self, path: Path):
        self.path = path

    def exists(self) -> bool:
        """Check if wallet file exists."""
        return self.path.exists()

    def load(self) -> WalletData | None:
        """Load wallet data (without decrypting)."""
        if not self.exists():
            return None
        return json.loads(self.path.read_text())

    def _write(self, data: WalletData) -> WalletData:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2))
        return data

    def save(
        self,
        address: str,
        private_key: str,
        password: str,
        *,
        relayer_api_key: str | None = None,
        relayer_address: str | None = None,
    ) -> WalletData:
        """Encrypt and save wallet, optionally with relayer credentials."""
        encrypted_key, salt = encrypt_private_key(private_key, password)

        data: WalletData = {
            "address": address,
            "encrypted_key": encrypted_key,
            "salt": salt,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if relayer_api_key and relayer_address:
            enc_relayer, relayer_salt = encrypt_private_key(relayer_api_key, password)
            data["encrypted_relayer_key"] = enc_relayer
            data["relayer_salt"] = relayer_salt
            data["relayer_address"] = relayer_address

        return self._write(data)

    def set_relayer(
        self, relayer_api_key: str, relayer_address: str, password: str
    ) -> WalletData:
        """Attach/replace relayer credentials on an existing wallet (key encrypted)."""
        data = self.load()
        if not data:
            raise ValueError("No wallet found")
        enc_relayer, relayer_salt = encrypt_private_key(relayer_api_key, password)
        data["encrypted_relayer_key"] = enc_relayer
        data["relayer_salt"] = relayer_salt
        data["relayer_address"] = relayer_address
        return self._write(data)

    def set_deposit_wallet(self, deposit_wallet_address: str) -> WalletData:
        """Persist the (public) derived deposit-wallet address."""
        data = self.load()
        if not data:
            raise ValueError("No wallet found")
        data["deposit_wallet_address"] = deposit_wallet_address
        return self._write(data)

    def decrypt(self, password: str) -> str:
        """Decrypt and return the EOA private key."""
        data = self.load()
        if not data:
            raise ValueError("No wallet found")
        return decrypt_private_key(data["encrypted_key"], data["salt"], password)

    def decrypt_relayer_key(self, password: str) -> str | None:
        """Decrypt and return the relayer API key, or None if not set."""
        data = self.load()
        if not data:
            raise ValueError("No wallet found")
        enc = data.get("encrypted_relayer_key")
        salt = data.get("relayer_salt")
        if not enc or not salt:
            return None
        return decrypt_private_key(enc, salt, password)

    def delete(self) -> bool:
        """Delete wallet file."""
        if self.exists():
            self.path.unlink()
            return True
        return False

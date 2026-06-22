"""Wallet management: generate, import, unlock, deposit-wallet status.

The EOA is the signing key; the deposit wallet (CREATE2-derived, deployed via
the relayer) is the trading account that holds pUSD + CTF tokens (sigtype 3).
Balances/approvals are read on-chain against the deposit wallet; deploy and
approvals are submitted gaslessly via the polymarket-client SDK.
"""

from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from web3 import Web3
from loguru import logger

from core.wallet.storage import WalletStorage
from core.wallet.contracts import CONTRACTS, ERC20_ABI, CTF_ABI


@dataclass
class WalletBalances:
    pol: float  # EOA POL — informational only (relayer pays gas)
    pusd: float  # deposit-wallet pUSD (CLOB buying power)


@dataclass
class WalletStatus:
    exists: bool
    address: Optional[str]
    unlocked: bool
    balances: Optional[WalletBalances]
    approvals_set: bool
    relayer_set: bool
    deposit_wallet_address: Optional[str]
    deposit_wallet_deployed: bool


class WalletManager:
    """Manages wallet lifecycle and blockchain interactions."""

    def __init__(self, storage: WalletStorage, rpc_url: str):
        self.storage = storage
        self.rpc_url = rpc_url
        self._unlocked_key: Optional[str] = None
        self._relayer_key: Optional[str] = None
        self._address: Optional[str] = None

    @property
    def is_unlocked(self) -> bool:
        return self._unlocked_key is not None

    @property
    def address(self) -> Optional[str]:
        if self._address:
            return self._address
        data = self.storage.load()
        return data["address"] if data else None

    @property
    def relayer_address(self) -> Optional[str]:
        data = self.storage.load()
        return data.get("relayer_address") if data else None

    @property
    def deposit_wallet_address(self) -> Optional[str]:
        data = self.storage.load()
        return data.get("deposit_wallet_address") if data else None

    def _get_web3(self) -> Web3:
        return Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60}))

    # ── lifecycle ────────────────────────────────────────────────────────

    def generate(
        self,
        password: str,
        relayer_api_key: str | None = None,
        relayer_address: str | None = None,
    ) -> str:
        """Generate new wallet, encrypt and save (optionally with relayer creds)."""
        if self.storage.exists():
            raise ValueError("Wallet already exists")

        account = Account.create()
        self.storage.save(
            account.address,
            account.key.hex(),
            password,
            relayer_api_key=relayer_api_key,
            relayer_address=relayer_address or account.address,
        )
        logger.info(f"Generated new wallet: {account.address}")
        return account.address

    def import_key(
        self,
        private_key: str,
        password: str,
        relayer_api_key: str | None = None,
        relayer_address: str | None = None,
    ) -> str:
        """Import existing private key, encrypt and save (optionally with relayer creds)."""
        if self.storage.exists():
            raise ValueError("Wallet already exists")

        account = Account.from_key(private_key)
        self.storage.save(
            account.address,
            private_key,
            password,
            relayer_api_key=relayer_api_key,
            relayer_address=relayer_address or account.address,
        )
        logger.info(f"Imported wallet: {account.address}")
        return account.address

    def set_relayer(
        self, relayer_api_key: str, relayer_address: str, password: str
    ) -> None:
        """Attach/replace relayer credentials on an existing wallet."""
        self.storage.set_relayer(relayer_api_key, relayer_address, password)
        if self.is_unlocked:
            self._relayer_key = relayer_api_key
        logger.info("Relayer credentials set")

    def unlock(self, password: str) -> str:
        """Decrypt the EOA key (and relayer key, if set) into memory."""
        self._unlocked_key = self.storage.decrypt(password)
        self._relayer_key = self.storage.decrypt_relayer_key(password)
        data = self.storage.load()
        self._address = data["address"] if data else None
        logger.info(f"Wallet unlocked: {self._address}")
        return self._address

    def lock(self) -> None:
        """Clear secrets from memory."""
        self._unlocked_key = None
        self._relayer_key = None
        logger.info("Wallet locked")

    def get_unlocked_key(self) -> str:
        """Get the unlocked EOA private key (for signing)."""
        if not self._unlocked_key:
            raise ValueError("Wallet not unlocked")
        return self._unlocked_key

    def get_relayer_api_key(self) -> Optional[str]:
        """Get the decrypted relayer API key (requires unlock)."""
        if not self.is_unlocked:
            raise ValueError("Wallet not unlocked")
        return self._relayer_key

    # ── deposit wallet (sigtype 3) ───────────────────────────────────────

    def deploy_deposit_wallet(self) -> str:
        """Deploy the deposit wallet + set trading approvals (gasless via relayer).

        Idempotent: the SDK no-ops if already deployed/approved. Persists the
        derived deposit-wallet address. Returns it.
        """
        from core.trading.secure_client import get_secure_client

        client = get_secure_client(self)
        if client is None:
            raise ValueError(
                "Could not init trading client (unlock + relayer key required)"
            )

        # SecureClient.create() already deploys the deposit wallet (b8:
        # setup_gasless_wallet/is_gasless_ready are deprecated no-ops).
        deposit_address = str(client.wallet)
        self._try_approvals(client)
        self.storage.set_deposit_wallet(deposit_address)
        logger.info(f"Deposit wallet ready: {deposit_address}")
        return deposit_address

    @staticmethod
    def _try_approvals(client) -> None:
        """Best-effort trading approvals.

        The relayer allowlist may reject some operators in the SDK's approval
        set, and split/merge/orders recover allowances on demand, so a
        rejection here must never block onboarding or trading.
        """
        try:
            client.setup_trading_approvals()
        except Exception as e:
            logger.warning(
                f"setup_trading_approvals skipped (allowances self-recover): {e}"
            )

    def set_approvals(self) -> str:
        """Best-effort trading approvals on the deposit wallet via the relayer."""
        from core.trading.secure_client import get_secure_client

        client = get_secure_client(self)
        if client is None:
            raise ValueError(
                "Could not init trading client (unlock + relayer key required)"
            )
        self._try_approvals(client)
        deposit_address = str(client.wallet)
        self.storage.set_deposit_wallet(deposit_address)
        return deposit_address

    # ── on-chain reads (deposit wallet; no unlock needed) ────────────────

    def get_balances(self) -> WalletBalances:
        """POL (EOA, informational) + pUSD (deposit-wallet buying power)."""
        address = self.address
        if not address:
            raise ValueError("No wallet configured")

        w3 = self._get_web3()
        eoa = Web3.to_checksum_address(address)
        pol = float(w3.from_wei(w3.eth.get_balance(eoa), "ether"))

        pusd_balance = 0.0
        deposit = self.deposit_wallet_address
        if deposit:
            pusd = w3.eth.contract(
                address=Web3.to_checksum_address(CONTRACTS["PUSD"]),
                abi=ERC20_ABI,
            )
            # pUSD is a 6-decimal ERC-20 (Polymarket V2 collateral).
            pusd_balance = (
                pusd.functions.balanceOf(Web3.to_checksum_address(deposit)).call() / 1e6
            )

        return WalletBalances(pol=pol, pusd=pusd_balance)

    def check_approvals(self) -> bool:
        """Check trading approvals on the deposit wallet."""
        deposit = self.deposit_wallet_address
        if not deposit:
            return False

        w3 = self._get_web3()
        checksum = Web3.to_checksum_address(deposit)

        pusd = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["PUSD"]),
            abi=ERC20_ABI,
        )
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        collateral_spenders = [
            CONTRACTS["CTF"],
            CONTRACTS["CTF_EXCHANGE"],
            CONTRACTS["NEG_RISK_CTF_EXCHANGE"],
        ]
        ctf_operators = [
            CONTRACTS["CTF_EXCHANGE"],
            CONTRACTS["NEG_RISK_CTF_EXCHANGE"],
        ]

        for spender in collateral_spenders:
            if pusd.functions.allowance(checksum, spender).call() == 0:
                return False
        for operator in ctf_operators:
            if not ctf.functions.isApprovedForAll(checksum, operator).call():
                return False
        return True

    def _is_deposit_wallet_deployed(self) -> bool:
        deposit = self.deposit_wallet_address
        if not deposit:
            return False
        w3 = self._get_web3()
        code = w3.eth.get_code(Web3.to_checksum_address(deposit))
        return len(code) > 0

    def get_status(self) -> WalletStatus:
        """Get complete wallet status."""
        exists = self.storage.exists()
        address = self.address
        data = self.storage.load() if exists else None

        balances = None
        approvals_set = False
        deployed = False

        if exists and address:
            try:
                balances = self.get_balances()
                approvals_set = self.check_approvals()
                deployed = self._is_deposit_wallet_deployed()
            except Exception as e:
                logger.warning(f"Failed to fetch wallet status: {e}")

        return WalletStatus(
            exists=exists,
            address=address,
            unlocked=self.is_unlocked,
            balances=balances,
            approvals_set=approvals_set,
            relayer_set=bool(data and data.get("encrypted_relayer_key")),
            deposit_wallet_address=data.get("deposit_wallet_address") if data else None,
            deposit_wallet_deployed=deployed,
        )

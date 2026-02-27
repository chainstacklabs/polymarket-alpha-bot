"""Wallet management: generate, import, unlock, status."""

from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from web3 import Web3
from loguru import logger

from core.wallet.storage import WalletStorage
from core.wallet.contracts import CONTRACTS, ERC20_ABI, CTF_ABI


@dataclass
class WalletBalances:
    pol: float
    usdc_e: float


@dataclass
class WalletStatus:
    exists: bool
    address: Optional[str]
    unlocked: bool
    balances: Optional[WalletBalances]
    approvals_set: bool


class WalletManager:
    """Manages wallet lifecycle and blockchain interactions."""

    def __init__(self, storage: WalletStorage, rpc_url: str):
        self.storage = storage
        self.rpc_url = rpc_url
        self._unlocked_key: Optional[str] = None
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

    def _get_web3(self) -> Web3:
        return Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60}))

    def generate(self, password: str) -> str:
        """Generate new wallet, encrypt and save."""
        if self.storage.exists():
            raise ValueError("Wallet already exists")

        account = Account.create()
        self.storage.save(account.address, account.key.hex(), password)
        logger.info(f"Generated new wallet: {account.address}")
        return account.address

    def import_key(self, private_key: str, password: str) -> str:
        """Import existing private key, encrypt and save."""
        if self.storage.exists():
            raise ValueError("Wallet already exists")

        account = Account.from_key(private_key)
        self.storage.save(account.address, private_key, password)
        logger.info(f"Imported wallet: {account.address}")
        return account.address

    def unlock(self, password: str) -> str:
        """Decrypt private key into memory."""
        self._unlocked_key = self.storage.decrypt(password)
        data = self.storage.load()
        self._address = data["address"] if data else None
        logger.info(f"Wallet unlocked: {self._address}")
        return self._address

    def lock(self) -> None:
        """Clear private key from memory."""
        self._unlocked_key = None
        logger.info("Wallet locked")

    def get_balances(self) -> WalletBalances:
        """Get POL and USDC.e balances."""
        address = self.address
        if not address:
            raise ValueError("No wallet configured")

        w3 = self._get_web3()
        checksum = Web3.to_checksum_address(address)

        pol = float(w3.from_wei(w3.eth.get_balance(checksum), "ether"))

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            abi=ERC20_ABI,
        )
        usdc_balance = usdc.functions.balanceOf(checksum).call() / 1e6

        return WalletBalances(pol=pol, usdc_e=usdc_balance)

    def check_approvals(self) -> bool:
        """Check if all Polymarket approvals are set."""
        address = self.address
        if not address:
            return False

        w3 = self._get_web3()
        checksum = Web3.to_checksum_address(address)

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            abi=ERC20_ABI,
        )
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        # Check USDC approvals
        for contract in [
            "CTF",
            "CTF_EXCHANGE",
            "NEG_RISK_CTF_EXCHANGE",
            "NEG_RISK_ADAPTER",
        ]:
            allowance = usdc.functions.allowance(checksum, CONTRACTS[contract]).call()
            if allowance == 0:
                return False

        # Check CTF approvals
        for contract in ["CTF_EXCHANGE", "NEG_RISK_CTF_EXCHANGE", "NEG_RISK_ADAPTER"]:
            approved = ctf.functions.isApprovedForAll(
                checksum, CONTRACTS[contract]
            ).call()
            if not approved:
                return False

        return True

    def get_status(self) -> WalletStatus:
        """Get complete wallet status."""
        exists = self.storage.exists()
        address = self.address

        balances = None
        approvals_set = False

        if exists and address:
            try:
                balances = self.get_balances()
                approvals_set = self.check_approvals()
            except Exception as e:
                logger.warning(f"Failed to fetch wallet status: {e}")

        return WalletStatus(
            exists=exists,
            address=address,
            unlocked=self.is_unlocked,
            balances=balances,
            approvals_set=approvals_set,
        )

    def set_approvals(self) -> list[str]:
        """Set all Polymarket contract approvals. Returns tx hashes."""
        if not self.is_unlocked:
            raise ValueError("Wallet must be unlocked")

        w3 = self._get_web3()
        address = Web3.to_checksum_address(self._address)
        account = w3.eth.account.from_key(self._unlocked_key)

        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["USDC_E"]),
            abi=ERC20_ABI,
        )
        ctf = w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACTS["CTF"]),
            abi=CTF_ABI,
        )

        MAX_UINT256 = 2**256 - 1
        tx_hashes = []

        approvals = [
            (usdc, "approve", CONTRACTS["CTF"], MAX_UINT256),
            (usdc, "approve", CONTRACTS["CTF_EXCHANGE"], MAX_UINT256),
            (usdc, "approve", CONTRACTS["NEG_RISK_CTF_EXCHANGE"], MAX_UINT256),
            (usdc, "approve", CONTRACTS["NEG_RISK_ADAPTER"], MAX_UINT256),
            (ctf, "setApprovalForAll", CONTRACTS["CTF_EXCHANGE"], True),
            (ctf, "setApprovalForAll", CONTRACTS["NEG_RISK_CTF_EXCHANGE"], True),
            (ctf, "setApprovalForAll", CONTRACTS["NEG_RISK_ADAPTER"], True),
        ]

        for contract, method, spender, value in approvals:
            fn = getattr(contract.functions, method)
            tx = fn(Web3.to_checksum_address(spender), value).build_transaction(
                {
                    "from": address,
                    "nonce": w3.eth.get_transaction_count(address),
                    "gas": 100000,
                    "gasPrice": w3.eth.gas_price,
                    "chainId": 137,
                }
            )

            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt["status"] != 1:
                raise ValueError(f"Approval failed: {tx_hash.hex()}")

            tx_hashes.append(tx_hash.hex())
            logger.info(f"Approval tx: {tx_hash.hex()[:20]}...")

        return tx_hashes

    def get_unlocked_key(self) -> str:
        """Get the unlocked private key (for trading)."""
        if not self._unlocked_key:
            raise ValueError("Wallet not unlocked")
        return self._unlocked_key

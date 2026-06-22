"""Wallet management API endpoints (deposit-wallet / sigtype-3 flow)."""

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.wallet.storage import WalletStorage
from core.wallet.manager import WalletManager


router = APIRouter()

# Singleton wallet manager
_wallet_manager: Optional[WalletManager] = None


def get_wallet_manager() -> WalletManager:
    """Get or create wallet manager singleton."""
    global _wallet_manager
    if _wallet_manager is None:
        wallet_path = Path("data/wallet.enc")
        rpc_url = os.environ.get("CHAINSTACK_NODE", "")
        if not rpc_url:
            raise HTTPException(
                status_code=500, detail="CHAINSTACK_NODE not configured"
            )

        storage = WalletStorage(wallet_path)
        _wallet_manager = WalletManager(storage, rpc_url)
    return _wallet_manager


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class PasswordRequest(BaseModel):
    password: str


class GenerateRequest(BaseModel):
    password: str
    relayer_api_key: Optional[str] = None
    relayer_address: Optional[str] = None


class ImportRequest(BaseModel):
    private_key: str
    password: str
    relayer_api_key: Optional[str] = None
    relayer_address: Optional[str] = None


class SetRelayerRequest(BaseModel):
    password: str
    relayer_api_key: str
    relayer_address: str


class WalletStatusResponse(BaseModel):
    exists: bool
    address: Optional[str]
    unlocked: bool
    balances: Optional[dict]
    approvals_set: bool
    relayer_set: bool
    deposit_wallet_address: Optional[str]
    deposit_wallet_deployed: bool


class GenerateResponse(BaseModel):
    address: str
    message: str


class UnlockResponse(BaseModel):
    unlocked: bool
    address: str


class DepositWalletResponse(BaseModel):
    success: bool
    deposit_wallet_address: str
    message: str


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("/status", response_model=WalletStatusResponse)
async def get_status():
    """Get wallet status including deposit-wallet state, balances, approvals."""
    manager = get_wallet_manager()
    status = manager.get_status()

    return WalletStatusResponse(
        exists=status.exists,
        address=status.address,
        unlocked=status.unlocked,
        balances={"pol": status.balances.pol, "pusd": status.balances.pusd}
        if status.balances
        else None,
        approvals_set=status.approvals_set,
        relayer_set=status.relayer_set,
        deposit_wallet_address=status.deposit_wallet_address,
        deposit_wallet_deployed=status.deposit_wallet_deployed,
    )


@router.post("/generate", response_model=GenerateResponse)
async def generate_wallet(req: GenerateRequest):
    """Generate a new wallet, optionally with relayer credentials."""
    manager = get_wallet_manager()
    try:
        address = manager.generate(
            req.password,
            relayer_api_key=req.relayer_api_key,
            relayer_address=req.relayer_address,
        )
        return GenerateResponse(
            address=address,
            message="Wallet created. Add a relayer key, deploy the deposit wallet, then send pUSD to it.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/import", response_model=GenerateResponse)
async def import_wallet(req: ImportRequest):
    """Import existing private key, optionally with relayer credentials."""
    manager = get_wallet_manager()
    try:
        address = manager.import_key(
            req.private_key,
            req.password,
            relayer_api_key=req.relayer_api_key,
            relayer_address=req.relayer_address,
        )
        return GenerateResponse(
            address=address,
            message="Wallet imported. Add a relayer key and deploy the deposit wallet.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/set-relayer", response_model=GenerateResponse)
async def set_relayer(req: SetRelayerRequest):
    """Attach/replace the relayer API key + address on an existing wallet."""
    manager = get_wallet_manager()
    try:
        manager.set_relayer(req.relayer_api_key, req.relayer_address, req.password)
        return GenerateResponse(
            address=req.relayer_address,
            message="Relayer credentials saved.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/unlock", response_model=UnlockResponse)
async def unlock_wallet(req: PasswordRequest):
    """Unlock wallet for trading (decrypt keys into memory)."""
    manager = get_wallet_manager()
    try:
        address = manager.unlock(req.password)
        return UnlockResponse(unlocked=True, address=address)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid password")


@router.post("/lock")
async def lock_wallet():
    """Lock wallet (clear secrets from memory)."""
    manager = get_wallet_manager()
    manager.lock()
    return {"locked": True}


@router.post("/deploy-deposit-wallet", response_model=DepositWalletResponse)
async def deploy_deposit_wallet():
    """Deploy the deposit wallet + set trading approvals (gasless, requires unlock + relayer key)."""
    manager = get_wallet_manager()
    if not manager.is_unlocked:
        raise HTTPException(status_code=401, detail="Unlock wallet first")
    try:
        deposit_address = manager.deploy_deposit_wallet()
        return DepositWalletResponse(
            success=True,
            deposit_wallet_address=deposit_address,
            message="Deposit wallet deployed and approved. Send pUSD to it to trade.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/approve-contracts", response_model=DepositWalletResponse)
async def approve_contracts():
    """Set trading approvals on the deposit wallet (requires unlock + relayer key)."""
    manager = get_wallet_manager()
    if not manager.is_unlocked:
        raise HTTPException(status_code=401, detail="Unlock wallet first")
    try:
        deposit_address = manager.set_approvals()
        return DepositWalletResponse(
            success=True,
            deposit_wallet_address=deposit_address,
            message="Trading approvals set on deposit wallet.",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

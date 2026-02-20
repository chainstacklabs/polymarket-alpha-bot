"""Position action endpoints - sell, merge, retry operations."""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from server.routers.wallet import get_wallet_manager
from server.routers.positions import get_storage, get_service
from core.positions.manager import PositionManager


router = APIRouter()

# Singleton
_manager: Optional[PositionManager] = None


def get_manager() -> PositionManager:
    """Get position manager instance (lazy init)."""
    global _manager
    if _manager is None:
        wallet = get_wallet_manager()
        storage = get_storage()
        service = get_service()
        _manager = PositionManager(wallet, storage, service)
    return _manager


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class SellTokenRequest(BaseModel):
    """Request to sell tokens for a position side."""

    side: Literal["target", "cover"]
    token_type: Literal["wanted", "unwanted"]


class SellTokenResponse(BaseModel):
    """Response from sell operation."""

    success: bool
    token_id: str
    amount: float
    order_id: Optional[str]
    filled: bool
    recovered_value: float
    error: Optional[str] = None


class MergeTokensRequest(BaseModel):
    """Request to merge YES+NO pairs."""

    side: Literal["target", "cover"]


class MergeTokensResponse(BaseModel):
    """Response from merge operation."""

    success: bool
    market_id: str
    merged_amount: float
    tx_hash: Optional[str]
    error: Optional[str] = None


class RetryPendingResponse(BaseModel):
    """Response from retry pending sells operation."""

    success: bool
    target_result: Optional[SellTokenResponse] = None
    cover_result: Optional[SellTokenResponse] = None
    message: str


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/{position_id}/sell", response_model=SellTokenResponse)
async def sell_position_tokens(position_id: str, req: SellTokenRequest):
    """
    Sell tokens from a position via CLOB.

    - side: "target" or "cover" market
    - token_type: "wanted" (your position) or "unwanted" (to be recovered)

    Uses IOC (Immediate-or-Cancel) market order at aggressive price for instant execution.
    """
    manager = get_manager()
    storage = get_storage()

    if not manager.wallet.is_unlocked:
        raise HTTPException(status_code=401, detail="Unlock wallet first")

    # Mark as selling (persists across page refresh)
    storage.update_selling_status(position_id, req.side, True)

    try:
        result = await manager.sell_position_tokens(
            position_id=position_id,
            side=req.side,
            token_type=req.token_type,
        )

        if not result.success and result.error == "Position not found":
            raise HTTPException(status_code=404, detail="Position not found")

        return SellTokenResponse(
            success=result.success,
            token_id=result.token_id,
            amount=result.amount,
            order_id=result.order_id,
            filled=result.filled,
            recovered_value=result.recovered_value,
            error=result.error,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Sell tokens failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clear selling status when done (success or failure)
        storage.update_selling_status(position_id, req.side, False)
        # Invalidate cache so next request gets fresh data
        get_service().invalidate_cache()


@router.post("/{position_id}/merge", response_model=MergeTokensResponse)
async def merge_position_tokens(position_id: str, req: MergeTokensRequest):
    """
    Merge YES+NO tokens back to USDC for a position side.

    Requires holding both YES and NO tokens. The mergeable amount is
    the minimum of the two balances. This is an on-chain operation
    that burns outcome tokens and returns USDC.e collateral.
    """
    manager = get_manager()

    if not manager.wallet.is_unlocked:
        raise HTTPException(status_code=401, detail="Unlock wallet first")

    try:
        result = await manager.merge_position_tokens(
            position_id=position_id,
            side=req.side,
        )

        if not result.success and result.error == "Position not found":
            raise HTTPException(status_code=404, detail="Position not found")

        return MergeTokensResponse(
            success=result.success,
            market_id=result.market_id,
            merged_amount=result.merged_amount,
            tx_hash=result.tx_hash,
            error=result.error,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Merge tokens failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{position_id}/retry", response_model=RetryPendingResponse)
async def retry_pending_sells(position_id: str):
    """
    Retry selling unwanted tokens for positions in pending state.

    Checks both target and cover sides for unwanted token balances
    and attempts to sell them via CLOB FOK orders.
    """
    manager = get_manager()

    if not manager.wallet.is_unlocked:
        raise HTTPException(status_code=401, detail="Unlock wallet first")

    try:
        result = await manager.retry_pending_sells(position_id)

        if result.get("message") == "Position not found":
            raise HTTPException(status_code=404, detail="Position not found")

        # Convert dict results to response models
        target_result = None
        cover_result = None

        if result.get("target_result"):
            tr = result["target_result"]
            target_result = SellTokenResponse(
                success=tr["success"],
                token_id=tr["token_id"],
                amount=tr["amount"],
                order_id=tr["order_id"],
                filled=tr["filled"],
                recovered_value=tr["recovered_value"],
                error=tr["error"],
            )

        if result.get("cover_result"):
            cr = result["cover_result"]
            cover_result = SellTokenResponse(
                success=cr["success"],
                token_id=cr["token_id"],
                amount=cr["amount"],
                order_id=cr["order_id"],
                filled=cr["filled"],
                recovered_value=cr["recovered_value"],
                error=cr["error"],
            )

        return RetryPendingResponse(
            success=result["success"],
            target_result=target_result,
            cover_result=cover_result,
            message=result["message"],
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Retry pending sells failed")
        raise HTTPException(status_code=500, detail=str(e))

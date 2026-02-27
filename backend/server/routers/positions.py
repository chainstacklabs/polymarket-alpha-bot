"""Positions API endpoints - track and manage entered portfolio pairs."""

from dataclasses import asdict
from typing import Optional
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from server.routers.wallet import get_wallet_manager
from core.positions.storage import PositionStorage, PositionEntry
from core.positions.service import PositionService

router = APIRouter()

# Singletons
_storage = PositionStorage()
_service: Optional[PositionService] = None


def get_storage() -> PositionStorage:
    """Get position storage instance."""
    return _storage


def get_service() -> PositionService:
    """Get position service instance (lazy init)."""
    global _service
    if _service is None:
        wallet = get_wallet_manager()
        _service = PositionService(_storage, wallet)
    return _service


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class CreatePositionRequest(BaseModel):
    """Request to record a new position entry (called after buy-pair succeeds)."""

    pair_id: str
    entry_amount_per_side: float

    target_market_id: str
    target_position: str
    target_token_id: str
    target_question: str
    target_entry_price: float
    target_split_tx: str
    target_clob_order_id: Optional[str] = None
    target_clob_filled: bool = False

    cover_market_id: str
    cover_position: str
    cover_token_id: str
    cover_question: str
    cover_entry_price: float
    cover_split_tx: str
    cover_clob_order_id: Optional[str] = None
    cover_clob_filled: bool = False


class UpdateNotesRequest(BaseModel):
    """Request to update position notes."""

    notes: str


class PositionResponse(BaseModel):
    """Single position with live data."""

    position_id: str
    pair_id: str
    entry_time: str
    entry_amount_per_side: float
    entry_total_cost: float

    target_market_id: str
    target_position: str
    target_token_id: str
    target_question: str
    target_entry_price: float
    target_group_slug: str
    target_split_tx: str
    target_clob_order_id: Optional[str]
    target_clob_filled: bool

    cover_market_id: str
    cover_position: str
    cover_token_id: str
    cover_question: str
    cover_entry_price: float
    cover_group_slug: str
    cover_split_tx: str
    cover_clob_order_id: Optional[str]
    cover_clob_filled: bool

    notes: Optional[str]

    # Live data - wanted tokens
    target_balance: float
    cover_balance: float
    target_current_price: float
    cover_current_price: float

    # Live data - unwanted tokens (for pending detection)
    target_unwanted_balance: float
    cover_unwanted_balance: float

    # Derived
    state: str
    entry_net_cost: float  # Actual cost after selling unwanted tokens
    realized_proceeds: float  # USDC recovered from exit sells/merges
    current_value: float
    pnl: float
    pnl_pct: float

    # Selling in progress flags
    selling_target: bool
    selling_cover: bool


class PositionsListResponse(BaseModel):
    """List of positions with summary stats."""

    count: int
    active_count: int
    total_pnl: float
    positions: list[PositionResponse]


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("", response_model=PositionsListResponse)
async def list_positions(state: Optional[str] = None):
    """
    Get all positions with live balances and prices.

    Optional filter by state: active, pending, partial, complete
    """
    service = get_service()

    try:
        positions = service.get_all_live()

        # Filter by state if specified
        if state:
            positions = [p for p in positions if p.state == state]

        # Calculate summary stats
        active_count = sum(
            1 for p in positions if p.state in ("active", "pending", "partial")
        )
        total_pnl = sum(p.pnl for p in positions)

        return PositionsListResponse(
            count=len(positions),
            active_count=active_count,
            total_pnl=round(total_pnl, 2),
            positions=[PositionResponse(**asdict(p)) for p in positions],
        )
    except Exception as e:
        logger.exception("Failed to fetch positions")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{position_id}", response_model=PositionResponse)
async def get_position(position_id: str):
    """Get single position with live data."""
    service = get_service()

    position = service.get_position(position_id)
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")

    return PositionResponse(**asdict(position))


@router.post("", response_model=dict)
async def create_position(req: CreatePositionRequest):
    """
    Record a new position entry.

    Called automatically after successful buy-pair, or manually for imports.
    """
    entry = PositionEntry(
        position_id=str(uuid4()),
        pair_id=req.pair_id,
        entry_time=datetime.utcnow().isoformat() + "Z",
        entry_amount_per_side=req.entry_amount_per_side,
        entry_total_cost=req.entry_amount_per_side * 2,
        target_market_id=req.target_market_id,
        target_position=req.target_position,
        target_token_id=req.target_token_id,
        target_question=req.target_question,
        target_entry_price=req.target_entry_price,
        target_split_tx=req.target_split_tx,
        target_clob_order_id=req.target_clob_order_id,
        target_clob_filled=req.target_clob_filled,
        cover_market_id=req.cover_market_id,
        cover_position=req.cover_position,
        cover_token_id=req.cover_token_id,
        cover_question=req.cover_question,
        cover_entry_price=req.cover_entry_price,
        cover_split_tx=req.cover_split_tx,
        cover_clob_order_id=req.cover_clob_order_id,
        cover_clob_filled=req.cover_clob_filled,
        notes=None,
    )

    _storage.add(entry)
    logger.info(f"Created position: {entry.position_id} for pair {req.pair_id}")

    return {"position_id": entry.position_id, "success": True}


@router.patch("/{position_id}/notes")
async def update_notes(position_id: str, req: UpdateNotesRequest):
    """Update position notes."""
    if not _storage.update_notes(position_id, req.notes):
        raise HTTPException(status_code=404, detail="Position not found")

    return {"success": True}


@router.delete("/{position_id}")
async def delete_position(position_id: str):
    """
    Delete position record.

    Note: This only removes the tracking record - it does NOT affect on-chain tokens.
    """
    if not _storage.delete(position_id):
        raise HTTPException(status_code=404, detail="Position not found")

    return {"success": True}

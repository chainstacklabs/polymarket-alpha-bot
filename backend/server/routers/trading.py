"""Trading API endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from server.routers.wallet import get_wallet_manager
from core.trading.executor import TradingExecutor


router = APIRouter()


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class BuyPairRequest(BaseModel):
    pair_id: str
    target_market_id: str
    target_position: str
    target_group_slug: str = ""
    cover_market_id: str
    cover_position: str
    cover_group_slug: str = ""
    amount_per_position: float
    skip_clob_sell: bool = False


class TradeResultModel(BaseModel):
    success: bool
    market_id: str
    position: str
    amount: float
    split_tx: Optional[str]
    clob_order_id: Optional[str]
    clob_filled: bool
    error: Optional[str] = None


class BuyPairResponse(BaseModel):
    success: bool
    pair_id: str
    target: TradeResultModel
    cover: TradeResultModel
    total_spent: float
    final_balances: dict
    warnings: list[str] = []  # CLOB failures, partial execution, etc.


class EstimateResponse(BaseModel):
    pair_id: str
    total_cost: float
    target_market: dict
    cover_market: dict
    wallet_balance: float
    sufficient_balance: bool


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/buy-pair", response_model=BuyPairResponse)
async def buy_pair(req: BuyPairRequest):
    """Execute a pair purchase (target + cover positions)."""
    wallet_manager = get_wallet_manager()

    if not wallet_manager.is_unlocked:
        raise HTTPException(status_code=401, detail="Unlock wallet first")

    executor = TradingExecutor(wallet_manager)

    try:
        result = await executor.buy_pair(
            pair_id=req.pair_id,
            target_market_id=req.target_market_id,
            target_position=req.target_position,
            cover_market_id=req.cover_market_id,
            cover_position=req.cover_position,
            amount_per_position=req.amount_per_position,
            skip_clob_sell=req.skip_clob_sell,
        )

        # Collect warnings for CLOB failures
        warnings = []
        if result.target.error:
            warnings.append(f"Target CLOB sell failed: {result.target.error}")
        if result.cover.error:
            warnings.append(f"Cover CLOB sell failed: {result.cover.error}")

        if warnings:
            is_geoblock = any(
                "geoblock" in (w or "").lower() or "restricted" in (w or "").lower()
                for w in warnings
            )
            if is_geoblock:
                warnings.append(
                    "CLOB sells blocked by region restriction. Enable a proxy, then go to Positions to sell unwanted tokens."
                )
            else:
                warnings.append(
                    "You hold both YES and NO tokens. Go to Positions to sell unwanted sides."
                )

        # Record position entry for tracking
        # Uses market info captured during trade - no extra API calls needed
        logger.info(
            f"Trade result.success={result.success}, attempting position recording"
        )
        if result.success:
            try:
                from datetime import datetime
                from uuid import uuid4
                from core.positions.storage import PositionStorage, PositionEntry

                entry = PositionEntry(
                    position_id=str(uuid4()),
                    pair_id=req.pair_id,
                    entry_time=datetime.utcnow().isoformat() + "Z",
                    entry_amount_per_side=req.amount_per_position,
                    entry_total_cost=req.amount_per_position * 2,
                    target_market_id=req.target_market_id,
                    target_position=req.target_position,
                    target_token_id=result.target.wanted_token_id,
                    target_question=result.target.question,
                    target_entry_price=result.target.entry_price,
                    target_split_tx=result.target.split_tx or "",
                    cover_market_id=req.cover_market_id,
                    cover_position=req.cover_position,
                    cover_token_id=result.cover.wanted_token_id,
                    cover_question=result.cover.question,
                    cover_entry_price=result.cover.entry_price,
                    cover_split_tx=result.cover.split_tx or "",
                    target_group_slug=req.target_group_slug,
                    cover_group_slug=req.cover_group_slug,
                    target_clob_order_id=result.target.clob_order_id,
                    target_clob_filled=result.target.clob_filled,
                    cover_clob_order_id=result.cover.clob_order_id,
                    cover_clob_filled=result.cover.clob_filled,
                )

                storage = PositionStorage()
                storage.add(entry)
                logger.info(
                    f"Recorded position: {entry.position_id} for pair {req.pair_id}"
                )
            except Exception as e:
                # Don't fail the trade if position recording fails
                logger.exception(f"Failed to record position: {e}")

        return BuyPairResponse(
            success=result.success,
            pair_id=result.pair_id,
            target=TradeResultModel(
                success=result.target.success,
                market_id=result.target.market_id,
                position=result.target.position,
                amount=result.target.amount,
                split_tx=result.target.split_tx,
                clob_order_id=result.target.clob_order_id,
                clob_filled=result.target.clob_filled,
                error=result.target.error,
            ),
            cover=TradeResultModel(
                success=result.cover.success,
                market_id=result.cover.market_id,
                position=result.cover.position,
                amount=result.cover.amount,
                split_tx=result.cover.split_tx,
                clob_order_id=result.cover.clob_order_id,
                clob_filled=result.cover.clob_filled,
                error=result.cover.error,
            ),
            total_spent=result.total_spent,
            final_balances=result.final_balances,
            warnings=warnings,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Buy pair failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/buy-pair/estimate", response_model=EstimateResponse)
async def estimate_buy_pair(req: BuyPairRequest):
    """Estimate costs for a pair purchase without executing."""
    wallet_manager = get_wallet_manager()
    executor = TradingExecutor(wallet_manager)

    try:
        target_market = await executor.get_market_info(req.target_market_id)
        cover_market = await executor.get_market_info(req.cover_market_id)

        total_cost = req.amount_per_position * 2
        balances = wallet_manager.get_balances()

        return EstimateResponse(
            pair_id=req.pair_id,
            total_cost=total_cost,
            target_market={
                "question": target_market.question[:60],
                "position": req.target_position,
                "price": target_market.yes_price
                if req.target_position == "YES"
                else target_market.no_price,
            },
            cover_market={
                "question": cover_market.question[:60],
                "position": req.cover_position,
                "price": cover_market.yes_price
                if req.cover_position == "YES"
                else cover_market.no_price,
            },
            wallet_balance=balances.usdc_e,
            sufficient_balance=balances.usdc_e >= total_cost,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

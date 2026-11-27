from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_session
from app.core.market_data import breakfast_service

router = APIRouter(prefix="/strategies/breakfast", tags=["breakfast-strategy"])


class BreakfastSettingsRequest(BaseModel):
    enabled: bool = True
    auto_start: bool = True
    mode: str = Field(default="PAPER", pattern="^PAPER$")
    top_stock_count: int = Field(default=3, ge=1, le=3)
    max_positions: int = Field(default=3, ge=1, le=3)
    max_trades_per_day: int = Field(default=3, ge=1, le=3)
    sector_breadth_percent: float = Field(default=60, ge=50, le=100)
    excessive_candle_percent: float = Field(default=5, gt=0, le=20)
    target_percent: float = Field(default=1, gt=0, le=10)
    stop_loss_percent: float = Field(default=1, gt=0, le=10)
    negative_nifty_long_target_percent: float = Field(default=0.5, gt=0, le=10)
    psychological_adjustment_enabled: bool = True
    psychological_adjustment_amount: float = Field(default=2, ge=0, le=20)
    entry_time: str = "09:21:00"
    exit_time: str = "15:15:00"


@router.get("/settings")
def settings(current=Depends(get_current_session)):
    return breakfast_service.settings()


@router.put("/settings")
def update_settings(payload: BreakfastSettingsRequest, current=Depends(get_current_session)):
    try:
        return breakfast_service.save_settings(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/settings/reset")
def reset_settings(current=Depends(get_current_session)):
    try:
        return breakfast_service.reset_settings()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/status")
def status(current=Depends(get_current_session)):
    return breakfast_service.snapshot()


@router.post("/start")
def start(current=Depends(get_current_session)):
    try:
        return breakfast_service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop(current=Depends(get_current_session)):
    return breakfast_service.stop()


@router.post("/positions/{position_id}/exit")
def manual_exit(position_id: str, current=Depends(get_current_session)):
    try:
        return breakfast_service.manual_exit(position_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/trades")
def trades(trade_date: date | None = Query(default=None, alias="date"), current=Depends(get_current_session)):
    return breakfast_service.trades(trade_date.isoformat() if trade_date else None)

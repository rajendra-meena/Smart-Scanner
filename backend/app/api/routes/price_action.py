from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import get_current_session
from app.core.market_data import price_action_service

router = APIRouter(prefix="/strategies/price-action", tags=["price-action-intraday"])


class PriceActionSettingsRequest(BaseModel):
    enabled: bool = True
    auto_start: bool = True
    mode: str = Field(default="PAPER", pattern="^PAPER$")
    max_positions: int = Field(default=3, ge=1)
    max_trades_per_day: int = Field(default=5, ge=1)
    stop_loss_points: float = Field(default=15, gt=0, le=500)
    risk_reward: float = Field(default=2.0, ge=1, le=5)
    max_spread_percent: float = Field(default=1.0, gt=0, le=5)
    volume_filter: bool = False
    volume_multiplier: float = Field(default=1.2, ge=1, le=5)
    exit_time: str = "15:15:00"
    max_daily_loss: float = Field(default=1500, gt=0)
    gainers_count: int = Field(default=20, ge=1, le=50)
    losers_count: int = Field(default=20, ge=1, le=50)

    @field_validator("exit_time")
    @classmethod
    def valid_exit_time(cls, value):
        try:
            from datetime import time
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Exit time must use HH:MM:SS format.") from exc
        if parsed <= time.fromisoformat("09:20:00"):
            raise ValueError("Exit time must be after 09:20:00 IST.")
        return parsed.isoformat(timespec="seconds")


@router.get("/settings")
def settings(current=Depends(get_current_session)):
    return price_action_service.settings()


@router.put("/settings")
def update_settings(payload: PriceActionSettingsRequest, current=Depends(get_current_session)):
    try:
        return price_action_service.save_settings(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/status")
def status(current=Depends(get_current_session)):
    return price_action_service.snapshot()


@router.post("/start")
def start(current=Depends(get_current_session)):
    try:
        return price_action_service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop(current=Depends(get_current_session)):
    return price_action_service.stop()



@router.post("/positions/{position_id}/exit")
def manual_exit(position_id: str, current=Depends(get_current_session)):
    try:
        return price_action_service.manual_exit(position_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

@router.get("/trades")
def trades(trade_date: date | None = Query(default=None, alias="date"), current=Depends(get_current_session)):
    return price_action_service.trades(trade_date.isoformat() if trade_date else None)

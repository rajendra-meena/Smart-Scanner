from datetime import date\nfrom fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from pydantic import field_validator

from app.api.dependencies import get_current_session
from app.core.market_data import opening_breakout_service


router = APIRouter(prefix="/strategies/opening-breakout", tags=["opening-breakout"])


class StartRequest(BaseModel):
    mode: str = Field(pattern="^(PAPER|LIVE)$")
    confirm_live: bool = False


class ConfigRequest(BaseModel):
    auto_start: bool = True
    gainers_count: int = Field(default=3, ge=1, le=20)
    losers_count: int = Field(default=3, ge=1, le=20)
    stop_loss_points: float = Field(default=15, gt=0, le=100)
    max_positions: int = Field(default=6, ge=1)
    max_spread_percent: float = Field(default=1.0, gt=0, le=10)
    exit_time: str = Field(default="09:30:00")

    @field_validator("exit_time")
    @classmethod
    def validate_exit_time(cls, value):
        try:
            from datetime import time
            parsed = time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Exit time must use HH:MM:SS format.") from exc
        if parsed <= time.fromisoformat("09:15:30"):
            raise ValueError("Exit time must be after 09:15:30 IST.")
        return parsed.isoformat(timespec="seconds")


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(PAPER|LIVE)$")


@router.get("/settings")
def settings(current=Depends(get_current_session)):
    return opening_breakout_service.settings()


@router.put("/settings")
def update_settings(payload: ConfigRequest, current=Depends(get_current_session)):
    try:
        opening_breakout_service.configure(payload.model_dump())
        return opening_breakout_service.settings()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/mode")
def update_mode(payload: ModeRequest, current=Depends(get_current_session)):
    try:
        return opening_breakout_service.set_mode(payload.mode)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/status")
def status(current=Depends(get_current_session)):
    return opening_breakout_service.snapshot()


@router.get("/trades")
def trades(
    trade_date: date | None = Query(default=None, alias="date"),
    current=Depends(get_current_session),
):
    return opening_breakout_service.trades(
        trade_date.isoformat() if trade_date else None
    )


@router.put("/config")
def configure(payload: ConfigRequest, current=Depends(get_current_session)):
    try:
        return opening_breakout_service.configure(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/start")
def start(payload: StartRequest, current=Depends(get_current_session)):
    if payload.mode == "LIVE":
        raise HTTPException(
            status_code=409,
            detail=(
                "Live execution is locked until real option tick subscriptions, "
                "attached SL lifecycle, partial fills, and broker square-off "
                "reconciliation are verified. Use Paper mode for development."
            ),
        )
    try:
        return opening_breakout_service.start(payload.mode, current.access_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop(current=Depends(get_current_session)):
    return opening_breakout_service.stop()

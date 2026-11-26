from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.api.dependencies import get_current_session
from app.core.opening_breakout.service import opening_breakout_service

router=APIRouter(prefix="/strategies/opening-breakout",tags=["opening-breakout"])
class StartRequest(BaseModel):
    mode:str=Field(pattern="^(PAPER|LIVE)$")
    confirm_live:bool=False
class ConfigRequest(BaseModel):
    stop_loss_points:float=Field(default=15,gt=0,le=100)
    max_positions:int=Field(default=6,ge=1,le=6)
    max_spread_percent:float=Field(default=1.0,gt=0,le=10)

@router.get("/status")
def status(current=Depends(get_current_session)): return opening_breakout_service.snapshot()
@router.get("/trades")
def trades(current=Depends(get_current_session)): return opening_breakout_service.trades()
@router.put("/config")
def configure(payload:ConfigRequest,current=Depends(get_current_session)): return opening_breakout_service.configure(payload.model_dump())
@router.post("/start")
def start(payload:StartRequest,current=Depends(get_current_session)):
    if payload.mode=="LIVE": raise HTTPException(status_code=409,detail="Live execution is locked until real option tick subscriptions, attached SL lifecycle, partial fills, and broker square-off reconciliation are verified. Use Paper mode for development.")
    try: return opening_breakout_service.start(payload.mode,current.access_token)
    except RuntimeError as exc: raise HTTPException(status_code=409,detail=str(exc)) from exc
@router.post("/stop")
def stop(current=Depends(get_current_session)): return opening_breakout_service.stop()

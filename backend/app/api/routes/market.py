from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from app.api.dependencies import get_current_session
from app.core.config import settings
from app.core.market_data import market_data_engine
from app.core.session_store import session_store

router = APIRouter(prefix="/market", tags=["market-data"])


def ensure_feed(current):
    if market_data_engine.snapshot()["status"] in ("disconnected", "error"):
        market_data_engine.start(settings.kite_api_key, current.access_token)


@router.get("/snapshot")
def snapshot(current=Depends(get_current_session)):
    ensure_feed(current)
    return market_data_engine.snapshot()


@router.get("/status")
def status(current=Depends(get_current_session)):
    return market_data_engine.snapshot()


@router.websocket("/stream")
async def stream(websocket: WebSocket):
    session = session_store.get(websocket.cookies.get(settings.session_cookie_name))
    if not session:
        await websocket.close(code=4401, reason="Zerodha session is not active")
        return
    await websocket.accept()
    try:
        if market_data_engine.snapshot()["status"] in ("disconnected", "error"):
            market_data_engine.start(settings.kite_api_key, session.access_token)
        async for message in market_data_engine.subscribe(websocket):
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        await websocket.close(code=1011, reason="Market stream unavailable")

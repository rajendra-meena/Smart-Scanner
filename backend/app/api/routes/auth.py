from datetime import datetime, time, timedelta, timezone
import logging
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from kiteconnect import KiteConnect
from app.api.dependencies import get_current_session
from app.core.config import settings
from app.core.market_data import market_data_engine
from app.core.session_store import session_store

router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def kite_client(access_token=None):
    if not settings.kite_api_key or not settings.kite_api_secret:
        raise HTTPException(status_code=503, detail="Kite API credentials are not configured on the backend.")
    client = KiteConnect(api_key=settings.kite_api_key)
    if access_token: client.set_access_token(access_token)
    return client

def next_session_expiry():
    now = datetime.now(IST)
    expiry = datetime.combine(now.date(), time(6), tzinfo=IST)
    return expiry + timedelta(days=1) if now >= expiry else expiry

@router.get("/zerodha/login")
def login():
    return {"login_url": kite_client().login_url()}

@router.get("/zerodha/callback")
def callback(request: Request):
    request_token = request.query_params.get("request_token")
    if request.query_params.get("status") != "success" or not request_token:
        return RedirectResponse(f"{settings.frontend_origin}/?auth=failed", status_code=303)
    try:
        data = kite_client().generate_session(request_token, api_secret=settings.kite_api_secret)
        expiry = next_session_expiry()
        user = {k: data.get(k) for k in ("user_id","user_name","user_shortname","email","broker","exchanges","products","login_time")}
        key, _ = session_store.create(data["access_token"], user, expiry)
        response = RedirectResponse(f"{settings.frontend_origin}/?auth=success", status_code=303)
        response.set_cookie(settings.session_cookie_name, key, expires=expiry.astimezone(timezone.utc), path="/", httponly=True,
                             secure=settings.session_cookie_secure, samesite="lax")
        return response
    except Exception as exc:
        logger.exception("Kite Connect session exchange failed (%s)", type(exc).__name__)
        return RedirectResponse(f"{settings.frontend_origin}/?auth=failed", status_code=303)

@router.get("/session")
def session(current=Depends(get_current_session)):
    return {"authenticated":True,"user":current.user,"expires_at":current.expires_at.isoformat()}

@router.post("/logout")
def logout(request: Request, response: Response):
    key = request.cookies.get(settings.session_cookie_name)
    current = session_store.delete(key)
    response.delete_cookie(settings.session_cookie_name, path="/", httponly=True,
                           secure=settings.session_cookie_secure, samesite="lax")
    if current:
        market_data_engine.stop(current.access_token)
        try:
            kite_client(current.access_token).invalidate_access_token()
        except Exception:
            logger.exception("Kite access token invalidation failed during logout")
    return {"authenticated":False}

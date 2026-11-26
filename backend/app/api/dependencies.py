from fastapi import HTTPException, Request, status
from app.core.config import settings
from app.core.session_store import session_store

def get_current_session(request: Request):
    session = session_store.get(request.cookies.get(settings.session_cookie_name))
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Zerodha session is not active. Please connect again.")
    return session

from dataclasses import dataclass
from datetime import datetime
from secrets import token_urlsafe
from threading import RLock

@dataclass
class UserSession:
    access_token: str
    user: dict
    expires_at: datetime

class InMemorySessionStore:
    """Single-process local development store; replace with Redis/DB in production."""
    def __init__(self):
        self._sessions: dict[str, UserSession] = {}
        self._lock = RLock()
    def create(self, access_token: str, user: dict, expires_at: datetime):
        key = token_urlsafe(32)
        value = UserSession(access_token, user, expires_at)
        with self._lock: self._sessions[key] = value
        return key, value
    def get(self, key: str | None):
        if not key: return None
        with self._lock:
            value = self._sessions.get(key)
            if value and value.expires_at <= datetime.now().astimezone():
                self._sessions.pop(key, None)
                return None
            return value
    def delete(self, key: str | None):
        if not key: return None
        with self._lock: return self._sessions.pop(key, None)

session_store = InMemorySessionStore()

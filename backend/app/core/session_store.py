import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from secrets import token_urlsafe
from threading import RLock
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


@dataclass
class UserSession:
    access_token: str
    user: dict
    expires_at: datetime


class SQLiteSessionStore:
    """Persistent single-user session store with encrypted Kite access tokens."""

    def __init__(self):
        key = settings.session_encryption_key
        if not key:
            raise RuntimeError(
                "SESSION_ENCRYPTION_KEY is required. Generate one with "
                "Fernet.generate_key() and keep it private in backend/.env."
            )
        try:
            self._cipher = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError("SESSION_ENCRYPTION_KEY must be a valid Fernet key.") from exc

        self._db_path = Path(settings.session_db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    @staticmethod
    def _digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self._db_path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_hash TEXT PRIMARY KEY,
                    encrypted_access_token BLOB NOT NULL,
                    user_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry ON user_sessions(expires_at)")

    def create(self, access_token: str, user: dict, expires_at: datetime):
        key = token_urlsafe(32)
        value = UserSession(access_token, user, expires_at)
        encrypted = self._cipher.encrypt(access_token.encode("utf-8"))
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO user_sessions
                    (session_hash, encrypted_access_token, user_json, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (self._digest(key), encrypted, json.dumps(user, default=str), expires_at.isoformat()),
            )
        return key, value

    def get(self, key: str | None):
        if not key:
            return None
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT encrypted_access_token, user_json, expires_at FROM user_sessions WHERE session_hash = ?",
                (self._digest(key),),
            ).fetchone()
            if row is None:
                return None
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= datetime.now().astimezone():
                db.execute("DELETE FROM user_sessions WHERE session_hash = ?", (self._digest(key),))
                return None
            try:
                access_token = self._cipher.decrypt(row["encrypted_access_token"]).decode("utf-8")
            except (InvalidToken, UnicodeDecodeError):
                db.execute("DELETE FROM user_sessions WHERE session_hash = ?", (self._digest(key),))
                return None
            return UserSession(access_token, json.loads(row["user_json"]), expires_at)

    def delete(self, key: str | None):
        if not key:
            return None
        current = self.get(key)
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM user_sessions WHERE session_hash = ?", (self._digest(key),))
        return current


session_store = SQLiteSessionStore()

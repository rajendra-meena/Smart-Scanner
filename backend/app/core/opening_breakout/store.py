"""SQLite persistence for strategy settings, events and trade audit.

Trade writes that cannot reach SQLite are placed in a fsynced JSONL recovery
journal and replayed idempotently when the database becomes available again.
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock


class OpeningBreakoutStore:
    def __init__(self, path: str = "./data/opening_breakout.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.recovery_path = self.path.with_suffix(self.path.suffix + ".recovery.jsonl")
        self.lock = RLock()
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS strategy_settings "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS strategy_trades "
                "(id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, symbol TEXT NOT NULL, "
                "mode TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
        with self.lock:
            self._replay_recovery_locked()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _insert_trade(db, trade: dict):
        db.execute(
            "INSERT OR REPLACE INTO strategy_trades "
            "VALUES (?,?,?,?,?,?)",
            (
                trade["id"],
                trade["trade_date"],
                trade["symbol"],
                trade["mode"],
                json.dumps(trade, default=str),
                datetime.now().isoformat(),
            ),
        )

    def _append_recovery(self, trade: dict):
        line = json.dumps(trade, default=str, separators=(",", ":"))
        with self.recovery_path.open("a", encoding="utf-8") as journal:
            journal.write(line + "\n")
            journal.flush()
            os.fsync(journal.fileno())

    def _replay_recovery_locked(self):
        if not self.recovery_path.exists():
            return 0
        try:
            lines = [
                line for line in self.recovery_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            pending = [json.loads(line) for line in lines]
            if not pending:
                self.recovery_path.unlink(missing_ok=True)
                return 0
            with self.connect() as db:
                for trade in pending:
                    self._insert_trade(db, trade)
            self.recovery_path.unlink(missing_ok=True)
            return len(pending)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            # Keep the journal intact for a later retry; never discard it on
            # a partial or failed replay.
            return 0


    def save_settings(self, values: dict):
        """Persist validated strategy settings atomically."""
        with self.lock:
            with self.connect() as db:
                for key, value in values.items():
                    db.execute(
                        "INSERT INTO strategy_settings (key, value, updated_at) "
                        "VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                        "value=excluded.value, updated_at=excluded.updated_at",
                        (key, json.dumps(value), datetime.now().isoformat()),
                    )

    def load_settings(self):
        with self.lock:
            with self.connect() as db:
                rows = db.execute(
                    "SELECT key, value FROM strategy_settings"
                ).fetchall()
            return {row["key"]: json.loads(row["value"]) for row in rows}

    def save_trade(self, trade: dict):
        with self.lock:
            try:
                with self.connect() as db:
                    self._insert_trade(db, trade)
            except sqlite3.Error:
                # False means the trade is safely queued in the recovery file,
                # not yet replayed into the primary SQLite table.
                self._append_recovery(trade)
                return False
            self._replay_recovery_locked()
            return True

    def trades(self, trade_date: str | None = None):
        with self.lock:
            self._replay_recovery_locked()
            with self.connect() as db:
                if trade_date:
                    rows = db.execute(
                        "SELECT payload FROM strategy_trades "
                        "WHERE trade_date=? ORDER BY created_at DESC",
                        (trade_date,),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT payload FROM strategy_trades "
                        "ORDER BY created_at DESC LIMIT 200"
                    ).fetchall()
            return [json.loads(row["payload"]) for row in rows]

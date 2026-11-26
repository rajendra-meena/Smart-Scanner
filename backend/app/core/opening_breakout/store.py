"""SQLite persistence for strategy settings, events and trade audit."""
import json, sqlite3
from pathlib import Path
from threading import RLock
from datetime import datetime

class OpeningBreakoutStore:
    def __init__(self, path: str = "./data/opening_breakout.sqlite3"):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self.lock = RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS strategy_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, symbol TEXT NOT NULL, mode TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
    def connect(self):
        db=sqlite3.connect(self.path, timeout=10); db.row_factory=sqlite3.Row; return db
    def save_trade(self, trade: dict):
        with self.lock, self.connect() as db:
            db.execute("INSERT OR REPLACE INTO strategy_trades VALUES (?,?,?,?,?,?)",(trade["id"],trade["trade_date"],trade["symbol"],trade["mode"],json.dumps(trade,default=str),datetime.now().isoformat()))
    def trades(self, trade_date: str | None = None):
        with self.connect() as db:
            if trade_date:
                rows=db.execute("SELECT payload FROM strategy_trades WHERE trade_date=? ORDER BY created_at DESC",(trade_date,)).fetchall()
            else:
                rows=db.execute("SELECT payload FROM strategy_trades ORDER BY created_at DESC LIMIT 200").fetchall()
            return [json.loads(r["payload"]) for r in rows]

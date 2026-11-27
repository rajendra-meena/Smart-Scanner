import json
import sqlite3
from threading import RLock

import pytest

from app.core.price_action_service import PriceActionService


def make_service(tmp_path, trade):
    service = PriceActionService.__new__(PriceActionService)
    service.lock = RLock()
    service.db_path = tmp_path / "price_action.sqlite3"
    service.positions = {}
    service.events = []
    with sqlite3.connect(service.db_path) as db:
        db.execute("CREATE TABLE price_action_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute(
            "INSERT INTO price_action_trades VALUES (?, ?, ?)",
            (trade["id"], "2026-11-27", json.dumps(trade)),
        )
    return service


def test_delete_trade_removes_completed_trade_from_database(tmp_path):
    trade = {"id": "trade-1", "status": "CLOSED", "symbol": "ABC", "pnl": 125}
    service = make_service(tmp_path, trade)

    assert service.delete_trade("trade-1") == {"deleted": True, "trade_id": "trade-1"}
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM price_action_trades").fetchone()[0] == 0
    assert service.events[-1]["event"] == "TRADE_DELETED"


def test_delete_trade_rejects_open_positions(tmp_path):
    trade = {"id": "trade-2", "status": "CLOSED", "symbol": "ABC"}
    service = make_service(tmp_path, trade)
    service.positions["trade-2"] = {"id": "trade-2", "status": "OPEN"}

    with pytest.raises(RuntimeError, match="Open positions cannot be deleted"):
        service.delete_trade("trade-2")


def test_delete_trade_rejects_missing_trade(tmp_path):
    service = make_service(tmp_path, {"id": "trade-3", "status": "CLOSED"})

    with pytest.raises(RuntimeError, match="not found"):
        service.delete_trade("missing")


def test_delete_trade_rejects_non_completed_record(tmp_path):
    service = make_service(tmp_path, {"id": "trade-4", "status": "OPEN"})

    with pytest.raises(RuntimeError, match="Only completed trades"):
        service.delete_trade("trade-4")

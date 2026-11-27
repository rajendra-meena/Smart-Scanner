import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import app.core.price_action_service as service_module
from app.core.price_action_service import PriceActionService

IST = ZoneInfo("Asia/Kolkata")


class FakeMarketData:
    def __init__(self):
        self.subscribed = []
    def subscribe_extra(self, tokens, full=True):
        self.subscribed.extend(tokens)
        return True
    def is_feed_fresh(self, max_age_seconds=5):
        return True


def test_open_paper_positions_are_restored_from_sqlite_after_service_restart(tmp_path, monkeypatch):
    db_path = tmp_path / "price_action_trades.sqlite3"
    position = {
        "id": "position-1", "strategy": "PRICE_ACTION_INTRADAY",
        "symbol": "ASHOKLEY", "option_symbol": "ASHOKLEY26DEC150PE",
        "direction": "PE", "mode": "PAPER", "status": "OPEN",
        "token": 123456, "quantity": 5000, "entry_price": 5.75,
        "stop_price": 4.90, "target_price": 7.45, "bid": 6.20,
        "ask": 6.25, "ltp": 6.24, "pnl": 2250.0,
        "opened_at": "2026-11-27T12:36:00+05:30",
    }
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE price_action_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_open_positions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute(
            "INSERT INTO price_action_open_positions VALUES (?, ?, ?)",
            (position["id"], json.dumps(position), datetime.now(IST).isoformat()),
        )

    monkeypatch.setattr(service_module, "Path", lambda _: db_path)
    market = FakeMarketData()
    service = PriceActionService(market)

    assert service.running is False
    assert service.snapshot()["open_positions"] == 1
    restored = service.snapshot()["positions"][0]
    assert restored["id"] == "position-1"
    assert restored["symbol"] == "ASHOKLEY"
    assert restored["quote_stale"] is True
    assert restored["pnl"] is None


def test_feed_reconnect_resubscribes_restored_option_position(tmp_path, monkeypatch):
    db_path = tmp_path / "price_action_trades.sqlite3"
    position = {
        "id": "position-2", "symbol": "MAZDOCK", "option_symbol": "MAZDOCK26DEC1900CE",
        "direction": "CE", "mode": "PAPER", "status": "OPEN", "token": 987654,
        "quantity": 225, "entry_price": 85.0, "stop_price": 72.25,
        "target_price": 110.5, "opened_at": datetime.now(IST).isoformat(),
    }
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE price_action_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_open_positions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO price_action_open_positions VALUES (?, ?, ?)", (
            position["id"], json.dumps(position), datetime.now(IST).isoformat()
        ))
    monkeypatch.setattr(service_module, "Path", lambda _: db_path)
    market = FakeMarketData()
    service = PriceActionService(market)

    service.on_feed_connected()

    assert market.subscribed == [987654]
    assert service.events[-1]["event"] == "OPEN_POSITIONS_RESTORED"


def test_closed_position_is_removed_from_active_positions_table(tmp_path, monkeypatch):
    db_path = tmp_path / "price_action_trades.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE price_action_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_open_positions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO price_action_open_positions VALUES (?, ?, ?)", (
            "position-3", json.dumps({"id": "position-3"}), datetime.now(IST).isoformat()
        ))
    monkeypatch.setattr(service_module, "Path", lambda _: db_path)
    service = PriceActionService(FakeMarketData())
    pos = {
        "id": "position-3", "symbol": "ABC", "status": "OPEN",
        "entry_price": 10.0, "quantity": 25,
    }
    service._close(pos, 11.0, "TARGET", datetime.now(IST))

    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM price_action_open_positions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM price_action_trades").fetchone()[0] == 1

def test_snapshot_reports_total_capital_used_only_for_open_positions(tmp_path, monkeypatch):
    db_path = tmp_path / "price_action_trades.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE price_action_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE price_action_open_positions (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
    monkeypatch.setattr(service_module, "Path", lambda _: db_path)
    service = PriceActionService(FakeMarketData())
    service.positions = {
        "open-1": {"id": "open-1", "status": "OPEN", "entry_price": 5.75, "quantity": 5000, "pnl": 2250},
        "open-2": {"id": "open-2", "status": "OPEN", "entry_price": 85.0, "quantity": 225, "pnl": -180},
        "closed-1": {"id": "closed-1", "status": "CLOSED", "entry_price": 100.0, "quantity": 100, "pnl": 500},
    }

    assert service.snapshot()["open_capital_used"] == 62500.0
    assert service.snapshot()["open_pnl"] == 2070.0

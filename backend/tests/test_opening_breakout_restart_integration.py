from datetime import datetime
from zoneinfo import ZoneInfo

import app.core.opening_breakout.service as service_module
from app.core.opening_breakout.service import OpeningBreakoutService
from app.core.opening_breakout.store import OpeningBreakoutStore


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 26, 9, 20, tzinfo=IST)


class RestartMarketData:
    def __init__(self):
        self.fresh = True
        self.subscriptions = []
        self.equity_rows = [{
            "symbol": "ABC",
            "instrument_token": 1,
            "close": 100,
            "open": 101,
            "change_percent": 1.0,
            "instrument_type": "EQ",
        }]
        self.option_rows = [{
            "instrument_token": 777,
            "tradingsymbol": "ABC26NOV100CE",
            "name": "ABC",
            "instrument_type": "CE",
            "expiry": NOW.date().isoformat(),
            "lot_size": 25,
            "strike": 100,
            "segment": "NFO-OPT",
        }]

    def is_feed_fresh(self, seconds):
        return self.fresh

    def snapshot(self):
        return {"status": "connected", "all_equity": self.equity_rows}

    def universe_symbols(self):
        return {"ABC"}

    def option_instruments(self):
        return list(self.option_rows)

    def subscribe_extra(self, tokens, full=False):
        self.subscriptions.append((list(tokens), full))
        return True


class NoopTimer:
    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.daemon = True
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


def test_sqlite_open_position_survives_service_restart_and_closes_after_reconnect(
    tmp_path, monkeypatch
):
    database = tmp_path / "opening-breakout.sqlite3"
    market_data = RestartMarketData()

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(service_module, "datetime", FixedDateTime)
    monkeypatch.setattr(service_module, "Timer", NoopTimer)
    monkeypatch.setattr(
        service_module,
        "OpeningBreakoutStore",
        lambda: OpeningBreakoutStore(str(database)),
    )

    # First backend process: an OPEN PAPER position exists when logout/stop occurs.
    first_process = OpeningBreakoutService(market_data)
    first_process.engine.set_universe(market_data.equity_rows, [])
    assert first_process.engine.reserve_entry("ABC")
    first_process.engine.mark_position_open("ABC", "PAPER-RESTART-777")
    position = {
        "id": "PAPER-RESTART-777",
        "trade_date": NOW.date().isoformat(),
        "symbol": "ABC",
        "option_symbol": "ABC26NOV100CE",
        "option_token": 777,
        "direction": "CE",
        "lot_size": 25,
        "quantity": 25,
        "mode": "PAPER",
        "status": "OPEN",
        "entry_price": 100.0,
        "stop_price": 85.0,
        "entry_count": 1,
        "exit_price": None,
        "pnl": None,
    }
    first_process.positions[position["id"]] = position
    first_process.store.save_trade(position)

    first_process.stop()

    persisted_after_stop = OpeningBreakoutStore(str(database)).trades()
    assert len(persisted_after_stop) == 1
    assert persisted_after_stop[0]["status"] == "OPEN"
    assert persisted_after_stop[0]["exit_price"] is None
    assert persisted_after_stop[0]["exit_reason"] == "MANUAL_STOP"

    # Second backend process: a new service/store instance reads the same SQLite DB.
    second_process = OpeningBreakoutService(market_data)
    assert second_process.positions == {}

    restored = second_process.on_feed_connected()

    assert restored == {"restored": 1, "unresolved": 1}
    assert second_process.positions[position["id"]]["status"] == "OPEN"
    assert second_process.engine.stocks["ABC"].active_position_id == position["id"]
    assert market_data.subscriptions == [([777], True)]

    # The reconnected feed supplies a fresh executable bid; the requested exit completes.
    second_process._handle_option_tick({
        "instrument_token": 777,
        "instrument_type": "CE",
        "last_price": 96.0,
        "depth": {
            "buy": [{"price": 95.5, "quantity": 100}],
            "sell": [{"price": 95.8, "quantity": 100}],
        },
    }, NOW)

    closed = second_process.positions[position["id"]]
    assert closed["status"] == "CLOSED"
    assert closed["exit_reason"] == "MANUAL_STOP"
    assert closed["exit_price"] == 95.5
    assert closed["pnl"] == -112.5

    persisted_after_exit = OpeningBreakoutStore(str(database)).trades()
    assert len(persisted_after_exit) == 1
    assert persisted_after_exit[0]["status"] == "CLOSED"
    assert persisted_after_exit[0]["exit_price"] == 95.5

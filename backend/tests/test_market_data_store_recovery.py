import sqlite3
from datetime import datetime, timezone

import app.core.market_data as market_data_module
from app.core.market_data import MarketDataEngine
from app.core.opening_breakout.store import OpeningBreakoutStore


def make_trade(trade_id="trade-1"):
    return {
        "id": trade_id,
        "trade_date": "2026-11-27",
        "symbol": "NIFTY",
        "mode": "PAPER",
        "status": "CLOSED_UNPRICED",
        "exit_price": None,
        "pnl": None,
    }


def test_feed_freshness_uses_monotonic_clock(monkeypatch):
    engine = MarketDataEngine()
    engine._status = "connected"
    engine._last_tick_monotonic = 100.0
    monkeypatch.setattr(market_data_module.clock, "monotonic", lambda: 104.0)

    assert engine.is_feed_fresh(5) is True

    monkeypatch.setattr(market_data_module.clock, "monotonic", lambda: 106.0)
    assert engine.is_feed_fresh(5) is False


def test_feed_freshness_requires_connected_status(monkeypatch):
    engine = MarketDataEngine()
    engine._last_tick_monotonic = 100.0
    monkeypatch.setattr(market_data_module.clock, "monotonic", lambda: 101.0)

    for status in ("disconnected", "reconnecting", "error", "connecting"):
        engine._status = status
        assert engine.is_feed_fresh(5) is False


def test_valid_market_tick_refreshes_monotonic_feed_timestamp(monkeypatch):
    engine = MarketDataEngine()
    engine._status = "connected"
    engine._instruments[123] = {
        "instrument_token": 123,
        "symbol": "ABC",
        "name": "ABC",
        "exchange": "NSE",
        "segment": "NSE",
        "instrument_type": "EQ",
    }
    monkeypatch.setattr(market_data_module.clock, "monotonic", lambda: 987.5)

    class TickConsumer:
        def on_market_tick(self, rows):
            self.rows = rows

    consumer = TickConsumer()
    monkeypatch.setattr(market_data_module, "opening_breakout_service", consumer)

    engine._on_ticks(None, [{
        "instrument_token": 123,
        "last_price": 101.0,
        "ohlc": {"open": 100.0, "high": 102.0, "low": 99.0, "close": 100.0},
        "volume": 1000,
    }])

    assert engine._last_tick_monotonic == 987.5
    assert consumer.rows[0]["last_price"] == 101.0


def test_store_journals_failed_trade_and_replays_after_database_recovers(tmp_path, monkeypatch):
    store = OpeningBreakoutStore(str(tmp_path / "opening_breakout.sqlite3"))
    trade = make_trade()
    original_connect = store.connect

    def unavailable_database():
        raise sqlite3.OperationalError("database is temporarily unavailable")

    monkeypatch.setattr(store, "connect", unavailable_database)

    assert store.save_trade(trade) is False
    assert store.recovery_path.exists()
    assert store.recovery_path.read_text(encoding="utf-8").strip()

    monkeypatch.setattr(store, "connect", original_connect)

    recovered = store.trades()
    assert recovered == [trade]
    assert not store.recovery_path.exists()


def test_store_recovery_replay_is_idempotent(tmp_path, monkeypatch):
    store = OpeningBreakoutStore(str(tmp_path / "opening_breakout.sqlite3"))
    trade = make_trade("trade-idempotent")
    original_connect = store.connect

    def unavailable_database():
        raise sqlite3.OperationalError("database is temporarily unavailable")

    monkeypatch.setattr(store, "connect", unavailable_database)
    assert store.save_trade(trade) is False
    monkeypatch.setattr(store, "connect", original_connect)

    # A replay followed by a normal read must not create duplicate trade rows.
    assert store.trades() == [trade]
    assert store.trades() == [trade]



def test_failed_option_subscription_rolls_back_local_registry():
    engine = MarketDataEngine()

    class FakeTicker:
        MODE_FULL = 3
        MODE_QUOTE = 1

        def __init__(self):
            self.subscribed = []
            self.unsubscribed = []

        def subscribe(self, tokens):
            self.subscribed.extend(tokens)

        def set_mode(self, mode, tokens):
            raise RuntimeError("mode update failed")

        def unsubscribe(self, tokens):
            self.unsubscribed.extend(tokens)

    ticker = FakeTicker()
    engine._ticker = ticker
    engine._option_instruments = [{
        "instrument_token": 501,
        "tradingsymbol": "ABC26NOV100CE",
        "name": "ABC",
        "instrument_type": "CE",
        "expiry": "2026-12-03",
        "lot_size": 25,
        "strike": 100,
    }]

    try:
        engine.subscribe_extra([501], full=True)
    except RuntimeError as exc:
        assert str(exc) == "mode update failed"
    else:
        raise AssertionError("Expected option subscription failure")

    assert ticker.subscribed == [501]
    assert ticker.unsubscribed == [501]
    assert 501 not in engine._subscribed
    assert 501 not in engine._instruments
    assert 501 not in engine._full_mode_tokens


def test_option_subscription_rejects_unknown_token_without_mutation():
    engine = MarketDataEngine()

    class FakeTicker:
        MODE_FULL = 3
        MODE_QUOTE = 1

        def subscribe(self, tokens):
            raise AssertionError("Unknown token must not be subscribed")

        def set_mode(self, mode, tokens):
            raise AssertionError("Unknown token must not change mode")

    engine._ticker = FakeTicker()
    engine._option_instruments = []

    assert engine.subscribe_extra([999], full=True) is False
    assert engine._subscribed == []
    assert engine._instruments == {}
    assert engine._full_mode_tokens == set()


def test_completed_trades_can_be_filtered_by_trading_date(tmp_path):
    store = OpeningBreakoutStore(str(tmp_path / "opening_breakout.sqlite3"))
    today = make_trade("today")
    yesterday = {**make_trade("yesterday"), "trade_date": "2026-11-26"}

    store.save_trade(today)
    store.save_trade(yesterday)

    assert store.trades("2026-11-27") == [today]
    assert store.trades("2026-11-26") == [yesterday]
    assert len(store.trades()) == 2


def test_delete_trade_removes_only_selected_persisted_record(tmp_path):
    store = OpeningBreakoutStore(str(tmp_path / "opening_breakout.sqlite3"))
    first = make_trade("delete-me")
    second = make_trade("keep-me")
    store.save_trade(first)
    store.save_trade(second)

    assert store.delete_trade("delete-me") is True
    assert store.delete_trade("missing") is False
    assert store.trades("2026-11-27") == [second]


def test_strategy_settings_persist_across_store_instances(tmp_path):
    path = str(tmp_path / "opening_breakout.sqlite3")
    first = OpeningBreakoutStore(path)
    first.save_settings({
        "selected_mode": "LIVE",
        "gainers_count": 5,
        "losers_count": 4,
        "stop_loss_points": 18.5,
    })

    second = OpeningBreakoutStore(path)
    assert second.load_settings() == {
        "selected_mode": "LIVE",
        "gainers_count": 5,
        "losers_count": 4,
        "stop_loss_points": 18.5,
    }


def test_strategy_settings_update_replaces_existing_values(tmp_path):
    store = OpeningBreakoutStore(str(tmp_path / "opening_breakout.sqlite3"))
    store.save_settings({"selected_mode": "LIVE", "gainers_count": 5})
    store.save_settings({"selected_mode": "PAPER"})

    assert store.load_settings() == {
        "selected_mode": "PAPER",
        "gainers_count": 5,
    }


def test_index_instruments_returns_only_loaded_nse_indices():
    engine = MarketDataEngine()
    engine._instruments = {
        101: {"instrument_token": 101, "symbol": "NIFTY 50", "exchange": "NSE", "segment": "INDICES", "instrument_type": "INDEX"},
        102: {"instrument_token": 102, "symbol": "NIFTY BANK", "exchange": "NSE", "segment": "INDICES", "instrument_type": "INDEX"},
        103: {"instrument_token": 103, "symbol": "RELIANCE", "exchange": "NSE", "segment": "NSE", "instrument_type": "EQ"},
        104: {"instrument_token": 104, "symbol": "ABC26NOV100CE", "exchange": "NFO", "segment": "NFO-OPT", "instrument_type": "CE"},
    }

    assert [row["symbol"] for row in engine.index_instruments()] == ["NIFTY 50", "NIFTY BANK"]


def test_historical_index_candles_uses_real_index_tokens_and_interval(monkeypatch):
    engine = MarketDataEngine()

    class FakeKite:
        def __init__(self):
            self.calls = []

        def historical_data(self, token, start_at, end_at, interval):
            self.calls.append((token, interval))
            return [{"open": 100, "high": 102, "low": 99, "close": 101, "volume": 0}]

    kite = FakeKite()
    engine._kite = kite
    engine._instruments = {
        101: {"instrument_token": 101, "symbol": "NIFTY 50", "exchange": "NSE", "segment": "INDICES"},
        102: {"instrument_token": 102, "symbol": "NIFTY BANK", "exchange": "NSE", "segment": "INDICES"},
        103: {"instrument_token": 103, "symbol": "RELIANCE", "exchange": "NSE", "segment": "NSE"},
    }
    monkeypatch.setattr(market_data_module.clock, "sleep", lambda seconds: None)

    result = engine.historical_index_candles(
        ["NIFTY 50", "NIFTY BANK", "RELIANCE", "UNKNOWN"],
        datetime(2026, 11, 27, 9, 15),
        datetime(2026, 11, 27, 10, 0),
        interval="5minute",
    )

    assert kite.calls == [(101, "5minute"), (102, "5minute")]
    assert len(result["NIFTY 50"]) == 1
    assert len(result["NIFTY BANK"]) == 1
    assert result["RELIANCE"] == []
    assert result["UNKNOWN"] == []


def test_historical_index_candles_rejects_unsupported_interval():
    engine = MarketDataEngine()

    try:
        engine.historical_index_candles(
            ["NIFTY 50"], datetime(2026, 11, 27, 9, 15),
            datetime(2026, 11, 27, 10, 0), interval="day",
        )
    except ValueError as exc:
        assert str(exc) == "Unsupported index candle interval."
    else:
        raise AssertionError("Unsupported interval must be rejected")

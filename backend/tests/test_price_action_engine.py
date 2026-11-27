from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.price_action_engine import PriceActionEngine

IST = ZoneInfo("Asia/Kolkata")


def bullish_sweep_candles():
    start = datetime(2026, 11, 27, 9, 20, tzinfo=IST)
    rows = []
    for index in range(8):
        rows.append({
            "minute": start + timedelta(minutes=index),
            "open": 99.5 + index * 0.15,
            "high": 100.0 + index * 0.35,
            "low": 98.0 + index * 0.25,
            "close": 99.7 + index * 0.2,
            "volume": 100,
        })
    rows[-1] = {
        "minute": start + timedelta(minutes=8),
        "open": 99.8,
        "high": 100.8,
        "low": 97.5,
        "close": 100.3,
        "volume": 100,
    }
    return rows


def test_bullish_structure_and_liquidity_sweep_generates_ce_signal():
    engine = PriceActionEngine({"volume_filter": False})
    signal = engine._analyze("ABC", bullish_sweep_candles())
    assert signal is not None
    assert signal["direction"] == "CE"
    assert "BULLISH_STRUCTURE" in signal["reasons"]
    assert "BULLISH_CANDLE_CONFIRMATION" in signal["reasons"]


def test_volume_filter_blocks_low_relative_volume_signal():
    engine = PriceActionEngine({"volume_filter": True, "volume_multiplier": 1.2})
    candles = bullish_sweep_candles()
    candles[-1]["volume"] = 100
    assert engine._analyze("ABC", candles) is None


def test_signal_is_not_repeated_for_same_completed_candle():
    engine = PriceActionEngine({"volume_filter": False})
    candles = bullish_sweep_candles()
    first = engine._analyze("ABC", candles)
    second = engine._analyze("ABC", candles)
    assert first is not None
    assert second is None


def test_tick_builder_only_analyzes_after_minute_closes():
    engine = PriceActionEngine()
    now = datetime(2026, 11, 27, 9, 20, tzinfo=IST)
    assert engine.on_tick({"symbol": "ABC", "last_price": 100, "volume": 10}, now) is None
    assert engine.on_tick({"symbol": "ABC", "last_price": 101, "volume": 12}, now + timedelta(seconds=20)) is None
    assert engine.current["ABC"]["high"] == 101
    assert engine.current["ABC"]["close"] == 101
    assert engine.current["ABC"]["volume"] == 2
    assert engine.on_tick({"symbol": "ABC", "last_price": 102, "volume": 15}, now + timedelta(minutes=1)) is None
    assert engine.current["ABC"]["volume_start"] == 12
    assert engine.current["ABC"]["volume"] == 3


def test_seed_history_populates_structure_without_retroactive_entry():
    engine = PriceActionEngine({"volume_filter": False})
    candles = bullish_sweep_candles()
    seeded = engine.seed_history("ABC", [{"date": row["minute"], **row} for row in candles])
    assert seeded == 8
    state = engine.snapshot()[0]
    assert state["structure"] in {"BULLISH_HH_HL", "BEARISH_LH_LL", "RANGE"}
    assert state["signal"] is None
    assert state["reason"] == "Historical baseline loaded; waiting for next completed candle"


def test_seed_history_reports_insufficient_historical_candles():
    engine = PriceActionEngine()
    seeded = engine.seed_history("ABC", [{"date": datetime(2026, 11, 27, 9, 15, tzinfo=IST), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}])
    assert seeded == 1
    assert engine.snapshot()[0]["reason"].startswith("Historical data returned 1 completed")

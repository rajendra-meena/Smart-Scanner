from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import app.core.market_regime_engine as regime_module
from app.core.market_regime_engine import MarketRegimeEngine

IST = ZoneInfo("Asia/Kolkata")


def candles(count=80, trend=1.0, start=None, interval=5):
    start = start or datetime(2026, 11, 24, 9, 15, tzinfo=IST)
    rows = []
    price = 100.0
    for index in range(count):
        next_price = price + trend
        rows.append({
            "date": start + timedelta(minutes=interval * index),
            "open": price,
            "high": next_price + 0.15,
            "low": price - 0.1,
            "close": next_price,
            "volume": 0,
        })
        price = next_price
    return rows


def seed_both(engine, rows_5m=None, rows_15m=None, now=None):
    now = now or datetime(2026, 11, 27, 15, 30, tzinfo=IST)
    for symbol in engine.REQUIRED_INDICES:
        engine.seed_history(symbol, "5minute", rows_5m or candles(), now=now)
        # Use a separate timestamp cadence for 15-minute history.
        source = rows_15m or candles(start=datetime(2026, 11, 24, 9, 15, tzinfo=IST), interval=15)
        engine.seed_history(symbol, "15minute", source, now=now)


def test_market_regime_is_unknown_until_sufficient_history_and_fresh_ticks():
    engine = MarketRegimeEngine()
    seed_both(engine, rows_5m=candles(20), rows_15m=candles(20))
    snapshot = engine.snapshot()
    assert snapshot["regime"] == "UNKNOWN"
    assert "insufficient" in snapshot["reason"].lower() or "fresh" in snapshot["reason"].lower()


def test_aligned_bullish_indices_allow_only_ce_after_fresh_ticks(monkeypatch):
    engine = MarketRegimeEngine()
    seed_both(engine)
    monkeypatch.setattr(regime_module, "monotonic", lambda: 100.0)
    now = datetime(2026, 11, 27, 10, 0, tzinfo=IST)
    for symbol in engine.REQUIRED_INDICES:
        engine.on_tick({"symbol": symbol, "last_price": 500.0, "instrument_type": "INDEX"}, now)

    allowed, snapshot = engine.allows("CE")
    assert snapshot["regime"] == "BULLISH"
    assert allowed is True
    allowed, _ = engine.allows("PE")
    assert allowed is False


def test_mixed_major_index_directions_block_all_entries(monkeypatch):
    engine = MarketRegimeEngine()
    bullish = candles()
    bearish = candles(trend=-1.0)
    now = datetime(2026, 11, 27, 15, 30, tzinfo=IST)
    for symbol in engine.REQUIRED_INDICES:
        engine.seed_history(symbol, "5minute", bullish if symbol == "NIFTY 50" else bearish, now=now)
        engine.seed_history(symbol, "15minute", bullish if symbol == "NIFTY 50" else bearish, now=now)
    monkeypatch.setattr(regime_module, "monotonic", lambda: 200.0)
    for symbol in engine.REQUIRED_INDICES:
        engine.on_tick({"symbol": symbol, "last_price": 500.0}, now)

    allowed, snapshot = engine.allows("CE")
    assert snapshot["regime"] == "MIXED"
    assert allowed is False


def test_stale_index_feed_fails_closed(monkeypatch):
    engine = MarketRegimeEngine()
    seed_both(engine)
    monkeypatch.setattr(regime_module, "monotonic", lambda: 100.0)
    now = datetime(2026, 11, 27, 10, 0, tzinfo=IST)
    engine.on_tick({"symbol": "NIFTY 50", "last_price": 500.0}, now)
    engine.on_tick({"symbol": "NIFTY BANK", "last_price": 500.0}, now)
    monkeypatch.setattr(regime_module, "monotonic", lambda: 111.0)

    allowed, snapshot = engine.allows("CE")
    assert snapshot["regime"] == "UNKNOWN"
    assert allowed is False


def test_seed_history_ignores_incomplete_current_candle():
    engine = MarketRegimeEngine()
    now = datetime(2026, 11, 27, 10, 2, tzinfo=IST)
    rows = candles(9, start=datetime(2026, 11, 27, 9, 15, tzinfo=IST))
    # This candle begins at 10:00 and is still forming at 10:02.
    rows.append({
        "date": datetime(2026, 11, 27, 10, 0, tzinfo=IST),
        "open": 200, "high": 202, "low": 199, "close": 201, "volume": 0,
    })

    seeded = engine.seed_history("NIFTY 50", "5minute", rows, now=now)
    assert seeded == 9



def test_sector_index_is_required_for_a_stock_entry(monkeypatch):
    engine = MarketRegimeEngine()
    required = engine.indices_for_symbols(["INFY"])
    assert required == ["NIFTY 50", "NIFTY BANK", "NIFTY IT"]
    engine.register_indices(required)
    seed_both(engine)
    now = datetime(2026, 11, 27, 15, 30, tzinfo=IST)
    sector_history = candles(
        start=datetime(2026, 11, 24, 9, 15, tzinfo=IST),
        interval=5,
    )
    engine.seed_history("NIFTY IT", "5minute", sector_history, now=now)
    engine.seed_history(
        "NIFTY IT", "15minute",
        candles(start=datetime(2026, 11, 24, 9, 15, tzinfo=IST), interval=15),
        now=now,
    )
    monkeypatch.setattr(regime_module, "monotonic", lambda: 300.0)
    for symbol in required:
        engine.on_tick({"symbol": symbol, "last_price": 500.0}, now)

    allowed, snapshot = engine.allows("CE", "INFY")
    assert allowed is True
    assert snapshot["required_indices"] == required
    assert snapshot["indices"]["NIFTY IT"]["feed_fresh"] is True


def test_sector_index_opposite_direction_blocks_stock_entry(monkeypatch):
    engine = MarketRegimeEngine()
    required = engine.indices_for_symbols(["INFY"])
    engine.register_indices(required)
    now = datetime(2026, 11, 27, 15, 30, tzinfo=IST)
    bullish = candles()
    bearish = candles(trend=-1.0)
    for symbol in ("NIFTY 50", "NIFTY BANK"):
        engine.seed_history(symbol, "5minute", bullish, now=now)
        engine.seed_history(
            symbol, "15minute",
            candles(start=datetime(2026, 11, 24, 9, 15, tzinfo=IST), interval=15),
            now=now,
        )
    engine.seed_history("NIFTY IT", "5minute", bearish, now=now)
    engine.seed_history(
        "NIFTY IT", "15minute",
        candles(trend=-1.0, start=datetime(2026, 11, 24, 9, 15, tzinfo=IST), interval=15),
        now=now,
    )
    monkeypatch.setattr(regime_module, "monotonic", lambda: 400.0)
    for symbol in required:
        engine.on_tick({"symbol": symbol, "last_price": 500.0}, now)

    allowed, snapshot = engine.allows("CE", "INFY")
    assert snapshot["regime"] == "MIXED"
    assert allowed is False


def test_unmapped_underlying_fails_closed():
    engine = MarketRegimeEngine()
    allowed, snapshot = engine.allows("CE", "UNMAPPED_STOCK")
    assert allowed is False
    assert snapshot["regime"] == "UNKNOWN"
    assert "No approved sector-index mapping" in snapshot["reason"]


def test_sideways_required_sector_index_blocks_entry(monkeypatch):
    engine = MarketRegimeEngine()
    required = engine.indices_for_symbols(["INFY"])
    engine.register_indices(required)
    now = datetime(2026, 11, 27, 15, 30, tzinfo=IST)
    bullish = candles()
    for symbol in ("NIFTY 50", "NIFTY BANK"):
        engine.seed_history(symbol, "5minute", bullish, now=now)
        engine.seed_history(
            symbol, "15minute",
            candles(start=datetime(2026, 11, 24, 9, 15, tzinfo=IST), interval=15),
            now=now,
        )
    # A flat range produces ADX below the sideways threshold.
    flat = []
    start = datetime(2026, 11, 24, 9, 15, tzinfo=IST)
    for index in range(80):
        stamp = start + timedelta(minutes=5 * index)
        flat.append({
            "date": stamp, "open": 100.0, "high": 100.1,
            "low": 99.9, "close": 100.0, "volume": 0,
        })
    engine.seed_history("NIFTY IT", "5minute", flat, now=now)
    engine.seed_history(
        "NIFTY IT", "15minute",
        [dict(row, date=start + timedelta(minutes=15 * index))
         for index, row in enumerate(flat)],
        now=now,
    )
    monkeypatch.setattr(regime_module, "monotonic", lambda: 500.0)
    for symbol in required:
        engine.on_tick({"symbol": symbol, "last_price": 500.0}, now)

    allowed, snapshot = engine.allows("CE", "INFY")
    assert snapshot["regime"] == "SIDEWAYS"
    assert allowed is False

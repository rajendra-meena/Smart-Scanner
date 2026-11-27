from app.core import market_data as market_data_module
from app.core.market_data import MarketDataEngine


class DummyService:
    def on_market_tick(self, rows):
        return None

    def on_feed_connected(self):
        return None


class FakeWebSocket:
    MODE_QUOTE = 1
    MODE_FULL = 2

    def __init__(self):
        self.subscribed = []
        self.modes = []

    def subscribe(self, tokens):
        self.subscribed.extend(tokens)

    def set_mode(self, mode, tokens):
        self.modes.append((mode, list(tokens)))


def make_index_engine():
    engine = MarketDataEngine()
    token = 256265
    row = {
        "instrument_token": token,
        "symbol": "NIFTY 50",
        "name": "NIFTY 50",
        "exchange": "NSE",
        "segment": "INDICES",
        "instrument_type": "INDEX",
    }
    engine._instruments = {token: row}
    engine._subscribed = [token]
    engine._index_tick_diagnostics = {
        "NIFTY 50": {
            "instrument_token": token,
            "subscription_requested": False,
            "last_tick_received_at": None,
            "last_tick_monotonic": None,
            "raw_ticks": 0,
            "valid_ticks": 0,
            "invalid_ticks": 0,
        }
    }
    return engine, token


def test_index_feed_diagnostics_report_missing_instrument_and_subscription():
    engine, _ = make_index_engine()

    diagnostics = engine.index_feed_diagnostics(["NIFTY 50", "NIFTY CONSUMER DURABLES"])

    assert diagnostics["NIFTY 50"]["instrument_found"] is True
    assert diagnostics["NIFTY 50"]["subscribed_locally"] is True
    assert diagnostics["NIFTY 50"]["subscription_requested"] is False
    assert diagnostics["NIFTY CONSUMER DURABLES"]["instrument_found"] is False
    assert diagnostics["NIFTY CONSUMER DURABLES"]["instrument_token"] is None
    assert diagnostics["NIFTY CONSUMER DURABLES"]["subscribed_locally"] is False


def test_on_connect_records_local_index_subscription_request(monkeypatch):
    engine, token = make_index_engine()
    ws = FakeWebSocket()
    monkeypatch.setattr(market_data_module, "price_action_service", DummyService())
    monkeypatch.setattr(engine, "_publish", lambda message: None)

    engine._on_connect(ws, {})

    diagnostics = engine.index_feed_diagnostics(["NIFTY 50"])["NIFTY 50"]
    assert token in ws.subscribed
    assert diagnostics["subscription_requested"] is True
    assert diagnostics["subscribed_locally"] is True


def test_index_tick_diagnostics_count_raw_valid_and_invalid_ticks(monkeypatch):
    engine, token = make_index_engine()
    monkeypatch.setattr(market_data_module, "opening_breakout_service", DummyService())
    monkeypatch.setattr(market_data_module, "price_action_service", DummyService())
    monkeypatch.setattr(engine, "_publish", lambda message: None)

    engine._on_ticks(None, [{
        "instrument_token": token,
        "last_price": 24000,
        "ohlc": {"close": 23900, "open": 23950, "high": 24010, "low": 23940},
    }])
    engine._on_ticks(None, [{
        "instrument_token": token,
        "last_price": 0,
        "ohlc": {"close": 23900},
    }])

    diagnostics = engine.index_feed_diagnostics(["NIFTY 50"])["NIFTY 50"]
    assert diagnostics["raw_ticks"] == 2
    assert diagnostics["valid_ticks"] == 1
    assert diagnostics["invalid_ticks"] == 1
    assert diagnostics["last_tick_received_at"] is not None
    assert diagnostics["tick_age_seconds"] is not None



def test_price_action_routes_index_ticks_even_when_strategy_disabled():
    from threading import RLock

    from app.core.price_action_service import PriceActionService

    class RegimeRecorder:
        def __init__(self):
            self.rows = []

        def on_tick(self, row, now):
            self.rows.append(row)

    service = PriceActionService.__new__(PriceActionService)
    service.lock = RLock()
    service.config = {"enabled": False}
    service.running = False
    service.positions = {}
    service.market_regime = RegimeRecorder()

    # Segment is authoritative even if an unexpected instrument_type slips through.
    index_row = {
        "symbol": "NIFTY 50",
        "segment": "INDICES",
        "instrument_type": "EQ",
        "last_price": 24000,
    }
    service.on_market_tick([index_row])

    assert service.market_regime.rows == [index_row]


def test_first_fresh_tick_triggers_opening_breakout_recovery_before_tick_dispatch(
    monkeypatch,
):
    engine, token = make_index_engine()
    calls = []

    class OpeningBreakoutRecorder:
        def on_feed_connected(self):
            calls.append("restore")

        def on_market_tick(self, rows):
            calls.append("ticks")

    class PriceActionRecorder:
        def on_market_tick(self, rows):
            return None

    monkeypatch.setattr(
        market_data_module,
        "opening_breakout_service",
        OpeningBreakoutRecorder(),
    )
    monkeypatch.setattr(
        market_data_module,
        "price_action_service",
        PriceActionRecorder(),
    )
    monkeypatch.setattr(engine, "_publish", lambda message: None)

    engine._on_ticks(None, [{
        "instrument_token": token,
        "last_price": 24000,
        "ohlc": {
            "close": 23900,
            "open": 23950,
            "high": 24010,
            "low": 23940,
        },
    }])

    assert calls == ["restore", "ticks"]

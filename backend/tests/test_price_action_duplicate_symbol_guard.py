from datetime import datetime
from threading import RLock
from zoneinfo import ZoneInfo

from app.core.price_action_service import PriceActionService

IST = ZoneInfo("Asia/Kolkata")


def make_service():
    service = PriceActionService.__new__(PriceActionService)
    service.lock = RLock()
    service.positions = {}
    service.pending = {}
    service.events = []
    # These tests isolate duplicate-symbol behavior; market-regime gating has
    # dedicated tests in test_market_regime_engine.py.
    service.market_regime = type("MarketRegime", (), {
        "allows": lambda self, direction: (True, {"regime": "BULLISH"})
    })()
    service.config = {"max_positions": 3, "max_trades_per_day": 5, "max_daily_loss": 1500}
    return service


def test_new_signal_is_skipped_when_same_underlying_has_open_position():
    service = make_service()
    service.positions["open-1"] = {
        "id": "open-1", "symbol": "ASHOKLEY", "status": "OPEN", "token": 123,
    }

    service._queue_option(
        {"symbol": "ASHOKLEY", "direction": "PE", "reasons": []},
        datetime(2026, 11, 27, 12, 40, tzinfo=IST),
    )

    assert service.events[-1]["event"] == "SIGNAL_SKIPPED_DUPLICATE_SYMBOL"
    assert service.events[-1]["symbol"] == "ASHOKLEY"
    assert service.pending == {}


def test_new_signal_is_skipped_when_same_underlying_entry_is_pending():
    service = make_service()
    service.pending[456] = {
        "symbol": "ASHOKLEY", "direction": "PE", "token": 456,
    }

    service._queue_option(
        {"symbol": "ASHOKLEY", "direction": "CE", "reasons": []},
        datetime(2026, 11, 27, 12, 40, tzinfo=IST),
    )

    assert service.events[-1]["event"] == "SIGNAL_SKIPPED_DUPLICATE_SYMBOL"
    assert service.pending.keys() == {456}


def test_different_underlying_is_not_blocked_by_another_open_position():
    service = make_service()
    service.positions["open-1"] = {
        "id": "open-1", "symbol": "ASHOKLEY", "status": "OPEN", "token": 123,
    }

    # This signal should proceed beyond the duplicate-symbol guard. It is
    # subsequently rejected by the configured max-position limit in this
    # minimal fixture only if the limit is reached.
    service.config["max_positions"] = 3
    service.config["max_trades_per_day"] = 5
    service.positions["open-2"] = {
        "id": "open-2", "symbol": "MAZDOCK", "status": "OPEN", "token": 124,
    }
    # Stub the trade-history and market-data dependencies after passing guard.
    service.trades = lambda trade_date=None: []
    service.market_data = type("MarketData", (), {
        "option_instruments": lambda self, symbol: [],
    })()
    service._queue_option(
        {"symbol": "COFORGE", "direction": "CE", "underlying_price": 100, "reasons": []},
        datetime(2026, 11, 27, 12, 40, tzinfo=IST),
    )

    assert service.events[-1]["event"] == "SIGNAL_SKIPPED"
    assert service.events[-1]["symbol"] == "COFORGE"
    assert service.events[-1]["reason"] == "No current option contract"

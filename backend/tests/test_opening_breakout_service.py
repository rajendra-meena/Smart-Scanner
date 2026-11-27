
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import app.core.opening_breakout.service as service_module
from app.core.opening_breakout.service import OpeningBreakoutService


IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 26, 9, 20, 0, tzinfo=IST)


class FakeMarketData:
    def __init__(self):
        self.fresh = True
        self.equity_rows = []
        self.option_rows = []
        self.fno_symbols = set()
        self.subscriptions = []
        self.subscription_error = None

    def is_feed_fresh(self, seconds):
        return self.fresh

    def snapshot(self):
        return {"status": "connected", "all_equity": self.equity_rows}

    def universe_symbols(self):
        return self.fno_symbols

    def option_instruments(self):
        return self.option_rows

    def subscribe_extra(self, tokens, full=False):
        if self.subscription_error:
            raise self.subscription_error

        self.subscriptions.append((tokens, full))
        return True


class FakeStore:
    def __init__(self):
        self.saved_trades = []
        self.saved_settings = {}

    def load_settings(self):
        return dict(self.saved_settings)

    def save_settings(self, values):
        self.saved_settings.update(values)

    def save_trade(self, trade):
        self.saved_trades.append(trade)

    def trades(self, trade_date=None):
        if trade_date:
            return [
                trade for trade in self.saved_trades
                if trade["trade_date"] == trade_date
            ]
        return list(self.saved_trades)


class FakeTimer:
    instances = []

    def __init__(self, seconds, callback):
        self.seconds = seconds
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def service_env(monkeypatch):
    store = FakeStore()

    monkeypatch.setattr(
        service_module,
        "OpeningBreakoutStore",
        lambda: store,
    )

    market_data = FakeMarketData()
    service = OpeningBreakoutService(market_data)

    service.engine.set_universe(
        [
            {
                "symbol": "ABC",
                "instrument_token": 1,
                "close": 100,
                "instrument_type": "EQ",
            },
            {
                "symbol": "XYZ",
                "instrument_token": 2,
                "close": 200,
                "instrument_type": "EQ",
            },
        ],
        [],
    )

    return service, market_data, store


def prepare_start_environment(service, market_data, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(
        service_module,
        "datetime",
        FixedDateTime,
    )
    monkeypatch.setattr(
        service_module,
        "Timer",
        FakeTimer,
    )

    market_data.equity_rows = [
        {
            "symbol": "ABC",
            "instrument_token": 1,
            "close": 100,
            "open": 101,
            "change_percent": 2.0,
            "instrument_type": "EQ",
        },
        {
            "symbol": "XYZ",
            "instrument_token": 2,
            "close": 200,
            "open": 198,
            "change_percent": -1.5,
            "instrument_type": "EQ",
        },
        {
            "symbol": "NOTFNO",
            "instrument_token": 3,
            "close": 100,
            "open": 102,
            "change_percent": 2.5,
            "instrument_type": "EQ",
        },
    ]

    market_data.fno_symbols = {"ABC", "XYZ"}
    FakeTimer.instances.clear()


def add_pending_entry(service, token=101):
    assert service.engine.reserve_entry("ABC")

    pending = {
        "id": f"PAPER-{token}",
        "symbol": "ABC",
        "option_symbol": "ABC26NOV100CE",
        "option_token": token,
        "direction": "CE",
        "lot_size": 25,
        "mode": "PAPER",
        "status": "WAITING_QUOTE",
    }

    service._pending_contracts[token] = pending
    service._option_token_symbols[token] = pending["option_symbol"]

    return pending


def add_open_position(service, token=202):
    position_id = f"OPEN-{token}"

    assert service.engine.reserve_entry("XYZ")

    position = {
        "id": position_id,
        "symbol": "XYZ",
        "option_symbol": "XYZ26NOV200CE",
        "option_token": token,
        "direction": "CE",
        "lot_size": 25,
        "quantity": 25,
        "mode": "PAPER",
        "status": "OPEN",
        "entry_price": 100.0,
        "stop_price": 85.0,
        "ltp": 100.0,
        "bid": 99.5,
        "ask": 100.0,
    }

    service.positions[position_id] = position
    service._option_token_symbols[token] = position["option_symbol"]
    service._last_option_tick_monotonic[token] = service_module.monotonic()
    service.engine.mark_position_open("XYZ", position_id)

    return position


def option_tick(token, ltp, bid, ask):
    return {
        "instrument_token": token,
        "instrument_type": "CE",
        "last_price": ltp,
        "depth": {
            "buy": [{"price": bid, "quantity": 100}],
            "sell": [{"price": ask, "quantity": 100}],
        },
    }


# ---------------------------------------------------------
# PAPER entry and exit lifecycle
# ---------------------------------------------------------

def test_paper_entry_uses_ask_price_and_sets_stop_loss(service_env):
    service, _, _ = service_env
    pending = add_pending_entry(service, token=101)

    service._handle_option_tick(
        option_tick(101, ltp=100.0, bid=99.8, ask=100.0),
        NOW,
    )

    position = service.positions[pending["id"]]

    assert position["status"] == "OPEN"
    assert position["entry_price"] == 100.0
    assert position["stop_price"] == 85.0
    assert position["quantity"] == 25
    assert position["bid"] == 99.8

    assert 101 not in service._pending_contracts
    assert service.engine.stocks["ABC"].active_position_id == pending["id"]

    assert any(
        event["event"] == "PAPER_ENTRY"
        for event in service.events
    )


def test_invalid_spread_does_not_open_pending_entry(service_env):
    service, _, _ = service_env
    pending = add_pending_entry(service, token=101)

    service._handle_option_tick(
        option_tick(101, ltp=100.0, bid=99.0, ask=102.0),
        NOW,
    )

    assert pending["status"] == "WAITING_QUOTE"
    assert 101 in service._pending_contracts
    assert pending["id"] not in service.positions
    assert service.engine.stocks["ABC"].pending_entry is True


def test_stop_loss_closes_position_and_saves_pnl(service_env):
    service, _, store = service_env
    pending = add_pending_entry(service, token=101)

    service._handle_option_tick(
        option_tick(101, ltp=100.0, bid=99.8, ask=100.0),
        NOW,
    )

    position = service.positions[pending["id"]]

    service._handle_option_tick(
        option_tick(101, ltp=84.5, bid=84.0, ask=84.2),
        NOW,
    )

    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "STOP_LOSS"
    assert position["exit_price"] == 84.0
    assert position["pnl"] == -400.0

    assert len(store.saved_trades) == 1
    assert store.saved_trades[0]["id"] == pending["id"]

    assert service.engine.stocks["ABC"].active_position_id is None

    assert any(
        event["event"] == "PAPER_EXIT"
        and event["reason"] == "STOP_LOSS"
        for event in service.events
    )


def test_first_exit_allows_one_reentry_but_not_third_entry(service_env):
    service, _, _ = service_env
    pending = add_pending_entry(service, token=101)

    service._handle_option_tick(
        option_tick(101, ltp=100.0, bid=99.8, ask=100.0),
        NOW,
    )

    service._handle_option_tick(
        option_tick(101, ltp=84.5, bid=84.0, ask=84.2),
        NOW,
    )

    assert service.engine.can_enter("ABC")

    assert service.engine.reserve_entry("ABC")
    service.engine.mark_position_open("ABC", "SECOND")
    service.engine.mark_position_closed("ABC", "SECOND")

    assert not service.engine.can_enter("ABC")
    assert service.engine.stocks["ABC"].entry_count == 2


# ---------------------------------------------------------
# Square-off, feed failure and manual stop
# ---------------------------------------------------------

def test_square_off_cancels_pending_and_closes_open_position(service_env):
    service, _, store = service_env

    add_pending_entry(service, token=101)
    position = add_open_position(service, token=202)

    service._last_option_ticks[202] = option_tick(
        202,
        ltp=96.0,
        bid=95.5,
        ask=95.8,
    )

    service._square_off_paper([], NOW)

    assert 101 not in service._pending_contracts
    assert 101 not in service._option_token_symbols

    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "TIME_EXIT_09_30"
    assert position["exit_price"] == 95.5

    assert len(store.saved_trades) == 1
    assert service.engine.stocks["ABC"].pending_entry is False
    assert service.engine.stocks["XYZ"].active_position_id is None



def test_feed_failure_cancels_pending_and_records_unpriced_exit(service_env):
    service, market_data, store = service_env

    add_pending_entry(service, token=101)
    position = add_open_position(service, token=202)

    # Simulate cached prices that must not be used after feed failure.
    position["bid"] = 95.0
    position["ltp"] = 96.0
    service._last_option_ticks[202] = option_tick(
        202,
        ltp=96.0,
        bid=95.5,
        ask=95.8,
    )

    service.running = True
    market_data.fresh = False
    exit_timer = FakeTimer(600, service._time_exit)
    service._exit_timer = exit_timer
    watchdog = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = watchdog

    service.on_market_tick([])

    assert service.running is False
    assert exit_timer.cancelled is True
    assert service._exit_timer is None
    assert watchdog.cancelled is True
    assert service._watchdog_timer is None
    assert 101 not in service._pending_contracts

    assert position["status"] == "CLOSED_UNPRICED"
    assert position["exit_reason"] == "FEED_GUARD_EXIT"
    assert position["exit_price"] is None
    assert position.get("pnl") is None

    assert len(store.saved_trades) == 1
    assert store.saved_trades[0]["status"] == "CLOSED_UNPRICED"
    assert store.saved_trades[0]["exit_price"] is None
    assert store.saved_trades[0].get("pnl") is None

    assert service.engine.stocks["XYZ"].active_position_id is None

    assert any(
        event["event"] == "FEED_GUARD_STOP"
        for event in service.events
    )


def test_watchdog_stops_strategy_when_feed_goes_stale_without_market_ticks(
    service_env, monkeypatch
):
    service, market_data, store = service_env
    add_pending_entry(service, token=101)
    position = add_open_position(service, token=202)
    service.running = True
    market_data.fresh = False

    watchdog = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = watchdog

    watchdog.callback()

    assert service.running is False
    assert watchdog.cancelled is True
    assert service._watchdog_timer is None
    assert 101 not in service._pending_contracts
    assert position["status"] == "CLOSED_UNPRICED"
    assert position["exit_price"] is None
    assert position.get("pnl") is None
    assert len(store.saved_trades) == 1
    assert any(event["event"] == "FEED_GUARD_STOP" for event in service.events)


def test_feed_failure_stops_strategy_even_when_trade_persistence_fails(service_env):
    service, market_data, store = service_env
    position = add_open_position(service, token=202)
    service.running = True
    market_data.fresh = False

    exit_timer = FakeTimer(600, service._time_exit)
    watchdog = FakeTimer(1, service._watchdog_tick)
    service._exit_timer = exit_timer
    service._watchdog_timer = watchdog

    def fail_save_trade(trade):
        raise RuntimeError("database unavailable")

    store.save_trade = fail_save_trade

    service._watchdog_tick()

    assert service.running is False
    assert service._exit_timer is None
    assert service._watchdog_timer is None
    assert exit_timer.cancelled is True
    assert watchdog.cancelled is True
    assert position["status"] == "CLOSED_UNPRICED"
    assert position["exit_price"] is None
    assert service.engine.stocks["XYZ"].active_position_id is None
    assert any(
        event["event"] == "PAPER_EXIT_PERSIST_FAILED"
        for event in service.events
    )
    assert any(event["event"] == "FEED_GUARD_STOP" for event in service.events)


def test_watchdog_reschedules_while_feed_is_fresh(service_env, monkeypatch):
    service, market_data, _ = service_env
    monkeypatch.setattr(service_module, "Timer", FakeTimer)
    FakeTimer.instances.clear()
    service.running = True
    market_data.fresh = True

    first = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = first

    first.callback()

    assert service.running is True
    assert service._watchdog_timer is not first
    assert service._watchdog_timer.started is True
    assert service._watchdog_timer.daemon is True


def test_schedule_watchdog_does_not_create_duplicate_timer(service_env, monkeypatch):
    service, _, _ = service_env
    monkeypatch.setattr(service_module, "Timer", FakeTimer)
    FakeTimer.instances.clear()
    service.running = True

    existing = FakeTimer(1, service._watchdog_tick)
    existing.start()
    service._watchdog_timer = existing

    service._schedule_watchdog()

    assert service._watchdog_timer is existing
    assert len(FakeTimer.instances) == 1
    assert existing.started is True
    assert existing.cancelled is False


def test_manual_stop_cancels_pending_and_closes_open_position(service_env):
    service, _, store = service_env

    add_pending_entry(service, token=101)
    position = add_open_position(service, token=202)

    service.running = True
    watchdog = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = watchdog

    result = service.stop()

    assert service.running is False
    assert 101 not in service._pending_contracts

    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "MANUAL_STOP"

    assert len(store.saved_trades) == 1
    assert result["running"] is False
    assert watchdog.cancelled is True
    assert service._watchdog_timer is None

    assert any(
        event["event"] == "STOPPED"
        for event in service.events
    )


# ---------------------------------------------------------
# Start validation and market universe
# ---------------------------------------------------------

def test_start_rejects_live_mode(service_env):
    service, _, _ = service_env

    with pytest.raises(RuntimeError, match="Live order routing"):
        service.start("LIVE", "dummy-token")

    assert service.running is False


def test_start_rejects_stale_market_feed(service_env, monkeypatch):
    service, market_data, _ = service_env

    prepare_start_environment(service, market_data, monkeypatch)
    market_data.fresh = False

    with pytest.raises(RuntimeError, match="fresh ticks unavailable"):
        service.start("PAPER", "dummy-token")

    assert service.running is False
    assert FakeTimer.instances == []


def test_auto_start_starts_paper_once_fresh_ticks_arrive(
    service_env, monkeypatch
):
    service, market_data, _ = service_env
    prepare_start_environment(service, market_data, monkeypatch)
    monkeypatch.setattr(service_module, "Timer", FakeTimer)
    FakeTimer.instances.clear()

    service.on_market_tick([])

    assert service.running is True
    assert service.mode == "PAPER"
    assert service._auto_start_attempted_day == NOW.date().isoformat()
    assert any(event["event"] == "PAPER_STARTED" for event in service.events)


def test_manual_stop_suppresses_auto_restart_for_same_day(
    service_env, monkeypatch
):
    service, market_data, _ = service_env
    prepare_start_environment(service, market_data, monkeypatch)
    monkeypatch.setattr(service_module, "Timer", FakeTimer)
    FakeTimer.instances.clear()

    service.on_market_tick([])
    assert service.running is True
    service.stop()
    assert service.running is False

    service.on_market_tick([])

    assert service.running is False
    assert service._auto_start_suppressed_day == NOW.date().isoformat()


def test_start_loads_only_fno_universe_and_starts_timer(
    service_env, monkeypatch
):
    service, market_data, _ = service_env

    prepare_start_environment(service, market_data, monkeypatch)
    market_data.option_rows = []

    result = service.start("PAPER", "dummy-token")

    assert result["running"] is True
    assert service.mode == "PAPER"

    assert set(service.engine.stocks) == {"ABC", "XYZ"}
    assert "NOTFNO" not in service.engine.stocks

    assert len(FakeTimer.instances) == 2
    assert service._exit_timer is FakeTimer.instances[0]
    assert service._watchdog_timer is FakeTimer.instances[1]
    assert all(timer.started and timer.daemon for timer in FakeTimer.instances)

    service.stop()


# ---------------------------------------------------------
# ATM selection
# ---------------------------------------------------------

def test_atm_selection_uses_nearest_valid_expiry_and_strike(
    service_env, monkeypatch
):
    service, market_data, _ = service_env

    prepare_start_environment(service, market_data, monkeypatch)

    service.engine.stocks["ABC"].last_price = 22450

    today = NOW.date()
    near_expiry = (today + timedelta(days=7)).isoformat()
    later_expiry = (today + timedelta(days=14)).isoformat()
    expired = (today - timedelta(days=1)).isoformat()

    service._instrument_rows = [
        {
            "name": "ABC",
            "instrument_type": "CE",
            "expiry": later_expiry,
            "strike": 22450,
            "instrument_token": 201,
        },
        {
            "name": "ABC",
            "instrument_type": "CE",
            "expiry": near_expiry,
            "strike": 22500,
            "instrument_token": 202,
        },
        {
            "name": "ABC",
            "instrument_type": "CE",
            "expiry": near_expiry,
            "strike": 22400,
            "instrument_token": 203,
        },
        {
            "name": "ABC",
            "instrument_type": "CE",
            "expiry": expired,
            "strike": 22450,
            "instrument_token": 204,
        },
    ]

    selected = service._select_atm("ABC", "CE")

    assert selected["instrument_token"] == 203
    assert selected["expiry"] == near_expiry


# ---------------------------------------------------------
# Subscription failure and reservation release
# ---------------------------------------------------------

def test_subscription_failure_releases_pending_reservation(
    service_env, monkeypatch
):
    service, market_data, _ = service_env

    prepare_start_environment(service, market_data, monkeypatch)

    market_data.subscription_error = RuntimeError(
        "subscription unavailable"
    )

    service.running = True
    service._day = NOW.date().isoformat()

    service._instrument_rows = [
        {
            "name": "ABC",
            "instrument_type": "PE",
            "expiry": (NOW.date() + timedelta(days=7)).isoformat(),
            "strike": 100,
            "instrument_token": 501,
            "lot_size": 25,
            "tradingsymbol": "ABC26NOV100PE",
        }
    ]

    # First tick establishes an upward excursion.
    service.engine.on_tick(
        {
            "symbol": "ABC",
            "open": 100,
            "last_price": 101,
        },
        NOW,
    )

    # Crossing below opening price generates PE signal.
    service.on_market_tick([
        {
            "symbol": "ABC",
            "instrument_type": "EQ",
            "open": 100,
            "last_price": 99,
        }
    ])

    state = service.engine.stocks["ABC"]

    assert state.pending_entry is False
    assert state.entry_count == 0

    assert 501 not in service._pending_contracts
    assert 501 not in service._option_token_symbols
    assert 501 not in service._pending_quote_started

    assert any(
        event["event"] == "SUBSCRIPTION_FAILED"
        for event in service.events
    )


def test_service_reentry_after_first_stop_loss(service_env, monkeypatch):
    service, market_data, store = service_env

    prepare_start_environment(service, market_data, monkeypatch)

    service.running = True
    service._day = NOW.date().isoformat()

    service._instrument_rows = [
        {
            "name": "ABC",
            "instrument_type": "PE",
            "expiry": (NOW.date() + timedelta(days=7)).isoformat(),
            "strike": 100,
            "instrument_token": 501,
            "lot_size": 25,
            "tradingsymbol": "ABC26NOV100PE",
        }
    ]

    equity_above_open = {
        "symbol": "ABC",
        "instrument_type": "EQ",
        "open": 100,
        "last_price": 101,
    }

    equity_below_open = {
        "symbol": "ABC",
        "instrument_type": "EQ",
        "open": 100,
        "last_price": 99,
    }

    # First upward excursion, then cross below opening price.
    service.on_market_tick([equity_above_open])
    service.on_market_tick([equity_below_open])

    assert len(service._pending_contracts) == 1
    assert len(market_data.subscriptions) == 1

    # First PAPER entry.
    service.on_market_tick([
        option_tick(501, ltp=100.0, bid=99.8, ask=100.0)
    ])

    first_position = next(iter(service.positions.values()))

    assert first_position["status"] == "OPEN"
    assert first_position["entry_price"] == 100.0

    # First position exits through Stop Loss.
    service.on_market_tick([
        option_tick(501, ltp=84.5, bid=84.0, ask=84.2)
    ])

    assert first_position["status"] == "CLOSED"
    assert first_position["exit_reason"] == "STOP_LOSS"
    assert service.engine.can_enter("ABC")

    # A fresh excursion and cross are required for re-entry.
    service.on_market_tick([equity_above_open])
    service.on_market_tick([equity_below_open])

    assert len(service._pending_contracts) == 1
    assert len(market_data.subscriptions) == 2

    # Second PAPER entry through the Service workflow.
    service.on_market_tick([
        option_tick(501, ltp=100.0, bid=99.8, ask=100.0)
    ])

    positions = list(service.positions.values())

    assert len(positions) == 2
    assert positions[0]["status"] == "CLOSED"
    assert positions[1]["status"] == "OPEN"
    assert positions[1]["entry_price"] == 100.0

    assert service.engine.stocks["ABC"].entry_count == 2
    assert not service.engine.can_enter("ABC")
    assert len(store.saved_trades) == 1


def test_max_positions_limit_skips_additional_signal(
    service_env, monkeypatch
):
    service, market_data, _ = service_env

    prepare_start_environment(service, market_data, monkeypatch)

    service.running = True
    service._day = NOW.date().isoformat()
    service.config.max_positions = 1

    expiry = (NOW.date() + timedelta(days=7)).isoformat()

    service._instrument_rows = [
        {
            "name": "ABC",
            "instrument_type": "PE",
            "expiry": expiry,
            "strike": 100,
            "instrument_token": 501,
            "lot_size": 25,
            "tradingsymbol": "ABC26NOV100PE",
        },
        {
            "name": "XYZ",
            "instrument_type": "PE",
            "expiry": expiry,
            "strike": 200,
            "instrument_token": 502,
            "lot_size": 25,
            "tradingsymbol": "XYZ26NOV200PE",
        },
    ]

    # Establish upward excursions for both stocks.
    service.on_market_tick([
        {
            "symbol": "ABC",
            "instrument_type": "EQ",
            "open": 100,
            "last_price": 101,
        },
        {
            "symbol": "XYZ",
            "instrument_type": "EQ",
            "open": 200,
            "last_price": 201,
        },
    ])

    # Both stocks generate PE signals in the same tick batch.
    service.on_market_tick([
        {
            "symbol": "ABC",
            "instrument_type": "EQ",
            "open": 100,
            "last_price": 99,
        },
        {
            "symbol": "XYZ",
            "instrument_type": "EQ",
            "open": 200,
            "last_price": 199,
        },
    ])

    # Only the first signal should be accepted.
    assert len(service._pending_contracts) == 1
    assert 501 in service._pending_contracts
    assert 502 not in service._pending_contracts

    assert len(market_data.subscriptions) == 1

    skipped_events = [
        event for event in service.events
        if event["event"] == "SIGNAL_SKIPPED"
    ]

    assert any(
        event["symbol"] == "XYZ"
        and event["reason"] == "Maximum open positions reached"
        for event in skipped_events
    )


def test_timer_callback_cancels_pending_and_closes_open_positions(
    service_env, monkeypatch
):
    service, _, store = service_env

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 26, 9, 30, 0, tzinfo=IST)

    monkeypatch.setattr(service_module, "datetime", FixedDateTime)

    add_pending_entry(service, token=101)
    position = add_open_position(service, token=202)

    service.running = True

    timer = FakeTimer(600, service._time_exit)
    service._exit_timer = timer
    watchdog = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = watchdog

    # Simulate the scheduled 09:30 timer callback.
    timer.callback()

    assert service.running is False
    assert watchdog.cancelled is True
    assert service._watchdog_timer is None
    assert 101 not in service._pending_contracts
    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "TIME_EXIT_09_30"

    assert len(store.saved_trades) == 1
    assert service.engine.stocks["ABC"].pending_entry is False
    assert service.engine.stocks["XYZ"].active_position_id is None

    assert any(
        event["event"] == "STRATEGY_TIME_EXIT"
        for event in service.events
    )

def test_start_rejects_before_091530(service_env, monkeypatch):
    service, market_data, _ = service_env
    FakeTimer.instances.clear()

    class BeforeStartDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 26, 9, 15, 29, tzinfo=IST)

    monkeypatch.setattr(
        service_module,
        "datetime",
        BeforeStartDateTime,
    )

    with pytest.raises(
        RuntimeError,
        match="between 09:15:30 and 09:30",
    ):
        service.start("PAPER", "dummy-token")

    assert service.running is False
    assert FakeTimer.instances == []

def test_start_rejects_at_or_after_0930(service_env, monkeypatch):
    service, market_data, _ = service_env
    FakeTimer.instances.clear()

    class AfterCutoffDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 26, 9, 30, 0, tzinfo=IST)

    monkeypatch.setattr(
        service_module,
        "datetime",
        AfterCutoffDateTime,
    )

    with pytest.raises(
        RuntimeError,
        match="between 09:15:30 and 09:30",
    ):
        service.start("PAPER", "dummy-token")

    assert service.running is False
    assert FakeTimer.instances == []


def test_start_accepts_exact_091530_boundary(service_env, monkeypatch):
    service, market_data, _ = service_env

    class ExactStartDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 26, 9, 15, 30, tzinfo=IST)

    monkeypatch.setattr(
        service_module,
        "datetime",
        ExactStartDateTime,
    )
    monkeypatch.setattr(
        service_module,
        "Timer",
        FakeTimer,
    )

    FakeTimer.instances.clear()

    market_data.equity_rows = [
        {
            "symbol": "ABC",
            "instrument_token": 1,
            "close": 100,
            "open": 101,
            "change_percent": 2.0,
            "instrument_type": "EQ",
        }
    ]
    market_data.fno_symbols = {"ABC"}
    market_data.option_rows = []

    result = service.start("PAPER", "dummy-token")

    assert result["running"] is True
    assert service.mode == "PAPER"
    assert len(FakeTimer.instances) == 2
    assert all(timer.started for timer in FakeTimer.instances)

def test_duplicate_start_does_not_reset_running_strategy(
    service_env, monkeypatch
):
    service, market_data, store = service_env
    prepare_start_environment(service, market_data, monkeypatch)
    pending = add_pending_entry(service, token=101)
    position = add_open_position(service, token=202)
    service.running = True
    service.mode = "PAPER"
    service.started_at = "existing-start"
    service._day = NOW.date().isoformat()
    FakeTimer.instances.clear()

    with pytest.raises(RuntimeError, match="already running"):
        service.start("PAPER", "dummy-token")

    assert service.running is True
    assert service.mode == "PAPER"
    assert service.started_at == "existing-start"
    assert service.positions[position["id"]] is position
    assert service._pending_contracts[101] is pending
    assert FakeTimer.instances == []
    assert store.saved_trades == []


def test_watchdog_stops_when_option_quote_stales_while_equity_feed_is_fresh(
    service_env, monkeypatch
):
    service, market_data, store = service_env
    position = add_open_position(service, token=202)
    service.running = True
    market_data.fresh = True
    service._last_option_tick_monotonic[202] = 10.0
    monkeypatch.setattr(service_module, "monotonic", lambda: 16.0)
    monkeypatch.setattr(service_module, "Timer", FakeTimer)
    FakeTimer.instances.clear()

    watchdog = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = watchdog

    service._watchdog_tick()

    assert service.running is False
    assert position["status"] == "CLOSED_UNPRICED"
    assert position["exit_price"] is None
    assert position.get("pnl") is None
    assert service.engine.stocks["XYZ"].active_position_id is None
    assert watchdog.cancelled is True
    assert service._watchdog_timer is None
    assert len(store.saved_trades) == 1
    assert any(
        event["event"] == "FEED_GUARD_STOP"
        and event["reason"] == "Option quote is stale"
        for event in service.events
    )


def test_watchdog_cancels_pending_entry_when_option_quote_never_arrives(
    service_env, monkeypatch
):
    service, market_data, _ = service_env
    pending = add_pending_entry(service, token=101)
    service.running = True
    market_data.fresh = True
    service._pending_quote_started[101] = 10.0
    monkeypatch.setattr(service_module, "monotonic", lambda: 16.0)
    monkeypatch.setattr(service_module, "Timer", FakeTimer)
    FakeTimer.instances.clear()

    watchdog = FakeTimer(1, service._watchdog_tick)
    service._watchdog_timer = watchdog

    service._watchdog_tick()

    assert service.running is True
    assert pending["status"] == "CANCELLED"
    assert pending["cancel_reason"] == "OPTION_QUOTE_TIMEOUT"
    assert 101 not in service._pending_contracts
    assert service._pending_quote_started == {}
    assert service.engine.stocks["ABC"].pending_entry is False
    assert service.engine.stocks["ABC"].entry_count == 0
    assert service._watchdog_timer is not None
    assert service._watchdog_timer.started is True



def test_square_off_never_uses_stale_cached_option_bid(
    service_env, monkeypatch
):
    service, _, store = service_env
    position = add_open_position(service, token=202)
    service.running = True
    service._last_option_ticks[202] = option_tick(
        202, ltp=100.0, bid=99.5, ask=100.0
    )
    service._last_option_tick_monotonic[202] = 10.0
    monkeypatch.setattr(service_module, "monotonic", lambda: 16.0)

    service._square_off_paper([], NOW)

    assert position["status"] == "CLOSED_UNPRICED"
    assert position["exit_price"] is None
    assert position["exit_reason"] == "TIME_EXIT_09_30_UNPRICED"
    assert position.get("pnl") is None
    assert service.engine.stocks["XYZ"].active_position_id is None
    assert len(store.saved_trades) == 1
    assert store.saved_trades[0]["exit_price"] is None
    unpriced_event = next(
        event for event in service.events
        if event["event"] == "PAPER_EXIT_UNPRICED"
    )
    assert unpriced_event["reason"] == "TIME_EXIT_09_30_UNPRICED"


def test_square_off_accepts_fresh_option_tick_from_cutoff_batch(
    service_env, monkeypatch
):
    service, _, store = service_env
    position = add_open_position(service, token=202)
    service.running = True
    monkeypatch.setattr(service_module, "monotonic", lambda: 20.0)

    fresh_tick = option_tick(202, ltp=96.0, bid=95.5, ask=95.8)
    service._square_off_paper([fresh_tick], NOW)

    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "TIME_EXIT_09_30"
    assert position["exit_price"] == 95.5
    assert len(store.saved_trades) == 1


def test_mode_preference_persists_and_live_remains_not_ready(service_env):
    service, _, store = service_env

    result = service.set_mode("LIVE")

    assert result["mode"] == "LIVE"
    assert result["live_ready"] is False
    assert store.saved_settings["selected_mode"] == "LIVE"
    assert "not implemented and verified" in result["live_block_reason"]

    service.set_mode("PAPER")
    assert store.saved_settings["selected_mode"] == "PAPER"


def test_settings_are_persisted_and_cannot_change_while_running(service_env):
    service, market_data, store = service_env
    result = service.configure({
        "gainers_count": 5,
        "losers_count": 4,
        "stop_loss_points": 18.0,
        "max_positions": 3,
        "max_spread_percent": 0.8,
    })

    assert result["config"]["gainers_count"] == 5
    assert store.saved_settings["gainers_count"] == 5
    assert store.saved_settings["losers_count"] == 4

    service.running = True
    with pytest.raises(RuntimeError, match="Stop the strategy"):
        service.configure({"gainers_count": 2})


def test_open_paper_position_updates_unrealized_pnl_from_bid(service_env):
    service, _, _ = service_env
    pending = add_pending_entry(service, token=303)

    service._handle_option_tick(
        option_tick(303, ltp=100.0, bid=99.8, ask=100.0),
        NOW,
    )
    position = service.positions[pending["id"]]

    service._handle_option_tick(
        option_tick(303, ltp=103.0, bid=102.5, ask=102.8),
        NOW,
    )

    assert position["pnl"] == 62.5
    assert position["ltp"] == 103.0
    assert position["bid"] == 102.5


def test_configured_exit_time_is_used_for_squareoff_timer(service_env, monkeypatch):
    service, market_data, _ = service_env
    prepare_start_environment(service, market_data, monkeypatch)
    service.configure({"exit_time": "09:28:00"})

    result = service.start("PAPER", "token")

    assert result["running"] is True
    assert service._exit_timer.seconds == 8 * 60
    assert service.events[0]["exit_time"] == "09:28:00"

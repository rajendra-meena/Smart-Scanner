from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.opening_breakout.engine import OpeningBreakoutEngine
from app.core.opening_breakout.models import BreakoutConfig

IST = ZoneInfo("Asia/Kolkata")


def engine(config=None):
    result = OpeningBreakoutEngine(config)
    result.set_universe(
        [{"symbol": "ABC", "instrument_token": 1, "close": 100, "instrument_type": "EQ"}],
        [],
    )
    return result


def test_pe_after_high_then_cross_below_open():
    e = engine()
    now = datetime(2026, 11, 26, 9, 15, 31, tzinfo=IST)
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 101}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 100}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 99}, now) == "PE"


def test_ce_after_low_then_cross_above_open():
    e = engine()
    now = datetime(2026, 11, 26, 9, 15, 31, tzinfo=IST)
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 99}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 100}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 101}, now) == "CE"


def test_no_signal_before_wait_or_after_cutoff():
    e = engine()
    assert e.on_tick(
        {"symbol": "ABC", "open": 100, "last_price": 101},
        datetime(2026, 11, 26, 9, 15, 20, tzinfo=IST),
    ) is None
    assert e.on_tick(
        {"symbol": "ABC", "open": 100, "last_price": 101},
        datetime(2026, 11, 26, 9, 30, tzinfo=IST),
    ) is None


def test_default_allows_one_reentry_after_first_position_closes():
    e = engine()
    now = datetime(2026, 11, 26, 9, 15, 31, tzinfo=IST)

    assert e.can_enter("ABC")
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 101}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 99}, now) == "PE"
    assert e.reserve_entry("ABC")
    assert not e.can_enter("ABC")

    e.mark_position_open("ABC", "P1")
    assert not e.can_enter("ABC")
    e.mark_position_closed("ABC", "P1")
    assert e.can_enter("ABC")

    # The second entry requires a new excursion/cross after the first exit.
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 101}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 100}, now) is None
    assert e.on_tick({"symbol": "ABC", "open": 100, "last_price": 99}, now) == "PE"
    assert e.reserve_entry("ABC")
    e.mark_position_open("ABC", "P2")
    e.mark_position_closed("ABC", "P2")
    assert not e.can_enter("ABC")


def test_pending_entry_blocks_duplicate_and_can_be_released():
    e = engine()
    assert e.reserve_entry("ABC")
    assert not e.can_enter("ABC")
    e.release_pending_entry("ABC")
    assert e.can_enter("ABC")
    assert e.stocks["ABC"].entry_count == 0


def test_reentry_can_be_disabled():
    e = engine(BreakoutConfig(allow_reentry=False))
    assert e.reserve_entry("ABC")
    e.mark_position_open("ABC", "P1")
    e.mark_position_closed("ABC", "P1")
    assert not e.can_enter("ABC")


def test_legacy_mark_traded_still_blocks_entries():
    e = engine()
    e.mark_traded("ABC")
    assert not e.can_enter("ABC")

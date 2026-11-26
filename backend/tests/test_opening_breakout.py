from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.opening_breakout.engine import OpeningBreakoutEngine

IST=ZoneInfo("Asia/Kolkata")

def engine():
    e=OpeningBreakoutEngine()
    e.set_universe([{"symbol":"ABC","instrument_token":1,"close":100,"instrument_type":"EQ"}],[])
    return e

def test_pe_after_high_then_cross_below_open():
    e=engine();now=datetime(2026,11,26,9,15,31,tzinfo=IST)
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},now) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":100},now) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":99},now)=="PE"

def test_ce_after_low_then_cross_above_open():
    e=engine();now=datetime(2026,11,26,9,15,31,tzinfo=IST)
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":99},now) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":100},now) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},now)=="CE"

def test_no_signal_before_wait_or_after_cutoff():
    e=engine()
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},datetime(2026,11,26,9,15,20,tzinfo=IST)) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},datetime(2026,11,26,9,30,tzinfo=IST)) is None

def test_signal_fires_only_once_after_mark_traded():
    e=engine();now=datetime(2026,11,26,9,15,31,tzinfo=IST)
    e.on_tick({"symbol":"ABC","open":100,"last_price":101},now)
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":99},now)=="PE"
    e.mark_traded("ABC")
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},now) is None

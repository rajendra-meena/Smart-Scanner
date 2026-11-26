from datetime import datetime
from zoneinfo import ZoneInfo
from app.core.opening_breakout.engine import OpeningBreakoutEngine

IST=ZoneInfo("Asia/Kolkata")
def test_pe_after_high_then_open_cross():
    e=OpeningBreakoutEngine(); e.set_universe([{"symbol":"ABC","instrument_token":1,"close":100}],[])
    now=datetime(2026,11,26,9,15,31,tzinfo=IST)
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},now) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":99},now)=="PE"
def test_ce_after_low_then_open_cross():
    e=OpeningBreakoutEngine(); e.set_universe([{"symbol":"ABC","instrument_token":1,"close":100}],[])
    now=datetime(2026,11,26,9,15,31,tzinfo=IST)
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":99},now) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},now)=="CE"
def test_window_boundaries():
    e=OpeningBreakoutEngine(); e.set_universe([{"symbol":"ABC","instrument_token":1,"close":100}],[])
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},datetime(2026,11,26,9,15,20,tzinfo=IST)) is None
    assert e.on_tick({"symbol":"ABC","open":100,"last_price":101},datetime(2026,11,26,9,30,tzinfo=IST)) is None

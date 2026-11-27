from app.core.breakfast_engine import BreakfastEngine


def rows():
    return [
        {"symbol": "TCS", "last_price": 110, "close": 100, "change_percent": 10},
        {"symbol": "INFY", "last_price": 109, "close": 100, "change_percent": 9},
        {"symbol": "WIPRO", "last_price": 108, "close": 100, "change_percent": 8},
        {"symbol": "HCLTECH", "last_price": 107, "close": 100, "change_percent": 7},
        {"symbol": "SUNPHARMA", "last_price": 95, "close": 100, "change_percent": -5},
        {"symbol": "CIPLA", "last_price": 96, "close": 100, "change_percent": -4},
        {"symbol": "DRREDDY", "last_price": 97, "close": 100, "change_percent": -3},
    ]


def candles():
    return {symbol: {"open": 100, "high": 101, "low": 99, "close": 100}
            for symbol in ("TCS", "INFY", "WIPRO", "HCLTECH", "SUNPHARMA", "CIPLA", "DRREDDY")}


def test_positive_nifty_selects_strong_sector_and_top_stocks():
    result = BreakfastEngine().select(rows(), 0.5, candles(), {"TCS", "INFY", "WIPRO", "HCLTECH", "SUNPHARMA", "CIPLA", "DRREDDY"})
    assert result["ready"] is True
    assert result["direction"] == "LONG"
    assert result["sector"] == "IT"
    assert [x["symbol"] for x in result["candidates"]] == ["TCS", "INFY", "WIPRO"]


def test_negative_nifty_selects_weak_sector_and_bottom_stocks():
    result = BreakfastEngine().select(rows(), -0.5, candles(), {"TCS", "INFY", "WIPRO", "HCLTECH", "SUNPHARMA", "CIPLA", "DRREDDY"})
    assert result["ready"] is True
    assert result["direction"] == "SHORT"
    assert result["sector"] == "PHARMA"
    assert [x["symbol"] for x in result["candidates"]] == ["SUNPHARMA", "CIPLA", "DRREDDY"]


def test_flat_nifty_blocks_directional_selection():
    result = BreakfastEngine().select(rows(), 0, candles(), {"TCS", "INFY", "WIPRO"})
    assert result["ready"] is False


def test_first_five_minute_candle_five_percent_move_is_excluded():
    sample = candles()
    sample["TCS"] = {"open": 100, "high": 106, "low": 100, "close": 106}
    result = BreakfastEngine().select(rows(), 0.5, sample, {"TCS", "INFY", "WIPRO", "HCLTECH"})
    assert "TCS" not in [x["symbol"] for x in result["candidates"]]

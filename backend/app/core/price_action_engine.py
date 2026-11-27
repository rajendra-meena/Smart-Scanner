"""Non-repainting, one-minute price-action signal engine for F&O underlyings."""
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class PriceActionEngine:
    def __init__(self, config=None):
        self.config = config or {}
        self.candles = {}
        self.current = {}
        self.last_signal_candle = {}
        self.state = {}
        self.last_cumulative_volume = {}

    def reset(self):
        self.candles.clear()
        self.current.clear()
        self.last_signal_candle.clear()
        self.state.clear()
        self.last_cumulative_volume.clear()

    def _close_minute(self, symbol, candle):
        history = self.candles.setdefault(symbol, deque(maxlen=120))
        history.append(candle)
        self.current.pop(symbol, None)
        return self._analyze(symbol, list(history))


    def seed_history(self, symbol, candles):
        """Seed completed Zerodha candles without generating a retroactive entry."""
        normalized = []
        for row in candles:
            stamp = row.get("date") or row.get("minute")
            if stamp is None:
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=IST)
            else:
                stamp = stamp.astimezone(IST)
            normalized.append({
                "minute": stamp.replace(second=0, microsecond=0),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "close": float(row.get("close") or 0),
                "volume": int(row.get("volume") or 0),
            })
        normalized = [c for c in normalized if c["close"] > 0 and c["high"] > 0 and c["low"] > 0]
        normalized.sort(key=lambda c: c["minute"])
        history = self.candles.setdefault(str(symbol).upper(), deque(maxlen=120))
        history.clear()
        history.extend(normalized[-120:])
        if len(history) >= 8:
            # Calculate the current market structure, but never execute an old signal.
            self._analyze(str(symbol).upper(), list(history))
            state = self.state[str(symbol).upper()]
            state["signal"] = None
            state["reason"] = "Historical baseline loaded; waiting for next completed candle"
        else:
            self.state[str(symbol).upper()] = {
                "structure": "BUILDING", "zone": None, "signal": None,
                "reason": f"Historical data returned {len(history)} completed candles; 8 required",
            }
        return len(history)

    def on_tick(self, row, now):
        symbol = str(row.get("symbol") or "").upper()
        price = float(row.get("last_price") or 0)
        if not symbol or price <= 0:
            return None
        stamp = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        minute = stamp.replace(second=0, microsecond=0)
        volume = int(row.get("volume") or 0)
        current = self.current.get(symbol)
        if current is None:
            baseline = self.last_cumulative_volume.get(symbol, volume)
            self.current[symbol] = {"minute": minute, "open": price, "high": price, "low": price, "close": price, "volume_start": baseline, "volume": max(0, volume - baseline)}
            self.last_cumulative_volume[symbol] = volume
            return None
        if current["minute"] != minute:
            signal = self._close_minute(symbol, current)
            baseline = self.last_cumulative_volume.get(symbol, volume)
            self.current[symbol] = {"minute": minute, "open": price, "high": price, "low": price, "close": price, "volume_start": baseline, "volume": max(0, volume - baseline)}
            self.last_cumulative_volume[symbol] = volume
            return signal
        current["high"] = max(current["high"], price)
        current["low"] = min(current["low"], price)
        current["close"] = price
        current["volume"] = max(current["volume"], max(0, volume - current["volume_start"]))
        self.last_cumulative_volume[symbol] = max(self.last_cumulative_volume.get(symbol, volume), volume)
        return None

    def _analyze(self, symbol, candles):
        if len(candles) < 8:
            self.state[symbol] = {"structure": "BUILDING", "zone": None, "signal": None, "reason": "Need at least 8 completed one-minute candles"}
            return None
        last = candles[-1]
        prior = candles[-2]
        sample = candles[-8:-1]
        highs = [c["high"] for c in sample]
        lows = [c["low"] for c in sample]
        swing_high, swing_low = max(highs), min(lows)
        recent_highs = [c["high"] for c in candles[-6:-3]]
        recent_lows = [c["low"] for c in candles[-6:-3]]
        newer_highs = [c["high"] for c in candles[-3:-1]]
        newer_lows = [c["low"] for c in candles[-3:-1]]
        bullish = max(newer_highs) > max(recent_highs) and min(newer_lows) > min(recent_lows)
        bearish = min(newer_lows) < min(recent_lows) and max(newer_highs) < max(recent_highs)
        structure = "BULLISH_HH_HL" if bullish else "BEARISH_LH_LL" if bearish else "RANGE"
        body = abs(last["close"] - last["open"])
        candle_range = max(last["high"] - last["low"], 0.01)
        lower_wick = min(last["open"], last["close"]) - last["low"]
        upper_wick = last["high"] - max(last["open"], last["close"])
        bullish_rejection = lower_wick >= body * 1.5 and last["close"] > last["open"] and last["close"] >= last["low"] + candle_range * 0.65
        bearish_rejection = upper_wick >= body * 1.5 and last["close"] < last["open"] and last["close"] <= last["low"] + candle_range * 0.35
        bullish_engulf = last["close"] > last["open"] and prior["close"] < prior["open"] and last["close"] >= prior["open"] and last["open"] <= prior["close"]
        bearish_engulf = last["close"] < last["open"] and prior["close"] > prior["open"] and last["close"] <= prior["open"] and last["open"] >= prior["close"]
        demand_touch = last["low"] <= swing_low * 1.003
        supply_touch = last["high"] >= swing_high * 0.997
        sweep_down = last["low"] < swing_low and last["close"] > swing_low
        sweep_up = last["high"] > swing_high and last["close"] < swing_high
        breakout_up = last["close"] > swing_high and prior["close"] <= swing_high
        breakout_down = last["close"] < swing_low and prior["close"] >= swing_low
        volumes = [c["volume"] for c in candles[-6:-1]]
        volume_ok = not self.config.get("volume_filter", False) or (bool(volumes) and last["volume"] > (sum(volumes) / len(volumes)) * float(self.config.get("volume_multiplier", 1.2)))
        signal = None
        reasons = []
        if volume_ok and structure == "BULLISH_HH_HL" and (demand_touch or sweep_down or breakout_up) and (bullish_rejection or bullish_engulf or breakout_up):
            signal = "CE"
            reasons = ["BULLISH_STRUCTURE", "DEMAND_OR_LIQUIDITY_SWEEP" if demand_touch or sweep_down else "RESISTANCE_BREAKOUT", "BULLISH_CANDLE_CONFIRMATION"]
        elif volume_ok and structure == "BEARISH_LH_LL" and (supply_touch or sweep_up or breakout_down) and (bearish_rejection or bearish_engulf or breakout_down):
            signal = "PE"
            reasons = ["BEARISH_STRUCTURE", "SUPPLY_OR_LIQUIDITY_SWEEP" if supply_touch or sweep_up else "SUPPORT_BREAKDOWN", "BEARISH_CANDLE_CONFIRMATION"]
        candle_id = last["minute"]
        if signal and self.last_signal_candle.get(symbol) == candle_id:
            signal = None
        if signal:
            self.last_signal_candle[symbol] = candle_id
        self.state[symbol] = {
            "structure": structure,
            "swing_high": round(swing_high, 2),
            "swing_low": round(swing_low, 2),
            "demand_zone": {"low": round(swing_low, 2), "high": round(swing_low * 1.003, 2)},
            "supply_zone": {"low": round(swing_high * 0.997, 2), "high": round(swing_high, 2)},
            "signal": signal,
            "reason": ", ".join(reasons) if reasons else "No complete confluence",
            "candle_time": candle_id.isoformat(),
            "volume_confirmed": volume_ok,
        }
        return {"symbol": symbol, "direction": signal, "underlying_price": last["close"], "stop_reference": last["low"] if signal == "CE" else last["high"], "reasons": reasons, "candle_time": candle_id.isoformat()} if signal else None

    def snapshot(self):
        return [{"symbol": symbol, **state} for symbol, state in sorted(self.state.items())]

"""Fail-closed NIFTY/BANKNIFTY market-regime filter for Price Action Intraday."""
from collections import deque
from datetime import datetime
from threading import RLock
from time import monotonic
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class MarketRegimeEngine:
    REQUIRED_INDICES = ("NIFTY 50", "NIFTY BANK")
    MIN_CANDLES = 50
    FRESH_SECONDS = 10.0

    def __init__(self):
        self.lock = RLock()
        self.candles = {
            symbol: {"5minute": deque(maxlen=500), "15minute": deque(maxlen=300)}
            for symbol in self.REQUIRED_INDICES
        }
        self.current = {symbol: {} for symbol in self.REQUIRED_INDICES}
        self.last_tick = {}
        self.states = {symbol: {"regime": "UNKNOWN", "reason": "Historical baseline not loaded"} for symbol in self.REQUIRED_INDICES}

    def reset(self):
        with self.lock:
            for symbol in self.REQUIRED_INDICES:
                for interval in ("5minute", "15minute"):
                    self.candles[symbol][interval].clear()
                self.current[symbol].clear()
            self.last_tick.clear()
            self.states = {symbol: {"regime": "UNKNOWN", "reason": "Historical baseline not loaded"} for symbol in self.REQUIRED_INDICES}

    @staticmethod
    def _normalize(rows):
        result = []
        for row in rows or []:
            stamp = row.get("date") or row.get("minute")
            if stamp is None:
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=IST)
            else:
                stamp = stamp.astimezone(IST)
            values = {key: float(row.get(key) or 0) for key in ("open", "high", "low", "close")}
            if values["close"] > 0 and values["high"] > 0 and values["low"] > 0:
                result.append({"date": stamp, **values, "volume": int(row.get("volume") or 0)})
        result.sort(key=lambda item: item["date"])
        return result

    def seed_history(self, symbol, interval, rows):
        symbol = str(symbol).strip().upper()
        if symbol not in self.candles or interval not in ("5minute", "15minute"):
            return 0
        normalized = self._normalize(rows)
        with self.lock:
            target = self.candles[symbol][interval]
            target.clear()
            target.extend(normalized)
            self._refresh_state(symbol)
        return len(normalized)

    @staticmethod
    def _ema(values, period):
        if len(values) < period:
            return None
        alpha = 2.0 / (period + 1)
        value = sum(values[:period]) / period
        for item in values[period:]:
            value = alpha * item + (1 - alpha) * value
        return value

    @staticmethod
    def _adx(candles, period=14):
        if len(candles) < period * 2 + 1:
            return None
        tr_values, plus_dm, minus_dm = [], [], []
        for previous, current in zip(candles, candles[1:]):
            up = current["high"] - previous["high"]
            down = previous["low"] - current["low"]
            plus_dm.append(max(up, 0.0) if up > down else 0.0)
            minus_dm.append(max(down, 0.0) if down > up else 0.0)
            tr_values.append(max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            ))
        dx = []
        for index in range(period - 1, len(tr_values)):
            tr = sum(tr_values[index-period+1:index+1])
            if tr <= 0:
                dx.append(0.0)
                continue
            pdi = 100 * sum(plus_dm[index-period+1:index+1]) / tr
            mdi = 100 * sum(minus_dm[index-period+1:index+1]) / tr
            denominator = pdi + mdi
            dx.append(100 * abs(pdi - mdi) / denominator if denominator else 0.0)
        return sum(dx[-period:]) / min(period, len(dx)) if dx else None

    def _timeframe_state(self, candles):
        rows = list(candles)
        closes = [row["close"] for row in rows]
        if len(rows) < self.MIN_CANDLES:
            return {"direction": "UNKNOWN", "reason": f"Need {self.MIN_CANDLES} completed candles; received {len(rows)}"}
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        adx = self._adx(rows)
        last = closes[-1]
        if ema20 is None or ema50 is None or adx is None:
            return {"direction": "UNKNOWN", "reason": "Indicator history is incomplete"}
        if adx < 18:
            return {"direction": "SIDEWAYS", "reason": "ADX below 18"}
        if last > ema20 > ema50:
            return {"direction": "BULLISH", "reason": "Close above EMA20 and EMA20 above EMA50"}
        if last < ema20 < ema50:
            return {"direction": "BEARISH", "reason": "Close below EMA20 and EMA20 below EMA50"}
        return {"direction": "MIXED", "reason": "Price and EMA alignment disagree"}

    def _refresh_state(self, symbol):
        five = self._timeframe_state(self.candles[symbol]["5minute"])
        fifteen = self._timeframe_state(self.candles[symbol]["15minute"])
        regime = "UNKNOWN"
        reason = "Both 5m and 15m index trends must be available and aligned"
        if five["direction"] == "SIDEWAYS" or fifteen["direction"] == "SIDEWAYS":
            regime, reason = "SIDEWAYS", "At least one index timeframe is sideways"
        elif five["direction"] == fifteen["direction"] and five["direction"] in ("BULLISH", "BEARISH"):
            regime, reason = five["direction"], "5m and 15m trends are aligned"
        elif "UNKNOWN" in (five["direction"], fifteen["direction"]):
            reason = "Insufficient historical candles for one or more timeframes"
        self.states[symbol] = {
            "regime": regime, "reason": reason,
            "5m": five, "15m": fifteen,
            "last_close_5m": list(self.candles[symbol]["5minute"])[-1]["close"] if self.candles[symbol]["5minute"] else None,
            "last_close_15m": list(self.candles[symbol]["15minute"])[-1]["close"] if self.candles[symbol]["15minute"] else None,
        }

    def on_tick(self, row, now):
        symbol = str(row.get("symbol") or "").strip().upper()
        price = float(row.get("last_price") or 0)
        if symbol not in self.candles or price <= 0:
            return
        stamp = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        minute = stamp.replace(second=0, microsecond=0)
        with self.lock:
            self.last_tick[symbol] = monotonic()
            for interval, width in (("5minute", 5), ("15minute", 15)):
                bucket_minute = (minute.minute // width) * width
                bucket = minute.replace(minute=bucket_minute)
                current = self.current[symbol].get(interval)
                if current is None:
                    self.current[symbol][interval] = {"date": bucket, "open": price, "high": price, "low": price, "close": price, "volume": 0}
                elif current["date"] != bucket:
                    self.candles[symbol][interval].append(current)
                    self.current[symbol][interval] = {"date": bucket, "open": price, "high": price, "low": price, "close": price, "volume": 0}
                    self._refresh_state(symbol)
                else:
                    current["high"] = max(current["high"], price)
                    current["low"] = min(current["low"], price)
                    current["close"] = price

    def snapshot(self):
        with self.lock:
            now = monotonic()
            indices = {}
            for symbol in self.REQUIRED_INDICES:
                state = dict(self.states[symbol])
                age = now - self.last_tick[symbol] if symbol in self.last_tick else None
                fresh = age is not None and 0 <= age <= self.FRESH_SECONDS
                indices[symbol] = {**state, "feed_fresh": fresh, "tick_age_seconds": round(age, 2) if age is not None else None}
            regimes = [indices[symbol]["regime"] for symbol in self.REQUIRED_INDICES]
            if not all(indices[symbol]["feed_fresh"] for symbol in self.REQUIRED_INDICES):
                overall, reason = "UNKNOWN", "NIFTY and BANKNIFTY live ticks must both be fresh"
            elif regimes == ["BULLISH", "BULLISH"]:
                overall, reason = "BULLISH", "NIFTY and BANKNIFTY both confirm bullish direction"
            elif regimes == ["BEARISH", "BEARISH"]:
                overall, reason = "BEARISH", "NIFTY and BANKNIFTY both confirm bearish direction"
            elif "SIDEWAYS" in regimes:
                overall, reason = "SIDEWAYS", "At least one major index is sideways"
            elif "UNKNOWN" in regimes:
                overall, reason = "UNKNOWN", "One or more major index regimes are unavailable"
            else:
                overall, reason = "MIXED", "NIFTY and BANKNIFTY directions do not agree"
            return {"regime": overall, "reason": reason, "indices": indices}

    def allows(self, direction):
        snapshot = self.snapshot()
        required = "BULLISH" if str(direction).upper() == "CE" else "BEARISH"
        return snapshot["regime"] == required, snapshot

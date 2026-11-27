"""Fail-closed multi-index market-regime filter for Price Action Intraday."""
from collections import deque
from datetime import timedelta
from threading import RLock
from time import monotonic
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class MarketRegimeEngine:
    REQUIRED_INDICES = ("NIFTY 50", "NIFTY BANK")
    MIN_CANDLES = 50
    FRESH_SECONDS = 10.0

    # Explicit mappings only: an unmapped underlying is blocked rather than
    # silently trading without its sector confirmation.
    SECTOR_INDEX_BY_SYMBOL = {
        **dict.fromkeys(("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK"), "NIFTY BANK"),
        **dict.fromkeys(("TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"), "NIFTY IT"),
        **dict.fromkeys(("MARUTI", "TATAMOTORS", "M&M", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT", "TVSMOTOR", "ASHOKLEY", "BOSCHLTD"), "NIFTY AUTO"),
        **dict.fromkeys(("HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM", "DABUR", "GODREJCP", "MARICO", "COLPAL", "VBL"), "NIFTY FMCG"),
        **dict.fromkeys(("SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA", "BIOCON", "ALKEM", "MANKIND", "TORNTPHARM"), "NIFTY PHARMA"),
        **dict.fromkeys(("TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "SAIL", "NATIONALUM", "NMDC"), "NIFTY METAL"),
        **dict.fromkeys(("DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD", "LODHA"), "NIFTY REALTY"),
        **dict.fromkeys(("RELIANCE", "ONGC", "IOC", "BPCL", "GAIL", "OIL", "PETRONET", "IGL", "MGL"), "NIFTY OIL AND GAS"),
        **dict.fromkeys(("ZEEL", "PVRINOX", "SUNTV", "NETWORK18"), "NIFTY MEDIA"),
        **dict.fromkeys(("APOLLOHOSP", "MAXHEALTH", "FORTIS", "SYNGENE", "LALPATHLAB", "METROPOLIS"), "NIFTY HEALTHCARE"),
        **dict.fromkeys(("VOLTAS", "HAVELLS", "DIXON", "CROMPTON", "POLYCAB", "WHIRLPOOL", "BLUESTARCO"), "NIFTY CONSUMER DURABLES"),
        **dict.fromkeys(("BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "SHRIRAMFIN", "SBICARD", "HDFCAMC", "MUTHOOTFIN", "RECLTD", "PFC", "LICHSGFIN", "MANAPPURAM"), "NIFTY FIN SERVICE"),
        **dict.fromkeys(("NTPC", "POWERGRID", "ADANIPOWER", "TATAPOWER", "NHPC", "SJVN"), "NIFTY ENERGY"),
    }

    def __init__(self):
        self.lock = RLock()
        self.active_indices = list(self.REQUIRED_INDICES)
        self.candles = {}
        self.current = {}
        self.last_tick = {}
        self.states = {}
        self.register_indices(self.REQUIRED_INDICES)

    def register_indices(self, symbols):
        requested = list(dict.fromkeys(
            str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()
        ))
        with self.lock:
            self.active_indices = requested
            self.candles = {
                symbol: {
                    "5minute": deque(maxlen=500),
                    "15minute": deque(maxlen=300),
                }
                for symbol in requested
            }
            self.current = {symbol: {} for symbol in requested}
            self.last_tick = {}
            self.states = {
                symbol: {"regime": "UNKNOWN", "reason": "Historical baseline not loaded"}
                for symbol in requested
            }

    @classmethod
    def indices_for_symbols(cls, symbols):
        sectors = {
            cls.SECTOR_INDEX_BY_SYMBOL.get(str(symbol).strip().upper())
            for symbol in symbols
        }
        sectors.discard(None)
        return list(cls.REQUIRED_INDICES) + sorted(sectors)

    @classmethod
    def sector_index_for_symbol(cls, symbol):
        return cls.SECTOR_INDEX_BY_SYMBOL.get(str(symbol or "").strip().upper())

    def reset(self):
        self.register_indices(self.active_indices)

    @staticmethod
    def _normalize(rows):
        result = []
        for row in rows or []:
            stamp = row.get("date") or row.get("minute")
            if stamp is None:
                continue
            if isinstance(stamp, str):
                from datetime import datetime
                stamp = datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=IST)
            else:
                stamp = stamp.astimezone(IST)
            values = {
                key: float(row.get(key) or 0)
                for key in ("open", "high", "low", "close")
            }
            if values["close"] > 0 and values["high"] > 0 and values["low"] > 0:
                result.append({
                    "date": stamp, **values,
                    "volume": int(row.get("volume") or 0),
                })
        result.sort(key=lambda item: item["date"])
        return result

    def seed_history(self, symbol, interval, rows, now=None):
        symbol = str(symbol).strip().upper()
        if symbol not in self.candles or interval not in ("5minute", "15minute"):
            return 0
        normalized = self._normalize(rows)
        if now is not None:
            current_time = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
            width = 5 if interval == "5minute" else 15
            normalized = [
                candle for candle in normalized
                if candle["date"] + timedelta(minutes=width) <= current_time
            ]
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
            start = index - period + 1
            tr = sum(tr_values[start:index + 1])
            if tr <= 0:
                dx.append(0.0)
                continue
            pdi = 100 * sum(plus_dm[start:index + 1]) / tr
            mdi = 100 * sum(minus_dm[start:index + 1]) / tr
            denominator = pdi + mdi
            dx.append(100 * abs(pdi - mdi) / denominator if denominator else 0.0)
        return sum(dx[-period:]) / min(period, len(dx)) if dx else None

    def _timeframe_state(self, candles):
        rows = list(candles)
        closes = [row["close"] for row in rows]
        if len(rows) < self.MIN_CANDLES:
            return {
                "direction": "UNKNOWN",
                "reason": f"Need {self.MIN_CANDLES} completed candles; received {len(rows)}",
            }
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)
        adx = self._adx(rows)
        last = closes[-1]
        if ema20 is None or ema50 is None or adx is None:
            return {"direction": "UNKNOWN", "reason": "Indicator history is incomplete"}
        if adx < 18:
            return {"direction": "SIDEWAYS", "reason": "ADX below 18"}
        if last > ema20 > ema50:
            return {
                "direction": "BULLISH",
                "reason": "Close above EMA20 and EMA20 above EMA50",
            }
        if last < ema20 < ema50:
            return {
                "direction": "BEARISH",
                "reason": "Close below EMA20 and EMA20 below EMA50",
            }
        return {"direction": "MIXED", "reason": "Price and EMA alignment disagree"}

    def _refresh_state(self, symbol):
        five = self._timeframe_state(self.candles[symbol]["5minute"])
        fifteen = self._timeframe_state(self.candles[symbol]["15minute"])
        regime = "UNKNOWN"
        reason = "Both 5m and 15m index trends must be available and aligned"
        if "SIDEWAYS" in (five["direction"], fifteen["direction"]):
            regime, reason = "SIDEWAYS", "At least one index timeframe is sideways"
        elif five["direction"] == fifteen["direction"] and five["direction"] in ("BULLISH", "BEARISH"):
            regime, reason = five["direction"], "5m and 15m trends are aligned"
        elif "UNKNOWN" in (five["direction"], fifteen["direction"]):
            reason = "Insufficient historical candles for one or more timeframes"
        self.states[symbol] = {
            "regime": regime,
            "reason": reason,
            "5m": five,
            "15m": fifteen,
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
                    self.current[symbol][interval] = {
                        "date": bucket, "open": price, "high": price,
                        "low": price, "close": price, "volume": 0,
                    }
                elif current["date"] != bucket:
                    self.candles[symbol][interval].append(current)
                    self.current[symbol][interval] = {
                        "date": bucket, "open": price, "high": price,
                        "low": price, "close": price, "volume": 0,
                    }
                    self._refresh_state(symbol)
                else:
                    current["high"] = max(current["high"], price)
                    current["low"] = min(current["low"], price)
                    current["close"] = price

    def snapshot(self, underlying_symbol=None):
        required = list(self.REQUIRED_INDICES)
        if underlying_symbol:
            sector = self.sector_index_for_symbol(underlying_symbol)
            if sector and sector not in required:
                required.append(sector)
        with self.lock:
            now = monotonic()
            indices = {}
            for symbol in required:
                state = dict(self.states.get(symbol, {
                    "regime": "UNKNOWN", "reason": "Index was not registered",
                }))
                age = now - self.last_tick[symbol] if symbol in self.last_tick else None
                fresh = age is not None and 0 <= age <= self.FRESH_SECONDS
                indices[symbol] = {
                    **state,
                    "feed_fresh": fresh,
                    "tick_age_seconds": round(age, 2) if age is not None else None,
                }
        regimes = [indices[symbol]["regime"] for symbol in required]
        if not all(indices[symbol]["feed_fresh"] for symbol in required):
            overall, reason = "UNKNOWN", "Fresh live ticks are required for every market and sector index"
        elif "SIDEWAYS" in regimes:
            overall, reason = "SIDEWAYS", "At least one required index is sideways"
        elif "UNKNOWN" in regimes:
            overall, reason = "UNKNOWN", "One or more required index regimes are unavailable"
        elif "MIXED" in regimes:
            overall, reason = "MIXED", "One or more required index directions are mixed"
        elif all(value == "BULLISH" for value in regimes):
            overall, reason = "BULLISH", "All required indices confirm bullish direction"
        elif all(value == "BEARISH" for value in regimes):
            overall, reason = "BEARISH", "All required indices confirm bearish direction"
        else:
            overall, reason = "MIXED", "Required index directions do not agree"
        return {
            "regime": overall,
            "reason": reason,
            "required_indices": required,
            "indices": indices,
        }

    def allows(self, direction, underlying_symbol=None):
        if not underlying_symbol:
            snapshot = self.snapshot()
            required = "BULLISH" if str(direction).upper() == "CE" else "BEARISH"
            return snapshot["regime"] == required, snapshot
        sector = self.sector_index_for_symbol(underlying_symbol)
        if not sector:
            return False, {
                "regime": "UNKNOWN",
                "reason": f"No approved sector-index mapping for {underlying_symbol}; entry blocked",
                "indices": {},
                "required_indices": list(self.REQUIRED_INDICES),
            }
        snapshot = self.snapshot(underlying_symbol)
        required = "BULLISH" if str(direction).upper() == "CE" else "BEARISH"
        return snapshot["regime"] == required, snapshot

import asyncio
import logging
import threading
import time as clock
from datetime import date, datetime, timezone
from typing import Any

from kiteconnect import KiteConnect, KiteTicker

logger = logging.getLogger(__name__)


class MarketDataEngine:
    """Single-process live market feed for the currently authenticated local user."""
    MAX_SUBSCRIPTIONS = 3000
    INDEX_NAMES = {
        "NIFTY 50", "NIFTY BANK", "NIFTY IT", "NIFTY FIN SERVICE",
        "NIFTY MIDCAP 50", "NIFTY NEXT 50", "NIFTY 100", "NIFTY 200",
        "NIFTY 500", "NIFTY AUTO", "NIFTY FMCG", "NIFTY PHARMA",
        "NIFTY METAL", "NIFTY REALTY", "NIFTY PSU BANK",
        "NIFTY PRIVATE BANK", "NIFTY ENERGY", "NIFTY MEDIA",
        "NIFTY HEALTHCARE", "NIFTY OIL AND GAS", "NIFTY CONSUMER DURABLES",
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._ticker = None
        self._kite = None
        self._access_token = None
        self._status = "disconnected"
        self._error = None
        self._instruments: dict[int, dict[str, Any]] = {}
        self._ticks: dict[int, dict[str, Any]] = {}
        self._subscribed: list[int] = []
        self._loop = None
        self._clients: set[asyncio.Queue] = set()
        self._last_tick_received_at = None
        self._last_tick_monotonic = None
        self._invalid_tick_count = 0
        self._fno_symbols: set[str] = set()
        self._option_instruments: list[dict[str, Any]] = []
        self._full_mode_tokens: set[int] = set()
        self._last_index_history_diagnostics: dict[str, dict[str, Any]] = {}
        # Local command status only; KiteTicker exposes no per-token exchange acknowledgement.
        self._index_tick_diagnostics: dict[str, dict[str, Any]] = {}
        self._index_subscription_errors: dict[str, str] = {}

    def _set_status(self, status: str, error: str | None = None):
        with self._lock:
            self._status, self._error = status, error
        self._publish({"type": "status", "status": status, "error": error})

    def start(self, api_key: str, access_token: str):
        with self._lock:
            if self._access_token == access_token and self._status in ("loading_instruments", "connecting", "connected"):
                return
            if self._ticker:
                try:
                    self._ticker.close()
                except Exception:
                    logger.exception("Unable to close previous KiteTicker")
            self._ticker = None
            self._access_token = access_token
            self._status, self._error = "loading_instruments", None
        try:
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
            self._kite = kite
            rows = kite.instruments()
            today = date.today()
            equities = [r for r in rows if r.get("exchange") == "NSE" and r.get("segment") == "NSE" and r.get("instrument_type") == "EQ"]
            futures = [r for r in rows if r.get("exchange") == "NFO" and r.get("instrument_type") == "FUT" and r.get("expiry") and r["expiry"] >= today]
            self._fno_symbols = {str(r.get("name") or "").strip().upper() for r in rows if r.get("exchange") == "NFO" and r.get("instrument_type") in ("FUT", "CE", "PE") and r.get("name")}
            self._option_instruments = [{**r, "expiry": r["expiry"].isoformat() if r.get("expiry") else None, "strike": float(r.get("strike") or 0), "lot_size": int(r.get("lot_size") or 0), "instrument_token": int(r["instrument_token"])} for r in rows if r.get("exchange") == "NFO" and r.get("instrument_type") in ("CE", "PE") and r.get("expiry") and r.get("name")]
            nearest: dict[str, dict[str, Any]] = {}
            for row in futures:
                name = row.get("name") or row.get("tradingsymbol", "")
                if name not in nearest or row["expiry"] < nearest[name]["expiry"]:
                    nearest[name] = row
            indices = [r for r in rows if r.get("exchange") == "NSE" and r.get("segment") == "INDICES" and r.get("tradingsymbol") in self.INDEX_NAMES]
            selected = (indices + equities + list(nearest.values()))[:self.MAX_SUBSCRIPTIONS]
            instrument_map = {int(r["instrument_token"]): {
                "instrument_token": int(r["instrument_token"]), "symbol": r.get("tradingsymbol"),
                "name": r.get("name") or r.get("tradingsymbol"), "exchange": r.get("exchange"),
                "segment": r.get("segment"), "instrument_type": "INDEX" if r.get("segment") == "INDICES" else (r.get("instrument_type") or "EQ"),
                "expiry": r.get("expiry").isoformat() if r.get("expiry") else None, "lot_size": r.get("lot_size"), "strike": float(r.get("strike") or 0), "tick_size": float(r.get("tick_size") or 0.05)
            } for r in selected}
            tokens = list(instrument_map)
            with self._lock:
                self._instruments, self._subscribed, self._ticks = instrument_map, tokens, {}
                self._last_tick_received_at = None
                self._last_tick_monotonic = None
                self._full_mode_tokens.clear()
                self._invalid_tick_count = 0
                self._index_tick_diagnostics = {
                    str(row.get("symbol") or "").strip().upper(): {
                        "instrument_token": int(row["instrument_token"]),
                        "subscription_requested": False,
                        "last_tick_received_at": None,
                        "last_tick_monotonic": None,
                        "raw_ticks": 0, "valid_ticks": 0, "invalid_ticks": 0,
                    }
                    for row in instrument_map.values() if row.get("segment") == "INDICES"
                }
                self._index_subscription_errors = {}
            ticker = KiteTicker(api_key, access_token)
            ticker.on_ticks, ticker.on_connect = self._on_ticks, self._on_connect
            ticker.on_close, ticker.on_error = self._on_close, self._on_error
            ticker.on_reconnect, ticker.on_noreconnect = self._on_reconnect, self._on_noreconnect
            with self._lock:
                self._ticker, self._status = ticker, "connecting"
            ticker.connect(threaded=True)
        except Exception as exc:
            logger.exception("Market data engine startup failed")
            self._set_status("error", type(exc).__name__)
            raise

    def _on_connect(self, ws, response):
        with self._lock:
            tokens = list(self._subscribed)
            full_tokens = list(self._full_mode_tokens)
        if tokens:
            try:
                ws.subscribe(tokens)
                full_token_set = set(full_tokens)
                quote_tokens = [token for token in tokens if token not in full_token_set]
                if quote_tokens:
                    ws.set_mode(ws.MODE_QUOTE, quote_tokens)
                if full_tokens:
                    ws.set_mode(ws.MODE_FULL, full_tokens)
                with self._lock:
                    for item in self._index_tick_diagnostics.values():
                        item["subscription_requested"] = True
            except Exception as exc:
                with self._lock:
                    self._index_subscription_errors = {
                        symbol: f"{type(exc).__name__}: {exc}"
                        for symbol in self._index_tick_diagnostics
                    }
                raise
        self._set_status("connected")
        # Restore subscriptions for persisted Price Action PAPER positions after
        # a backend/websocket restart. The service keeps positions in SQLite.
        try:
            price_action_service.on_feed_connected()
        except Exception:
            logger.exception("Unable to restore Price Action open-position subscriptions")
        try:
            breakfast_service.on_feed_connected()
        except Exception:
            logger.exception("Unable to restore Breakfast open-position subscriptions")
        logger.info("KiteTicker connected; subscribed to %s instruments", len(tokens))

    def _on_ticks(self, ws, ticks):
        changed = []
        invalid = 0
        with self._lock:
            for tick in ticks:
                token = int(tick["instrument_token"])
                meta = self._instruments.get(token)
                if not meta:
                    continue
                index_symbol = str(meta.get("symbol") or "").strip().upper()
                index_diagnostic = self._index_tick_diagnostics.get(index_symbol) if meta.get("segment") == "INDICES" else None
                if index_diagnostic is not None:
                    index_diagnostic["raw_ticks"] += 1
                    index_diagnostic["last_tick_received_at"] = datetime.now(timezone.utc).isoformat()
                    index_diagnostic["last_tick_monotonic"] = clock.monotonic()
                ohlc = tick.get("ohlc") or {}
                close = float(ohlc.get("close") or 0)
                ltp = float(tick.get("last_price") or 0)
                # Never interpret missing/zero prices as a 100% loss or market breadth.
                if ltp <= 0 or close <= 0:
                    invalid += 1
                    if index_diagnostic is not None:
                        index_diagnostic["invalid_ticks"] += 1
                    continue
                if index_diagnostic is not None:
                    index_diagnostic["valid_ticks"] += 1
                enriched = {**meta, "last_price": ltp, "close": close,
                    "open": float(ohlc.get("open") or 0), "high": float(ohlc.get("high") or 0),
                    "low": float(ohlc.get("low") or 0), "volume": int(tick.get("volume") or 0),
                    "change": round(ltp-close, 2),
                    "change_percent": round((ltp-close)/close*100, 2),
                    "exchange_timestamp": tick.get("exchange_timestamp").isoformat() if tick.get("exchange_timestamp") else None}
                depth = tick.get("depth") or {}
                enriched["depth"] = depth
                self._instruments[token] = meta
                self._ticks[token] = enriched
                changed.append(enriched)
            self._invalid_tick_count += invalid
            if changed:
                self._last_tick_received_at = datetime.now(timezone.utc).isoformat()
                self._last_tick_monotonic = clock.monotonic()
            equity = [r for r in self._ticks.values() if r["instrument_type"] == "EQ"]
            advances = sum(r["change"] > 0 for r in equity)
            declines = sum(r["change"] < 0 for r in equity)
            unchanged = sum(r["change"] == 0 for r in equity)
            breadth = {"advances": advances, "declines": declines, "unchanged": unchanged,
                "total": len(equity), "advance_decline_ratio": round(advances/declines, 2) if declines else None}
        if changed:
            try:
                opening_breakout_service.on_feed_connected()
            except Exception:
                logger.exception("Unable to restore Opening Breakout open-position subscriptions")
            try:
                opening_breakout_service.on_market_tick(changed)
            except Exception:
                logger.exception("Opening breakout tick processing failed")
            try:
                price_action_service.on_market_tick(changed)
            except Exception:
                logger.exception("Price Action Intraday tick processing failed")
            try:
                breakfast_service.on_market_tick(changed)
            except Exception:
                logger.exception("Breakfast Strategy tick processing failed")
            self._publish({"type": "ticks", "data": changed, "breadth": breadth,
                           "last_tick_received_at": self._last_tick_received_at})

    def _on_close(self, ws, code, reason):
        self._set_status("disconnected", str(reason) if reason else None)

    def _on_error(self, ws, code, reason):
        logger.warning("KiteTicker error %s: %s", code, reason)
        self._set_status("error", str(reason))

    def _on_reconnect(self, ws, attempts):
        self._set_status("reconnecting")

    def _on_noreconnect(self, ws):
        self._set_status("disconnected", "Reconnect attempts exhausted")

    def stop(self, access_token: str | None = None):
        with self._lock:
            if access_token and self._access_token != access_token:
                return
            ticker, self._ticker = self._ticker, None
            self._access_token = None
            self._kite = None
            self._ticks.clear()
            self._subscribed.clear()
            self._fno_symbols.clear()
            self._option_instruments.clear()
            self._full_mode_tokens.clear()
            self._last_tick_received_at = None
            self._last_tick_monotonic = None
            self._status, self._error = "disconnected", None
        if ticker:
            try:
                ticker.close()
            except Exception:
                logger.exception("KiteTicker shutdown failed")
        self._publish({"type": "status", "status": "disconnected"})

    def option_instruments(self, underlying: str | None = None):
        with self._lock:
            rows = list(self._option_instruments)
        return [r for r in rows if underlying is None or str(r.get("name") or "").upper() == underlying.upper()]

    def subscribe_extra(self, tokens: list[int], full: bool = True):
        normalized = list(dict.fromkeys(int(token) for token in tokens))
        if not normalized:
            return False

        with self._lock:
            ws = self._ticker
            if not ws:
                return False

            option_by_token = {
                int(row["instrument_token"]): row
                for row in self._option_instruments
            }
            if any(token not in option_by_token for token in normalized):
                return False

            newly_added = [
                token for token in normalized
                if token not in self._subscribed
            ]
            previous_full = {
                token for token in normalized
                if token in self._full_mode_tokens
            }
            for token in normalized:
                row = option_by_token[token]
                self._instruments[token] = {
                    "instrument_token": token,
                    "symbol": row.get("tradingsymbol"),
                    "name": row.get("name") or row.get("tradingsymbol"),
                    "exchange": "NFO",
                    "segment": row.get("segment") or "NFO-OPT",
                    "instrument_type": row.get("instrument_type"),
                    "expiry": row.get("expiry"),
                    "lot_size": int(row.get("lot_size") or 0),
                    "strike": float(row.get("strike") or 0),
                    "tick_size": float(row.get("tick_size") or 0.05),
                }
                if token not in self._subscribed:
                    self._subscribed.append(token)
                if full:
                    self._full_mode_tokens.add(token)

        try:
            if newly_added:
                ws.subscribe(newly_added)
            ws.set_mode(
                ws.MODE_FULL if full else ws.MODE_QUOTE,
                normalized,
            )
            if not full:
                with self._lock:
                    self._full_mode_tokens.difference_update(normalized)
        except Exception:
            # Roll back the local subscription registry so a failed option
            # subscription is not silently re-added on the next reconnect.
            with self._lock:
                for token in newly_added:
                    if token in self._subscribed:
                        self._subscribed.remove(token)
                    self._instruments.pop(token, None)
                    self._full_mode_tokens.discard(token)
                for token in normalized:
                    if token in previous_full:
                        self._full_mode_tokens.add(token)
                    elif full:
                        self._full_mode_tokens.discard(token)
            if newly_added:
                try:
                    ws.unsubscribe(newly_added)
                except Exception:
                    logger.exception(
                        "Unable to roll back failed option subscription"
                    )
            raise

        return True


    def historical_minute_candles(self, symbols, start_at, end_at):
        """Fetch today's real NSE minute candles for the requested underlyings."""
        from datetime import timedelta
        with self._lock:
            kite = self._kite
            instrument_rows = list(self._instruments.values())
        if kite is None:
            raise RuntimeError("Zerodha historical-data client is not ready.")
        token_by_symbol = {
            str(row.get("symbol") or "").upper(): int(row["instrument_token"])
            for row in instrument_rows
            if row.get("exchange") == "NSE" and row.get("instrument_type") == "EQ"
        }
        result = {}
        for symbol in sorted(set(str(value).upper() for value in symbols)):
            token = token_by_symbol.get(symbol)
            if not token:
                result[symbol] = []
                continue
            try:
                rows = kite.historical_data(token, start_at, end_at, "minute")
                result[symbol] = rows
            except Exception:
                logger.exception("Historical minute candles unavailable for %s", symbol)
                result[symbol] = []
            # Zerodha historical endpoint is rate-limited; pace per instrument.
            clock.sleep(0.35)
        return result

    def index_instruments(self):
        """Return the actual NSE index instruments loaded from Zerodha's instrument master."""
        with self._lock:
            return [
                dict(row) for row in self._instruments.values()
                if row.get("exchange") == "NSE" and row.get("segment") == "INDICES"
            ]

    def historical_index_candles(self, symbols, start_at, end_at, interval="minute"):
        """Fetch real Zerodha historical candles for known NSE indices only.

        Supported intervals mirror KiteConnect's documented candle intervals.
        Missing index instruments or individual history failures return an empty
        list for that symbol; a missing authenticated Kite client is a hard error.
        """
        allowed_intervals = {"minute", "5minute", "15minute"}
        if interval not in allowed_intervals:
            raise ValueError("Unsupported index candle interval.")

        with self._lock:
            kite = self._kite
            index_rows = [
                dict(row) for row in self._instruments.values()
                if row.get("exchange") == "NSE" and row.get("segment") == "INDICES"
            ]
        if kite is None:
            raise RuntimeError("Zerodha historical-data client is not ready.")

        token_by_symbol = {
            str(row.get("symbol") or "").strip().upper(): int(row["instrument_token"])
            for row in index_rows
        }
        result = {}
        diagnostics = {}
        for symbol in sorted(set(str(value).strip().upper() for value in symbols)):
            token = token_by_symbol.get(symbol)
            if token is None:
                result[symbol] = []
                diagnostics[symbol] = {
                    "status": "MISSING_INSTRUMENT",
                    "error": f"{symbol} was not found in Zerodha NSE INDICES instrument master",
                    "candle_count": 0,
                }
                continue
            try:
                rows = kite.historical_data(token, start_at, end_at, interval)
                result[symbol] = rows
                diagnostics[symbol] = {
                    "status": "LOADED" if rows else "EMPTY",
                    "error": None if rows else "Zerodha returned no candles",
                    "candle_count": len(rows),
                }
            except Exception as exc:
                logger.exception(
                    "Historical %s candles unavailable for index %s",
                    interval, symbol,
                )
                result[symbol] = []
                diagnostics[symbol] = {
                    "status": "FETCH_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                    "candle_count": 0,
                }
            # Pace requests to avoid bursting Zerodha's historical endpoint.
            clock.sleep(0.35)
        with self._lock:
            for symbol, diagnostic in diagnostics.items():
                self._last_index_history_diagnostics[f"{symbol}:{interval}"] = diagnostic
        return result

    def index_history_diagnostics(self):
        with self._lock:
            return {key: dict(value) for key, value in self._last_index_history_diagnostics.items()}

    def index_feed_diagnostics(self, symbols):
        """Return per-index instrument, local subscription and tick diagnostics.

        subscription_requested confirms local subscribe/mode calls only, not an
        exchange acknowledgement.
        """
        requested = list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
        with self._lock:
            now = clock.monotonic()
            by_symbol = {
                str(row.get("symbol") or "").strip().upper(): row
                for row in self._instruments.values() if row.get("segment") == "INDICES"
            }
            result = {}
            for symbol in requested:
                instrument = by_symbol.get(symbol)
                diagnostic = dict(self._index_tick_diagnostics.get(symbol, {}))
                token = int(instrument["instrument_token"]) if instrument else None
                last_monotonic = diagnostic.pop("last_tick_monotonic", None)
                age = now - last_monotonic if last_monotonic is not None else None
                result[symbol] = {
                    "instrument_found": instrument is not None,
                    "instrument_token": token,
                    "exchange": instrument.get("exchange") if instrument else None,
                    "segment": instrument.get("segment") if instrument else None,
                    "subscribed_locally": token in self._subscribed if token is not None else False,
                    "subscription_requested": bool(diagnostic.get("subscription_requested", False)),
                    "subscription_error": self._index_subscription_errors.get(symbol),
                    "last_tick_received_at": diagnostic.get("last_tick_received_at"),
                    "tick_age_seconds": round(age, 2) if age is not None and age >= 0 else None,
                    "raw_ticks": int(diagnostic.get("raw_ticks", 0)),
                    "valid_ticks": int(diagnostic.get("valid_ticks", 0)),
                    "invalid_ticks": int(diagnostic.get("invalid_ticks", 0)),
                }
        return result

    def universe_symbols(self):
        with self._lock:
            return sorted(self._fno_symbols)

    def is_feed_fresh(self, max_age_seconds: int = 5):
        """Return true only while connected and receiving valid ticks recently.

        Monotonic time avoids wall-clock adjustments making an old feed appear
        fresh (or a fresh feed appear stale).
        """
        with self._lock:
            last_tick = self._last_tick_monotonic
            status = self._status
        if status != "connected" or last_tick is None:
            return False
        age = clock.monotonic() - last_tick
        return 0 <= age <= max_age_seconds

    def snapshot(self):
        with self._lock:
            rows = list(self._ticks.values())
            instruments, status, error = len(self._subscribed), self._status, self._error
            last_tick = self._last_tick_received_at
            invalid = self._invalid_tick_count
        equity = [r for r in rows if r["instrument_type"] == "EQ"]
        futures = [r for r in rows if r["instrument_type"] == "FUT"]
        indices = [r for r in rows if r["segment"] == "INDICES"]
        def ranked(items, positive):
            valid = [r for r in items if r["last_price"] > 0 and r["close"] > 0]
            return sorted(valid, key=lambda r: r["change_percent"], reverse=positive)
        advances, declines = sum(r["change"] > 0 for r in equity), sum(r["change"] < 0 for r in equity)
        unchanged = sum(r["change"] == 0 for r in equity)
        return {"status": status, "error": error, "subscribed_instruments": instruments,
            "received_instruments": len(rows), "invalid_ticks_ignored": invalid,
            "last_tick_received_at": last_tick,
            "breadth": {"advances": advances, "declines": declines, "unchanged": unchanged,
                "total": len(equity), "advance_decline_ratio": round(advances/declines, 2) if declines else None},
            "equity": {"gainers": ranked(equity, True)[:50], "losers": ranked(equity, False)[:50]},
            "futures": {"gainers": ranked(futures, True)[:50], "losers": ranked(futures, False)[:50]},
            "all_equity": equity, "all_futures": futures, "indices": indices,
            "updated_at": max((r["exchange_timestamp"] or "" for r in rows), default=None)}

    def _publish(self, message):
        loop = self._loop
        if not loop or loop.is_closed():
            return
        def deliver():
            for queue in tuple(self._clients):
                try:
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        loop.call_soon_threadsafe(deliver)

    async def subscribe(self, websocket):
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._clients.add(queue)
        await queue.put({"type": "snapshot", "data": self.snapshot()})
        try:
            while True:
                yield await queue.get()
        finally:
            self._clients.discard(queue)


market_data_engine = MarketDataEngine()

# Import after the singleton exists to avoid circular dependencies.
from app.core.opening_breakout.service import OpeningBreakoutService
from app.core.price_action_service import PriceActionService
from app.core.breakfast_service import BreakfastService
opening_breakout_service = OpeningBreakoutService(market_data_engine)
price_action_service = PriceActionService(market_data_engine)
breakfast_service = BreakfastService(market_data_engine)

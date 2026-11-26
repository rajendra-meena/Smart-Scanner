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
        self._access_token = None
        self._status = "disconnected"
        self._error = None
        self._instruments: dict[int, dict[str, Any]] = {}
        self._ticks: dict[int, dict[str, Any]] = {}
        self._subscribed: list[int] = []
        self._loop = None
        self._clients: set[asyncio.Queue] = set()
        self._last_tick_received_at = None
        self._invalid_tick_count = 0
        self._fno_symbols: set[str] = set()
        self._option_instruments: list[dict[str, Any]] = []

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
            selected = (equities + list(nearest.values()) + indices)[:self.MAX_SUBSCRIPTIONS]
            instrument_map = {int(r["instrument_token"]): {
                "instrument_token": int(r["instrument_token"]), "symbol": r.get("tradingsymbol"),
                "name": r.get("name") or r.get("tradingsymbol"), "exchange": r.get("exchange"),
                "segment": r.get("segment"), "instrument_type": r.get("instrument_type") or "INDEX",
                "expiry": r.get("expiry").isoformat() if r.get("expiry") else None, "lot_size": r.get("lot_size")
            } for r in selected}
            tokens = list(instrument_map)
            with self._lock:
                self._instruments, self._subscribed, self._ticks = instrument_map, tokens, {}
                self._last_tick_received_at = None
                self._invalid_tick_count = 0
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
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_QUOTE, tokens)
        self._set_status("connected")
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
                ohlc = tick.get("ohlc") or {}
                close = float(ohlc.get("close") or 0)
                ltp = float(tick.get("last_price") or 0)
                # Never interpret missing/zero prices as a 100% loss or market breadth.
                if ltp <= 0 or close <= 0:
                    invalid += 1
                    continue
                enriched = {**meta, "last_price": ltp, "close": close,
                    "open": float(ohlc.get("open") or 0), "high": float(ohlc.get("high") or 0),
                    "low": float(ohlc.get("low") or 0), "volume": int(tick.get("volume") or 0),
                    "change": round(ltp-close, 2),
                    "change_percent": round((ltp-close)/close*100, 2),
                    "exchange_timestamp": tick.get("exchange_timestamp").isoformat() if tick.get("exchange_timestamp") else None}
                depth = tick.get("depth") or {}
                enriched["depth"] = depth
                self._ticks[token] = enriched
                changed.append(enriched)
            self._invalid_tick_count += invalid
            if changed:
                self._last_tick_received_at = datetime.now(timezone.utc).isoformat()
            equity = [r for r in self._ticks.values() if r["instrument_type"] == "EQ"]
            advances = sum(r["change"] > 0 for r in equity)
            declines = sum(r["change"] < 0 for r in equity)
            unchanged = sum(r["change"] == 0 for r in equity)
            breadth = {"advances": advances, "declines": declines, "unchanged": unchanged,
                "total": len(equity), "advance_decline_ratio": round(advances/declines, 2) if declines else None}
        if changed:
            try:
                opening_breakout_service.on_market_tick(changed)
            except Exception:
                logger.exception("Opening breakout tick processing failed")
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
            self._ticks.clear()
            self._subscribed.clear()
            self._fno_symbols.clear()
            self._option_instruments.clear()
            self._last_tick_received_at = None
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
        with self._lock:
            ws = self._ticker
        if not ws or not tokens:
            return False
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL if full else ws.MODE_QUOTE, tokens)
        return True

    def universe_symbols(self):
        with self._lock:
            return sorted(self._fno_symbols)

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

# Import after the singleton exists to avoid a circular dependency.
from app.core.opening_breakout.service import OpeningBreakoutService
opening_breakout_service = OpeningBreakoutService(market_data_engine)

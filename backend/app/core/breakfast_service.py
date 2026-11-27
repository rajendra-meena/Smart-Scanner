"""Independent PAPER-only Breakfast Strategy lifecycle."""
import json
import sqlite3
from datetime import datetime, time
from pathlib import Path
from threading import RLock
from uuid import uuid4
from zoneinfo import ZoneInfo

from .breakfast_engine import BreakfastEngine

IST = ZoneInfo("Asia/Kolkata")


class BreakfastService:
    DEFAULTS = {
        "enabled": True, "auto_start": True, "mode": "PAPER",
        "top_stock_count": 3, "max_positions": 3, "max_trades_per_day": 3,
        "sector_breadth_percent": 60.0, "excessive_candle_percent": 5.0,
        "target_percent": 1.0, "stop_loss_percent": 1.0,
        "negative_nifty_long_target_percent": 0.5,
        "psychological_adjustment_enabled": True, "psychological_adjustment_amount": 2.0,
        "entry_time": "09:21:00", "exit_time": "15:15:00",
    }

    def __init__(self, market_data):
        self.market_data = market_data
        self.lock = RLock()
        self.db_path = Path("./data/breakfast_trades.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS breakfast_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS breakfast_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS breakfast_open_positions (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        with sqlite3.connect(self.db_path) as db:
            saved = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM breakfast_settings")}
            restored = [json.loads(r[0]) for r in db.execute("SELECT payload FROM breakfast_open_positions")]
        self.config = {**self.DEFAULTS, **saved}
        self.engine = BreakfastEngine()
        self.running = False
        self.positions = {p["id"]: p for p in restored if p.get("status") == "OPEN"}
        for p in self.positions.values():
            p["quote_stale"] = True
            p["pnl"] = None
        self.first_candles = {}
        self.selection = None
        self.events = []
        self.pending = {}
        self.latest_option_quotes = {}
        self.attempted_day = None
        self.session_day = None

    def _event(self, name, **fields):
        self.events.append({"time": datetime.now(IST).isoformat(), "event": name, **fields})
        self.events = self.events[-150:]

    def _persist_position(self, p):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO breakfast_open_positions(id,payload) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (p["id"], json.dumps(p, default=str)))

    def on_feed_connected(self):
        tokens = [int(p["token"]) for p in self.positions.values() if p.get("status") == "OPEN"]
        if tokens:
            try:
                self.market_data.subscribe_extra(tokens, full=True)
            except Exception as exc:
                self._event("POSITION_RESTORE_SUBSCRIPTION_FAILED", reason=str(exc))

    def save_settings(self, values):
        with self.lock:
            if self.running:
                raise RuntimeError("Stop Breakfast Strategy before changing settings.")
            self.config.update(values)
            with sqlite3.connect(self.db_path) as db:
                db.executemany("INSERT INTO breakfast_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(k, json.dumps(v)) for k,v in values.items()])
            return self.settings()

    def reset_settings(self):
        with self.lock:
            if self.running:
                raise RuntimeError("Stop Breakfast Strategy before resetting settings.")
            self.config = dict(self.DEFAULTS)
            with sqlite3.connect(self.db_path) as db:
                db.execute("DELETE FROM breakfast_settings")
            return self.settings()

    def settings(self):
        return {"config": dict(self.config), "mode": "PAPER", "live_ready": False,
                "live_block_reason": "Breakfast Strategy supports PAPER simulation only; broker order placement is not implemented."}

    def start(self):
        with self.lock:
            now = datetime.now(IST)
            if not self.config["enabled"]:
                raise RuntimeError("Breakfast Strategy is disabled.")
            if self.running:
                raise RuntimeError("Breakfast Strategy is already running.")
            if not self.market_data.is_feed_fresh(5):
                raise RuntimeError("Zerodha feed is not fresh.")
            if now.time() < time(9, 20) or now.time() >= time.fromisoformat(self.config["exit_time"]):
                raise RuntimeError("Start is allowed from 09:20 IST until configured exit time.")
            self.running = True
            self._event("PAPER_STARTED", detail="Collecting first 5-minute candle and waiting for the configured entry time")
            return self.snapshot()

    def stop(self):
        with self.lock:
            now = datetime.now(IST)
            self.running = False
            for pending in self.pending.values():
                self._event("PENDING_CANCELLED", symbol=pending["symbol"], reason="STRATEGY_STOP")
            self.pending.clear()
            for p in list(self.positions.values()):
                if p.get("status") == "OPEN":
                    p["exit_requested"] = True
                    self._persist_position(p)
            self._event("PAPER_STOPPED", detail="Open positions remain monitored until a fresh option bid is available")
            return self.snapshot()

    def _first_candle_tick(self, row, now):
        if now.time() < time(9, 15) or now.time() >= time(9, 20):
            return
        symbol = str(row.get("symbol") or "").upper()
        if row.get("instrument_type") != "EQ" or symbol not in BreakfastEngine.SECTOR_BY_SYMBOL:
            return
        price = float(row.get("last_price") or 0)
        if price <= 0:
            return
        candle = self.first_candles.setdefault(symbol, {"open": price, "high": price, "low": price, "close": price})
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price

    def _build_selection(self, now):
        snapshot = self.market_data.snapshot()
        indices = {str(r.get("symbol") or "").upper(): r for r in snapshot.get("indices", [])}
        nifty = indices.get("NIFTY 50")
        if not nifty or float(nifty.get("last_price") or 0) <= 0:
            self._event("SELECTION_BLOCKED", reason="NIFTY live index quote unavailable")
            return None
        option_rows = self.market_data.option_instruments()
        option_symbols = {str(r.get("name") or "").upper() for r in option_rows}
        result = self.engine.select(
            snapshot.get("all_equity", []), float(nifty.get("change_percent") or 0),
            self.first_candles, option_symbols,
            int(self.config["top_stock_count"]), float(self.config["excessive_candle_percent"]),
            float(self.config["sector_breadth_percent"]),
        )
        self.selection = result
        self._event("SECTOR_STOCK_SELECTION", direction=result.get("direction"), sector=result.get("sector"),
                    candidates=[c["symbol"] for c in result.get("candidates", [])], reason=result.get("reason"))
        return result

    def _queue_option(self, candidate, now):
        direction = "CE" if self.selection["direction"] == "LONG" else "PE"
        options = [r for r in self.market_data.option_instruments(candidate["symbol"])
                   if r.get("instrument_type") == direction and r.get("expiry") and r["expiry"] >= now.date().isoformat()]
        if not options:
            self._event("ENTRY_SKIPPED", symbol=candidate["symbol"], reason="No current ATM option contract")
            return
        expiry = min(r["expiry"] for r in options)
        chain = [r for r in options if r["expiry"] == expiry]
        contract = min(chain, key=lambda r: abs(float(r.get("strike") or 0) - candidate["price"]))
        token = int(contract["instrument_token"])
        if not self.market_data.subscribe_extra([token], full=True):
            self._event("ENTRY_SKIPPED", symbol=candidate["symbol"], reason="Option quote subscription failed")
            return
        self.pending[token] = {**candidate, "token": token, "direction": direction,
            "option_symbol": contract["tradingsymbol"], "lot_size": int(contract.get("lot_size") or 0),
            "requested_at": now.isoformat()}
        self._event("OPTION_SUBSCRIPTION_REQUESTED", symbol=candidate["symbol"], option=contract["tradingsymbol"], direction=direction)

    def _equity_levels(self, price, direction, nifty_change):
        target_pct = float(self.config["target_percent"])
        if direction == "LONG":
            if float(nifty_change or 0) < 0:
                target_pct = float(self.config["negative_nifty_long_target_percent"])
            target = price * (1 + target_pct / 100)
            stop = price * (1 - float(self.config["stop_loss_percent"]) / 100)
        else:
            target = price * (1 - target_pct / 100)
            stop = price * (1 + float(self.config["stop_loss_percent"]) / 100)
        if self.config["psychological_adjustment_enabled"]:
            adjustment = float(self.config["psychological_adjustment_amount"])
            level = round(target / 100) * 100
            if abs(target - level) <= adjustment:
                target = level - adjustment if direction == "LONG" else level + adjustment
        return round(target, 2), round(stop, 2)

    def on_market_tick(self, rows):
        now = datetime.now(IST)
        with self.lock:
            day_key = now.date().isoformat()
            if self.session_day != day_key:
                self.session_day = day_key
                self.first_candles = {}
                self.selection = None
                self.attempted_day = None
                self._event("NEW_TRADING_SESSION", trade_date=day_key)
            for token, pending in list(self.pending.items()):
                requested = datetime.fromisoformat(pending["requested_at"])
                if (now - requested).total_seconds() > 5:
                    self.pending.pop(token, None)
                    self._event("PENDING_CANCELLED", symbol=pending["symbol"], reason="OPTION_QUOTE_TIMEOUT")
            for row in rows:
                self._first_candle_tick(row, now)
            for row in rows:
                if row.get("instrument_type") in ("CE", "PE"):
                    self._on_option_tick(row, now)
            if self.config["enabled"] and self.config["auto_start"] and not self.running and self.attempted_day != now.date().isoformat() and time(9, 20) <= now.time() < time(9, 30):
                self.attempted_day = now.date().isoformat()
                try:
                    self.start()
                except RuntimeError as exc:
                    self.attempted_day = None
                    self._event("AUTO_START_FAILED", reason=str(exc))
            if self.running and self.selection is None and now.time() >= time.fromisoformat(self.config["entry_time"]):
                result = self._build_selection(now)
                if result and result.get("ready"):
                    self._queue_candidates(result, now)
            self._monitor_equity_positions(rows, now)
            if self.running and now.time() >= time.fromisoformat(self.config["exit_time"]):
                self.stop()

    def _queue_candidates(self, result, now):
        max_positions = int(self.config["max_positions"])
        max_daily = int(self.config["max_trades_per_day"])
        already = len(self.trades(now.date().isoformat()))
        active = sum(p.get("status") == "OPEN" for p in self.positions.values()) + len(self.pending)
        capacity = max(0, min(max_positions - active, max_daily - already - len(self.pending)))
        active_symbols = {str(p.get("symbol") or "").upper() for p in self.positions.values() if p.get("status") == "OPEN"}
        active_symbols.update(str(p.get("symbol") or "").upper() for p in self.pending.values())
        queued = 0
        for candidate in result["candidates"]:
            if queued >= capacity:
                break
            if candidate["symbol"] in active_symbols:
                self._event("ENTRY_SKIPPED", symbol=candidate["symbol"], reason="An open or pending position already exists for this stock")
                continue
            self._queue_option(candidate, now)
            active_symbols.add(candidate["symbol"])
            queued += 1

    def _monitor_equity_positions(self, rows, now):
        by_symbol = {str(r.get("symbol") or "").upper(): r for r in rows if r.get("instrument_type") == "EQ"}
        for p in list(self.positions.values()):
            if p.get("status") != "OPEN":
                continue
            row = by_symbol.get(p["symbol"])
            if row:
                price = float(row.get("last_price") or 0)
                p["underlying_ltp"] = price
                long_side = p["underlying_direction"] == "LONG"
                target_hit = price >= p["underlying_target"] if long_side else price <= p["underlying_target"]
                stop_hit = price <= p["underlying_stop"] if long_side else price >= p["underlying_stop"]
                if target_hit:
                    p["exit_requested"] = True
                    p["exit_reason"] = "UNDERLYING_TARGET"
                elif stop_hit:
                    p["exit_requested"] = True
                    p["exit_reason"] = "UNDERLYING_STOP_LOSS"
                if p.get("exit_requested"):
                    quote = self.latest_option_quotes.get(int(p["token"]))
                    if quote and (now - quote["received_at"]).total_seconds() <= 5 and quote["bid"] > 0:
                        self._close(p, quote["bid"], p.get("exit_reason") or "MANUAL_OR_TIME_EXIT", now)
                    else:
                        p["quote_stale"] = True
                        self._persist_position(p)

    def _on_option_tick(self, row, now):
        token = int(row["instrument_token"])
        depth = row.get("depth") or {}
        bids, asks = depth.get("buy") or [], depth.get("sell") or []
        bid = float(bids[0].get("price") or 0) if bids else 0
        ask = float(asks[0].get("price") or 0) if asks else 0
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        self.latest_option_quotes[token] = {"bid": bid, "ask": ask, "received_at": now}
        pending = self.pending.get(token)
        if pending:
            if pending["lot_size"] <= 0:
                self.pending.pop(token, None)
                return
            underlying_direction = self.selection["direction"]
            target, stop = self._equity_levels(pending["price"], underlying_direction,
                self.selection.get("nifty_change_percent", 0))
            p = {"id": str(uuid4()), "strategy": "BREAKFAST", "symbol": pending["symbol"],
                 "option_symbol": pending["option_symbol"], "direction": pending["direction"],
                 "underlying_direction": underlying_direction, "mode": "PAPER", "status": "OPEN",
                 "token": token, "quantity": pending["lot_size"], "entry_price": ask,
                 "bid": bid, "ask": ask, "ltp": float(row.get("last_price") or ask),
                 "underlying_entry": pending["price"], "underlying_target": target,
                 "underlying_stop": stop, "opened_at": now.isoformat(), "pnl": 0.0,
                 "quote_stale": False}
            self.positions[p["id"]] = p
            self._persist_position(p)
            self.pending.pop(token, None)
            self._event("PAPER_OPTION_ENTRY", symbol=p["symbol"], option=p["option_symbol"],
                        entry=ask, underlying_target=target, underlying_stop=stop)
            return
        p = next((item for item in self.positions.values() if item.get("token") == token and item.get("status") == "OPEN"), None)
        if not p:
            return
        p.update(bid=bid, ask=ask, ltp=float(row.get("last_price") or 0), quote_stale=False,
                 pnl=round((bid - p["entry_price"]) * p["quantity"], 2))
        if p.get("exit_requested"):
            self._close(p, bid, p.get("exit_reason") or "MANUAL_OR_TIME_EXIT", now)

    def _close(self, p, price, reason, now):
        if p.get("status") != "OPEN":
            return
        p.update(status="CLOSED", exit_price=price, exit_reason=reason,
                 closed_at=now.isoformat(), pnl=round((price - p["entry_price"]) * p["quantity"], 2))
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO breakfast_trades VALUES(?,?,?)",
                       (p["id"], now.date().isoformat(), json.dumps(p, default=str)))
            db.execute("DELETE FROM breakfast_open_positions WHERE id=?", (p["id"],))
        self._event("PAPER_OPTION_EXIT", symbol=p["symbol"], reason=reason, pnl=p["pnl"])

    def manual_exit(self, position_id):
        with self.lock:
            p = self.positions.get(position_id)
            if not p or p.get("status") != "OPEN":
                raise RuntimeError("Open position not found.")
            quote = self.latest_option_quotes.get(int(p["token"]))
            now = datetime.now(IST)
            if not quote or (now - quote["received_at"]).total_seconds() > 5 or quote["bid"] <= 0:
                raise RuntimeError("Fresh option bid is unavailable; position remains open.")
            self._close(p, quote["bid"], "MANUAL_EXIT", now)
            return self.snapshot()

    def trades(self, trade_date=None):
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT payload FROM breakfast_trades WHERE trade_date=? ORDER BY rowid DESC", (trade_date,)).fetchall() if trade_date else db.execute("SELECT payload FROM breakfast_trades ORDER BY rowid DESC LIMIT 200").fetchall()
        return [json.loads(r[0]) for r in rows]

    def snapshot(self):
        with self.lock:
            today = datetime.now(IST).date().isoformat()
            open_positions = [p for p in self.positions.values() if p.get("status") == "OPEN"]
            return {"running": self.running, "enabled": self.config["enabled"], "mode": "PAPER",
                "market_data_ready": self.market_data.is_feed_fresh(5),
                "selection": self.selection, "sectors": (self.selection or {}).get("sectors", []),
                "first_candles": self.first_candles, "positions": list(self.positions.values()),
                "open_positions": len(open_positions),
                "open_pnl": round(sum(float(p.get("pnl") or 0) for p in open_positions if p.get("pnl") is not None), 2),
                "trades_count": len(self.trades(today)), "trades": self.trades(today),
                "events": self.events[-100:]}


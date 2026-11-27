"""Paper-only F&O options execution for the independent Price Action strategy."""
import json
import sqlite3
from datetime import datetime, time
from pathlib import Path
from threading import RLock, Timer, Thread
from uuid import uuid4
from zoneinfo import ZoneInfo

from .price_action_engine import PriceActionEngine

IST = ZoneInfo("Asia/Kolkata")


class PriceActionService:
    DEFAULTS = {
        "enabled": True, "auto_start": True, "mode": "PAPER",
        "max_positions": 3, "max_trades_per_day": 5,
        "stop_loss_points": 15.0, "risk_reward": 2.0,
        "max_spread_percent": 1.0, "volume_filter": False,
        "volume_multiplier": 1.2, "exit_time": "15:15:00",
        "max_daily_loss": 1500.0, "gainers_count": 20, "losers_count": 20,
    }

    def __init__(self, market_data):
        self.market_data = market_data
        self.lock = RLock()
        self.db_path = Path("./data/price_action_trades.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS price_action_trades (id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS price_action_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        with sqlite3.connect(self.db_path) as db:
            saved = {k: json.loads(v) for k, v in db.execute("SELECT key,value FROM price_action_settings")}
        self.config = {**self.DEFAULTS, **saved}
        self.engine = PriceActionEngine(self.config)
        self.running = False
        self.positions = {}
        self.pending = {}
        self.option_meta = {}
        self.latest_option_quotes = {}
        self.events = []
        self.started_day = None
        self._squareoff_timer = None
        self._watchdog_timer = None
        self._attempted_day = None
        self.allowed_symbols = set()
        self._feed_stale_notified = False
        self._stale_option_notified = set()

    def save_settings(self, values):
        with self.lock:
            if self.running:
                raise RuntimeError("Stop Price Action Intraday before changing settings.")
            self.config.update(values)
            self.engine.config = self.config
            with sqlite3.connect(self.db_path) as db:
                db.executemany("INSERT INTO price_action_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [(k,json.dumps(v)) for k,v in values.items()])
            return self.settings()

    def settings(self):
        return {"config": dict(self.config), "live_ready": False, "mode": "PAPER", "live_block_reason": "This strategy supports PAPER simulation only. Broker order placement is not implemented."}

    def start(self):
        with self.lock:
            if not self.config["enabled"]:
                raise RuntimeError("Price Action Intraday is disabled.")
            if self.running:
                raise RuntimeError("Price Action Intraday is already running.")
            now = datetime.now(IST)
            exit_time = time.fromisoformat(self.config["exit_time"])
            if now.time() < time(9, 20) or now.time() >= exit_time:
                raise RuntimeError("Start is allowed between 09:20 IST and configured exit time.")
            if not self.market_data.is_feed_fresh(5):
                raise RuntimeError("Zerodha feed is not fresh.")
            self.running = True
            self.started_day = now.date().isoformat()
            self.engine.reset()
            snapshot = self.market_data.snapshot()
            fno_symbols = {str(symbol).upper() for symbol in self.market_data.universe_symbols()}
            eligible = [row for row in snapshot.get("all_equity", []) if str(row.get("symbol") or "").upper() in fno_symbols and row.get("instrument_type") == "EQ" and float(row.get("last_price") or 0) > 0]
            gainers = sorted([row for row in eligible if float(row.get("change_percent") or 0) > 0], key=lambda row: float(row.get("change_percent") or 0), reverse=True)[:int(self.config["gainers_count"])]
            losers = sorted([row for row in eligible if float(row.get("change_percent") or 0) < 0], key=lambda row: float(row.get("change_percent") or 0))[:int(self.config["losers_count"])]
            self.allowed_symbols = {str(row["symbol"]).upper() for row in gainers + losers}
            history_start = datetime.combine(now.date(), time(9, 15))
            history_end = now.replace(tzinfo=None, second=0, microsecond=0)
            historical = self.market_data.historical_minute_candles(
                self.allowed_symbols, history_start, history_end
            )
            seeded = 0
            for symbol in self.allowed_symbols:
                seeded += self.engine.seed_history(symbol, historical.get(symbol, []))
            self.events = [{
                "time": now.isoformat(), "event": "HISTORICAL_BASELINE_LOADED",
                "detail": f"{seeded} completed Zerodha one-minute candles loaded across {len(self.allowed_symbols)} stocks"
            }]
            self.positions.clear()
            self.pending.clear()
            self.option_meta.clear()
            self.latest_option_quotes.clear()
            self.events.append({"time": now.isoformat(), "event": "PAPER_STARTED", "detail": "Price Action Intraday started using historical baseline and live Zerodha ticks"})
            seconds = max(0, (datetime.combine(now.date(), exit_time, tzinfo=IST)-now).total_seconds())
            self._squareoff_timer = Timer(seconds, self.stop)
            self._squareoff_timer.daemon = True
            self._squareoff_timer.start()
            self._schedule_watchdog()
            return self.snapshot()

    def stop(self):
        with self.lock:
            now = datetime.now(IST)
            for token, item in list(self.pending.items()):
                self.pending.pop(token, None)
                self.events.append({"time": now.isoformat(), "event": "PENDING_CANCELLED", "symbol": item["symbol"], "reason": "STRATEGY_STOP"})
            for pos in list(self.positions.values()):
                if pos["status"] == "OPEN":
                    quote = self.latest_option_quotes.get(pos["token"])
                    if quote and (now - quote["received_at"]).total_seconds() <= 5:
                        self._close(pos, quote.get("bid"), "MANUAL_OR_TIME_EXIT", now)
                    else:
                        self._close(pos, None, "EXIT_UNPRICED", now)
            self.running = False
            if self._squareoff_timer:
                self._squareoff_timer.cancel()
                self._squareoff_timer = None
            if self._watchdog_timer:
                self._watchdog_timer.cancel()
                self._watchdog_timer = None
            self.events.append({"time": now.isoformat(), "event": "PAPER_STOPPED"})
            return self.snapshot()

    def _schedule_watchdog(self):
        if not self.running or self._watchdog_timer is not None:
            return
        timer = Timer(1, self._watchdog_tick)
        timer.daemon = True
        self._watchdog_timer = timer
        timer.start()

    def _watchdog_tick(self):
        with self.lock:
            if not self.running:
                self._watchdog_timer = None
                return
            now = datetime.now(IST)
            if not self.market_data.is_feed_fresh(5):
                if not self._feed_stale_notified:
                    self.events.append({"time": now.isoformat(), "event": "FEED_STALE_PAUSED", "reason": "Waiting for fresh Zerodha ticks; open PAPER positions are retained"})
                    self._feed_stale_notified = True
                self._watchdog_timer = None
                self._schedule_watchdog()
                return
            self._feed_stale_notified = False
            for token, pending in list(self.pending.items()):
                requested = datetime.fromisoformat(pending["requested_at"])
                if (now - requested).total_seconds() > 5:
                    self.pending.pop(token, None)
                    self.events.append({"time": now.isoformat(), "event": "PENDING_CANCELLED", "symbol": pending["symbol"], "reason": "OPTION_QUOTE_TIMEOUT"})
            for pos in list(self.positions.values()):
                if pos["status"] != "OPEN":
                    continue
                quote = self.latest_option_quotes.get(pos["token"])
                if not quote or (now - quote["received_at"]).total_seconds() > 5:
                    pos["quote_stale"] = True
                    if pos["token"] not in self._stale_option_notified:
                        self.events.append({"time": now.isoformat(), "event": "OPTION_QUOTE_STALE", "reason": "Waiting for next valid option quote; strategy remains active", "symbol": pos["symbol"]})
                        self._stale_option_notified.add(pos["token"])
                else:
                    pos["quote_stale"] = False
            self._watchdog_timer = None
            self._schedule_watchdog()

    def on_market_tick(self, rows):
        now = datetime.now(IST)
        with self.lock:
            if not self.config["enabled"]:
                return
            if not self.running and self.config["auto_start"] and self.config["mode"] == "PAPER" and time(9,20) <= now.time() < time.fromisoformat(self.config["exit_time"]) and self._attempted_day != now.date().isoformat():
                self._attempted_day = now.date().isoformat()
                Thread(target=self._auto_start_worker, daemon=True, name="price-action-auto-start").start()
            if not self.running:
                return
            if not self.market_data.is_feed_fresh(5) or now.date().isoformat() != self.started_day:
                return
            for row in rows:
                kind = row.get("instrument_type")
                if kind in ("CE","PE"):
                    self._on_option_tick(row, now)
                elif kind == "EQ" and str(row.get("symbol") or "").upper() in self.allowed_symbols:
                    signal = self.engine.on_tick(row, now)
                    if signal:
                        self._queue_option(signal, now)

    def _auto_start_worker(self):
        try:
            self.start()
        except RuntimeError as exc:
            with self.lock:
                self.events.append({"time": datetime.now(IST).isoformat(), "event": "AUTO_START_FAILED", "reason": str(exc)})
                self._attempted_day = None

    def _queue_option(self, signal, now):
        open_count = sum(p["status"]=="OPEN" for p in self.positions.values()) + len(self.pending)
        trades_today = len(self.trades(now.date().isoformat()))
        if open_count >= int(self.config["max_positions"]) or trades_today + len(self.pending) >= int(self.config["max_trades_per_day"]):
            self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":signal["symbol"],"reason":"Position or daily trade limit reached"})
            return
        realized_today = sum(float(trade.get("pnl") or 0) for trade in self.trades(now.date().isoformat()))
        if realized_today <= -abs(float(self.config["max_daily_loss"])):
            self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":signal["symbol"],"reason":"Maximum daily loss reached"})
            return
        options = [r for r in self.market_data.option_instruments(signal["symbol"]) if r.get("instrument_type")==signal["direction"] and r.get("expiry") and r["expiry"] >= now.date().isoformat()]
        if not options:
            self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":signal["symbol"],"reason":"No current option contract"})
            return
        expiry = min(r["expiry"] for r in options)
        chain = [r for r in options if r["expiry"]==expiry]
        contract = min(chain,key=lambda r:abs(float(r.get("strike") or 0)-signal["underlying_price"]))
        token = int(contract["instrument_token"])
        if not self.market_data.subscribe_extra([token], full=True):
            self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":signal["symbol"],"reason":"Option subscription failed"})
            return
        self.pending[token] = {**signal,"token":token,"option_symbol":contract["tradingsymbol"],"lot_size":int(contract.get("lot_size") or 0),"requested_at":now.isoformat()}
        self.option_meta[token] = contract
        self.events.append({"time":now.isoformat(),"event":"PRICE_ACTION_SIGNAL","symbol":signal["symbol"],"direction":signal["direction"],"reasons":signal["reasons"],"option":contract["tradingsymbol"]})

    def _on_option_tick(self, row, now):
        token = int(row["instrument_token"])
        item = self.pending.get(token)
        depth = row.get("depth") or {}
        bids, asks = depth.get("buy") or [], depth.get("sell") or []
        bid = float(bids[0].get("price") or 0) if bids else 0
        ask = float(asks[0].get("price") or 0) if asks else 0
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        self.latest_option_quotes[token] = {"bid": bid, "ask": ask, "received_at": now}
        spread = (ask-bid)/((ask+bid)/2)*100
        if spread > float(self.config["max_spread_percent"]):
            return
        if item:
            qty = int(item["lot_size"])
            if qty <= 0:
                self.pending.pop(token,None); return
            entry = ask
            sl = entry-float(self.config["stop_loss_points"])
            target = entry+float(self.config["stop_loss_points"])*float(self.config["risk_reward"])
            pos = {"id":str(uuid4()),"strategy":"PRICE_ACTION_INTRADAY","symbol":item["symbol"],"option_symbol":item["option_symbol"],"direction":item["direction"],"mode":"PAPER","status":"OPEN","token":token,"quantity":qty,"entry_price":entry,"stop_price":sl,"target_price":target,"ltp":float(row.get("last_price") or ask),"bid":bid,"ask":ask,"opened_at":now.isoformat(),"signal_reasons":item["reasons"],"pnl":0.0}
            self.positions[pos["id"]]=pos
            self.pending.pop(token,None)
            self.events.append({"time":now.isoformat(),"event":"PAPER_ENTRY","symbol":item["symbol"],"option":item["option_symbol"],"entry":entry,"stop":sl,"target":target,"quantity":qty})
            return
        pos = next((p for p in self.positions.values() if p["token"]==token and p["status"]=="OPEN"),None)
        if not pos:return
        pos.update(ltp=float(row.get("last_price") or 0),bid=bid,ask=ask,pnl=round((bid-pos["entry_price"])*pos["quantity"],2),quote_stale=False)
        self._stale_option_notified.discard(token)
        if bid <= pos["stop_price"]: self._close(pos,bid,"STOP_LOSS",now)
        elif bid >= pos["target_price"]: self._close(pos,bid,"TARGET",now)

    def _close(self,pos,price,reason,now):
        if pos["status"]!="OPEN":return
        pos.update(status="CLOSED" if price is not None else "CLOSED_UNPRICED",exit_price=price,exit_reason=reason,closed_at=now.isoformat(),pnl=round((price-pos["entry_price"])*pos["quantity"],2) if price is not None else None)
        trade={**pos,"trade_date":now.date().isoformat()}
        with sqlite3.connect(self.db_path) as db:db.execute("INSERT OR REPLACE INTO price_action_trades VALUES(?,?,?)",(pos["id"],trade["trade_date"],json.dumps(trade,default=str)))
        self.events.append({"time":now.isoformat(),"event":"PAPER_EXIT","symbol":pos["symbol"],"reason":reason,"pnl":pos["pnl"]})

    def trades(self, trade_date=None):
        with sqlite3.connect(self.db_path) as db:
            if trade_date: rows=db.execute("SELECT payload FROM price_action_trades WHERE trade_date=? ORDER BY rowid DESC",(trade_date,)).fetchall()
            else: rows=db.execute("SELECT payload FROM price_action_trades ORDER BY rowid DESC LIMIT 200").fetchall()
        return [json.loads(r[0]) for r in rows]

    def snapshot(self):
        return {"running":self.running,"mode":"PAPER","enabled":self.config["enabled"],"market_data_ready":self.market_data.is_feed_fresh(5),"watchlist":self.engine.snapshot(),"positions":list(self.positions.values()),"open_positions":sum(p["status"]=="OPEN" for p in self.positions.values()),"trades_count":len(self.trades(datetime.now(IST).date().isoformat())),"trades":self.trades(datetime.now(IST).date().isoformat()),"events":self.events[-100:]}

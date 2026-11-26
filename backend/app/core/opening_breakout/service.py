"""Paper-mode Opening Breakout service. Broker execution is deliberately fail-closed."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from threading import RLock
from uuid import uuid4
from app.core.market_data import market_data_engine
from .engine import OpeningBreakoutEngine
from .models import BreakoutConfig
from .store import OpeningBreakoutStore

IST=ZoneInfo("Asia/Kolkata")

class OpeningBreakoutService:
    def __init__(self):
        self.lock=RLock(); self.config=BreakoutConfig(); self.engine=OpeningBreakoutEngine(self.config)
        self.store=OpeningBreakoutStore(); self.running=False; self.mode=None; self.events=[]
        self.positions={}; self.started_at=None; self.option_instruments={}
        self._traded_symbols=set(); self._day=None

    def configure(self, values):
        with self.lock:
            for key,value in values.items():
                if hasattr(self.config,key): setattr(self.config,key,value)
            self.engine.config=self.config
            return self.snapshot()

    def start(self, mode, access_token):
        if mode != "PAPER": raise RuntimeError("Live order routing is not enabled.")
        with self.lock:
            snapshot=market_data_engine.snapshot()
            if snapshot["status"] != "connected" or not snapshot["last_tick_received_at"]:
                raise RuntimeError("Kite feed is not connected or fresh ticks are unavailable.")
            fno=set(market_data_engine.universe_symbols())
            eligible=[r for r in snapshot["all_equity"] if r["symbol"].upper() in fno and r["instrument_type"]=="EQ" and r["open"]>0 and r["close"]>0]
            gainers=sorted([r for r in eligible if r["change_percent"]>0],key=lambda r:r["change_percent"],reverse=True)[:3]
            losers=sorted([r for r in eligible if r["change_percent"]<0],key=lambda r:r["change_percent"])[:3]
            self.engine.set_universe(gainers,losers)
            self.positions={}; self._traded_symbols=set(); self.mode="PAPER"; self.running=True
            self.started_at=datetime.now(IST).isoformat(); self._day=datetime.now(IST).date().isoformat()
            self.events=[{"time":self.started_at,"event":"PAPER_STARTED","detail":f"{len(self.engine.stocks)} F&O stocks loaded"}]
            return self.snapshot()

    def on_market_tick(self, rows):
        now=datetime.now(IST)
        with self.lock:
            if not self.running: return
            if now.date().isoformat()!=self._day: self.running=False; return
            for row in rows:
                if row.get("instrument_type")!="EQ": continue
                symbol=row["symbol"].upper()
                if symbol in self._traded_symbols: continue
                signal=self.engine.on_tick(row,now)
                if not signal: continue
                self._traded_symbols.add(symbol); self.engine.mark_traded(symbol)
                self.events.append({"time":now.isoformat(),"event":"SIGNAL","symbol":symbol,"direction":signal,
                                    "status":"WAITING_OPTION_MARKET_DATA",
                                    "detail":"Underlying breakout detected; option quote/contract subscription is not yet connected."})
            # Paper positions are marked from option ticks once option subscriptions exist.
            for row in rows:
                if row.get("instrument_type") not in ("CE","PE"): continue
                for pos in self.positions.values():
                    if pos["status"]=="OPEN" and pos["option_token"]==row.get("instrument_token"):
                        pos["ltp"]=float(row.get("last_price") or 0)
                        if pos["ltp"]>0 and pos["ltp"]<=pos["stop_price"]:
                            self._close_paper(pos, pos["ltp"], "STOP_LOSS", now)

    def _close_paper(self, pos, price, reason, now):
        pos.update(status="CLOSED",exit_price=price,exit_reason=reason,closed_at=now.isoformat())
        pos["pnl"]=(price-pos["entry_price"])*pos["quantity"]
        self.store.save_trade({"id":pos["id"],"trade_date":now.date().isoformat(),"symbol":pos["symbol"],**pos})

    def stop(self):
        with self.lock:
            self.running=False
            self.events.append({"time":datetime.now(IST).isoformat(),"event":"STOPPED"})
            return self.snapshot()

    def snapshot(self):
        snap=market_data_engine.snapshot()
        return {"running":self.running,"mode":self.mode,"market_data_ready":snap["status"]=="connected" and bool(snap["last_tick_received_at"]),
                "watchlist_count":len(self.engine.stocks),"open_positions":sum(p["status"]=="OPEN" for p in self.positions.values()),
                "trades_count":len(self.store.trades(datetime.now(IST).date().isoformat())),
                "watchlist":self.engine.snapshot(),"positions":list(self.positions.values()),
                "events":self.events[-100:],"market_status":snap["status"],"started_at":self.started_at,
                "live_ready":False,"live_block_reason":"Protective SL/partial-fill/exit reconciliation not yet verified."}

    def trades(self): return self.store.trades()

opening_breakout_service=OpeningBreakoutService()

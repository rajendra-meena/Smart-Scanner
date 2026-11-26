"""Strategy orchestration. Live mode remains fail-closed until protective order lifecycle is verified."""
from datetime import datetime
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
        self.positions={}; self.paper_ticks={}; self.started_at=None; self.last_scan_date=None

    def configure(self, values):
        with self.lock:
            for key,value in values.items():
                if hasattr(self.config,key): setattr(self.config,key,value)
            return self.snapshot()

    def start(self, mode, access_token):
        if mode == "LIVE": raise RuntimeError("Live mode is safety locked pending verified SL/order reconciliation.")
        with self.lock:
            snapshot=market_data_engine.snapshot()
            if snapshot["status"] != "connected": raise RuntimeError("Kite live market feed is not connected.")
            if not snapshot["last_tick_received_at"]: raise RuntimeError("No fresh market tick received.")
            equities=snapshot["all_equity"]
            fno=set(market_data_engine.universe_symbols())
            eligible=[r for r in equities if r["symbol"].upper() in fno and r["instrument_type"]=="EQ" and r["open"]>0 and r["close"]>0]
            gainers=sorted([r for r in eligible if r["change_percent"]>0],key=lambda r:r["change_percent"],reverse=True)[:3]
            losers=sorted([r for r in eligible if r["change_percent"]<0],key=lambda r:r["change_percent"])[:3]
            self.engine.set_universe(gainers,losers)
            self.running=True; self.mode="PAPER"; self.started_at=datetime.now(IST).isoformat()
            self.events=[{"time":self.started_at,"event":"PAPER_STARTED","detail":f"{len(self.engine.stocks)} F&O stocks loaded"}]
            self.last_scan_date=datetime.now(IST).date().isoformat()
            return self.snapshot()

    def on_market_tick(self, rows):
        now=datetime.now(IST)
        with self.lock:
            if not self.running: return
            for row in rows:
                if row.get("instrument_type")!="EQ": continue
                signal=self.engine.on_tick(row,now)
                if not signal: continue
                symbol=row["symbol"].upper()
                if len([p for p in self.positions.values() if p["status"]=="OPEN"])>=self.config.max_positions: continue
                paper=self.paper_ticks.get(symbol)
                if not paper:
                    self.engine.mark_traded(symbol)
                    self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":symbol,"reason":"Option quote subscription/executable paper quote not configured yet"})
                    continue
                self.engine.mark_traded(symbol)

    def stop(self):
        with self.lock: self.running=False; self.events.append({"time":datetime.now(IST).isoformat(),"event":"STOPPED"}); return self.snapshot()

    def snapshot(self):
        snap=market_data_engine.snapshot()
        return {"running":self.running,"mode":self.mode,"market_data_ready":snap["status"]=="connected" and bool(snap["last_tick_received_at"]),
                "watchlist_count":len(self.engine.stocks),"open_positions":sum(p["status"]=="OPEN" for p in self.positions.values()),
                "trades_count":len(self.store.trades(datetime.now(IST).date().isoformat())),"watchlist":self.engine.snapshot(),
                "events":self.events[-100:],"market_status":snap["status"],"started_at":self.started_at}

    def trades(self): return self.store.trades()

opening_breakout_service=OpeningBreakoutService()

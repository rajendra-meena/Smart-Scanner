"""Strategy orchestration. Paper engine is tick driven; Live stays fail-closed."""
from datetime import datetime,time
from zoneinfo import ZoneInfo
from threading import RLock, Timer
from uuid import uuid4
from .engine import OpeningBreakoutEngine
from .models import BreakoutConfig
from .store import OpeningBreakoutStore

IST=ZoneInfo("Asia/Kolkata")

class OpeningBreakoutService:
    def __init__(self, market_data=None):
        self.market_data = market_data
        self.lock=RLock();self.config=BreakoutConfig();self.engine=OpeningBreakoutEngine(self.config)
        self.store=OpeningBreakoutStore();self.running=False;self.mode=None;self.events=[]
        self.positions={};self.started_at=None;self._traded_symbols=set();self._day=None
        self._instrument_rows=[];self._option_token_symbols={};self._pending_contracts={};self._last_option_ticks={};self._exit_timer=None

    def configure(self,values):
        with self.lock:
            for key,value in values.items():
                if hasattr(self.config,key):setattr(self.config,key,value)
            self.engine.config=self.config
            return self.snapshot()

    def _load_universe(self,snapshot):
        fno=set(self.market_data.universe_symbols())
        eligible=[r for r in snapshot["all_equity"] if r["symbol"].upper() in fno and r["instrument_type"]=="EQ" and r["open"]>0 and r["close"]>0]
        gainers=sorted([r for r in eligible if r["change_percent"]>0],key=lambda r:r["change_percent"],reverse=True)[:3]
        losers=sorted([r for r in eligible if r["change_percent"]<0],key=lambda r:r["change_percent"])[:3]
        self.engine.set_universe(gainers,losers)

    def start(self,mode,access_token):
        if mode!="PAPER":raise RuntimeError("Live order routing is not enabled.")
        now=datetime.now(IST)
        if now.time()<time(9,15,30) or now.time()>=time(9,30):
            raise RuntimeError("Opening Breakout can only be started between 09:15:30 and 09:30 IST.")
        with self.lock:
            snap=self.market_data.snapshot()
            if not self.market_data.is_feed_fresh(5):raise RuntimeError("Kite feed is not connected or fresh ticks unavailable.")
            self._load_universe(snap)
            self.positions={};self._pending_contracts={};self._last_option_ticks={};self._option_token_symbols={};self._traded_symbols=set();self.mode=mode;self.running=True
            self.started_at=datetime.now(IST).isoformat();self._day=datetime.now(IST).date().isoformat()
            seconds=max(0,(datetime.combine(datetime.now(IST).date(),time(9,30),tzinfo=IST)-datetime.now(IST)).total_seconds())
            self._exit_timer=Timer(seconds,self._time_exit)
            self._exit_timer.daemon=True;self._exit_timer.start()
            self._instrument_rows=self.market_data.option_instruments()
            self.events=[{"time":self.started_at,"event":"PAPER_STARTED","detail":f"{len(self.engine.stocks)} F&O stocks loaded"}]
            return self.snapshot()

    def _select_atm(self,underlying,direction):
        now=datetime.now(IST).date()
        options=[r for r in self._instrument_rows if str(r.get("name") or "").upper()==underlying.upper()
                 and r.get("instrument_type")==direction and r.get("expiry") and r["expiry"]>=now.isoformat()
                 and float(r.get("strike") or 0)>0]
        if not options:return None
        expiry=min(r["expiry"] for r in options)
        chain=[r for r in options if r["expiry"]==expiry]
        spot=next((s.last_price for s in self.engine.stocks.values() if s.symbol==underlying),None)
        if not spot:return None
        return min(chain,key=lambda r:(abs(float(r["strike"])-spot),float(r["strike"])))

    def _liquidity_prices(self,row):
        depth=row.get("depth") or {};buy=depth.get("buy") or [];sell=depth.get("sell") or []
        bid=next((float(x.get("price") or 0) for x in buy if x.get("price")),0)
        ask=next((float(x.get("price") or 0) for x in sell if x.get("price")),0)
        if bid<=0 or ask<=0 or ask<bid:return None
        spread=(ask-bid)/((ask+bid)/2)*100
        if spread>self.config.max_spread_percent:return None
        return bid,ask

    def on_market_tick(self,rows):
        now=datetime.now(IST)
        if not self.market_data.is_feed_fresh(5):
            with self.lock:
                if self.running:
                    self.running=False
                    self.events.append({"time":now.isoformat(),"event":"FEED_GUARD_STOP","reason":"Market data connection is not healthy"})
            return
        with self.lock:
            if not self.running:return
            if now.date().isoformat()!=self._day:self.running=False;return
            if now.time()>=time(9,30):
                self._square_off_paper(rows,now);self.running=False
                if self._exit_timer:self._exit_timer.cancel();self._exit_timer=None
                return
            for row in rows:
                typ=row.get("instrument_type")
                if typ in ("CE","PE"):
                    self._handle_option_tick(row,now);continue
                if typ!="EQ":continue
                symbol=str(row.get("symbol") or "").upper()
                if symbol in self._traded_symbols:continue
                signal=self.engine.on_tick(row,now)
                if not signal:continue
                if len([p for p in self.positions.values() if p["status"]=="OPEN"])+len(self._pending_contracts) >= self.config.max_positions:
                    self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":symbol,"reason":"Maximum open positions reached"});continue
                self._traded_symbols.add(symbol);self.engine.mark_traded(symbol)
                contract=self._select_atm(symbol,signal)
                if not contract:
                    self.events.append({"time":now.isoformat(),"event":"SIGNAL_SKIPPED","symbol":symbol,"reason":"No valid nearest ATM option in current instrument master"});continue
                token=int(contract["instrument_token"])
                self._option_token_symbols[token]=contract["tradingsymbol"]
                self._pending_contracts[token]={"id":str(uuid4()),"symbol":symbol,"option_symbol":contract["tradingsymbol"],"option_token":token,"direction":signal,"lot_size":int(contract.get("lot_size") or 0),"mode":"PAPER","status":"WAITING_QUOTE"}
                self.market_data.subscribe_extra([token],full=True)
                self.events.append({"time":now.isoformat(),"event":"SIGNAL","symbol":symbol,"direction":signal,
                                    "option_symbol":contract["tradingsymbol"],"status":"WAITING_OPTION_TICK",
                                    "detail":"ATM option subscribed; waiting for fresh full-depth option tick."})

    def _time_exit(self):
        now=datetime.now(IST)
        with self.lock:
            if not self.running:return
            for pending in self._pending_contracts.values():
                pending["status"]="CANCELLED_TIME_EXIT"
            self._pending_contracts.clear()
            for pos in list(self.positions.values()):
                if pos["status"]=="OPEN":
                    tick=self._last_option_ticks.get(int(pos["option_token"]),{})
                    depth=tick.get("depth") or {}; bids=depth.get("buy") or []
                    price=float((bids[0].get("price") if bids else 0) or pos.get("bid") or tick.get("last_price") or pos["entry_price"])
                    self._close_paper(pos,price,"TIME_EXIT_09_30",now)
            self.running=False
            self.events.append({"time":now.isoformat(),"event":"STRATEGY_TIME_EXIT"})

    def _handle_option_tick(self,row,now):
        symbol=self._option_token_symbols.get(int(row.get("instrument_token") or 0))
        if not symbol:return
        self._last_option_ticks[int(row["instrument_token"])]=row
        pending=self._pending_contracts.get(int(row["instrument_token"]))
        ltp=float(row.get("last_price") or 0)
        if ltp<=0:return
        if pending:
            prices=self._liquidity_prices(row)
            if not prices:return
            bid,ask=prices
            pending.update(status="OPEN",entry_price=ask,stop_price=round(ask-self.config.stop_loss_points,2),
                           ltp=ltp,bid=bid,ask=ask,quantity=int(pending["lot_size"]),opened_at=now.isoformat())
            self.positions[pending["id"]]=pending
            self._pending_contracts.pop(int(row["instrument_token"]),None)
            self.events.append({"time":now.isoformat(),"event":"PAPER_ENTRY","symbol":pending["symbol"],"option_symbol":symbol,"price":ltp})
            return
        for pos in self.positions.values():
            if pos["option_token"]==row["instrument_token"] and pos["status"]=="OPEN":
                pos["ltp"]=ltp
                prices=self._liquidity_prices(row)
                if prices:
                    bid,ask=prices;pos["bid"]=bid;pos["ask"]=ask
                if ltp<=pos["stop_price"]:
                    exit_price=(prices[0] if prices else ltp)
                    self._close_paper(pos,exit_price,"STOP_LOSS",now)

    def _close_paper(self,pos,price,reason,now):
        pos.update(status="CLOSED",exit_price=price,exit_reason=reason,closed_at=now.isoformat())
        pos["pnl"]=round((price-pos["entry_price"])*pos["quantity"],2)
        self.store.save_trade({"id":pos["id"],"trade_date":now.date().isoformat(),"symbol":pos["symbol"],**pos})
        self.events.append({"time":now.isoformat(),"event":"PAPER_EXIT","symbol":pos["symbol"],"reason":reason,"price":price})

    def _square_off_paper(self,rows,now):
        latest={int(r.get("instrument_token") or 0):float(r.get("last_price") or 0) for r in rows}
        latest.update({token:float(tick.get("last_price") or 0) for token,tick in self._last_option_ticks.items()})
        for pos in self.positions.values():
            if pos["status"]=="OPEN":
                tick=self._last_option_ticks.get(int(pos["option_token"]),{})
                depth=tick.get("depth") or {}; bids=depth.get("buy") or []
                price=float((bids[0].get("price") if bids else 0) or pos.get("bid") or latest.get(pos["option_token"]) or pos.get("ltp") or pos["entry_price"])
                self._close_paper(pos,price,"TIME_EXIT_09_30",now)

    def stop(self):
        with self.lock:
            self.running=False
            if self._exit_timer:
                self._exit_timer.cancel();self._exit_timer=None
            self.events.append({"time":datetime.now(IST).isoformat(),"event":"STOPPED"})
            return self.snapshot()

    def snapshot(self):
        snap=self.market_data.snapshot()
        return {"running":self.running,"mode":self.mode,"market_data_ready":self.market_data.is_feed_fresh(5),
                "watchlist_count":len(self.engine.stocks),"open_positions":sum(p["status"]=="OPEN" for p in self.positions.values())+sum(1 for p in self._pending_contracts.values() if p.get("status")=="WAITING_QUOTE"),
                "trades_count":len(self.store.trades(datetime.now(IST).date().isoformat())),"watchlist":self.engine.snapshot(),
                "positions":list(self.positions.values())+[p for p in self._pending_contracts.values() if p.get("status")=="WAITING_QUOTE"],"events":self.events[-100:],"market_status":snap["status"],
                "started_at":self.started_at,"live_ready":False,"live_block_reason":"Protective stop order lifecycle, partial-fill recovery and broker reconciliation require verification."}

    def trades(self):return self.store.trades()

opening_breakout_service=OpeningBreakoutService()

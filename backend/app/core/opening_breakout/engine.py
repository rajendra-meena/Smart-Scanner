"""Tick-driven opening-price reversal breakout detector."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from .models import BreakoutConfig, Direction, StockState

IST=ZoneInfo("Asia/Kolkata")

class OpeningBreakoutEngine:
    def __init__(self, config: BreakoutConfig | None = None):
        self.config=config or BreakoutConfig()
        self.stocks: dict[str,StockState]={}

    def set_universe(self,gainers:list[dict],losers:list[dict])->list[StockState]:
        selected=gainers[:self.config.gainers_count]+losers[:self.config.losers_count]
        unique={}
        for row in selected:
            symbol=str(row.get("symbol") or "").upper()
            if symbol and row.get("instrument_type")=="EQ" and symbol not in unique:
                unique[symbol]=StockState(symbol,int(row["instrument_token"]),float(row["close"]))
        self.stocks=unique
        return list(unique.values())

    def on_tick(self,row:dict,now:datetime)->Direction|None:
        symbol=str(row.get("symbol") or "").upper()
        state=self.stocks.get(symbol)
        if not state or state.traded or state.skipped:return None
        local=now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        if local.time()<time(9,15,30) or local.time()>=time(9,30):return None
        price=float(row.get("last_price") or 0)
        day_open=float(row.get("open") or 0)
        if price<=0 or day_open<=0:return None
        if state.opening_price is None:state.opening_price=day_open
        # Keep the opening reference immutable for the trading day.
        if abs(day_open-state.opening_price)>1e-8:return None
        if price>state.opening_price:state.excursion_up=True
        if price<state.opening_price:state.excursion_down=True
        prior=state.last_price
        state.last_price=price
        state.high_after_open=max(state.high_after_open or price,price)
        state.low_after_open=min(state.low_after_open or price,price)
        if prior is not None:
            if state.excursion_up and prior>=state.opening_price and price<state.opening_price:
                state.signal="PE";return "PE"
            if state.excursion_down and prior<=state.opening_price and price>state.opening_price:
                state.signal="CE";return "CE"
        return None

    def mark_traded(self,symbol:str):
        if symbol.upper() in self.stocks:self.stocks[symbol.upper()].traded=True

    def snapshot(self):
        return [{name:getattr(s,name) for name in s.__dataclass_fields__} for s in self.stocks.values()]

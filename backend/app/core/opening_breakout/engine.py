"""Tick-driven opening-price reversal breakout detector."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .models import BreakoutConfig, Direction, StockState

IST = ZoneInfo("Asia/Kolkata")


class OpeningBreakoutEngine:
    def __init__(self, config: BreakoutConfig | None = None):
        self.config = config or BreakoutConfig()
        self.stocks: dict[str, StockState] = {}

    def set_universe(self, gainers: list[dict], losers: list[dict]) -> list[StockState]:
        selected = gainers[:self.config.gainers_count] + losers[:self.config.losers_count]
        unique = {}
        for row in selected:
            symbol = str(row.get("symbol") or "").upper()
            if symbol and row.get("instrument_type") == "EQ" and symbol not in unique:
                unique[symbol] = StockState(symbol, int(row["instrument_token"]), float(row["close"]))
        self.stocks = unique
        return list(unique.values())

    def max_entries(self) -> int:
        return 2 if self.config.allow_reentry else 1

    def can_enter(self, symbol: str) -> bool:
        state = self.stocks.get(symbol.upper())
        return bool(
            state and not state.skipped and not state.traded
            and state.entry_count < self.max_entries()
            and not state.pending_entry and state.active_position_id is None
        )

    def reserve_entry(self, symbol: str) -> bool:
        symbol = symbol.upper()
        if not self.can_enter(symbol):
            return False
        state = self.stocks[symbol]
        state.entry_count += 1
        state.pending_entry = True
        state.traded = state.entry_count >= self.max_entries()
        return True

    def release_pending_entry(self, symbol: str) -> None:
        state = self.stocks.get(symbol.upper())
        if not state or not state.pending_entry:
            return
        state.pending_entry = False
        state.entry_count = max(0, state.entry_count - 1)
        state.traded = state.entry_count >= self.max_entries()

    def mark_position_open(self, symbol: str, position_id: str) -> None:
        state = self.stocks.get(symbol.upper())
        if not state:
            return
        state.pending_entry = False
        state.active_position_id = position_id
        state.traded = state.entry_count >= self.max_entries()

    def mark_position_closed(self, symbol: str, position_id: str) -> None:
        state = self.stocks.get(symbol.upper())
        if not state or state.active_position_id != position_id:
            return
        state.active_position_id = None
        state.traded = state.entry_count >= self.max_entries()
        if state.entry_count < self.max_entries():
            state.excursion_up = False
            state.excursion_down = False
            state.last_price = None
            state.signal = None

    def on_tick(self, row: dict, now: datetime) -> Direction | None:
        symbol = str(row.get("symbol") or "").upper()
        state = self.stocks.get(symbol)
        if not state or not self.can_enter(symbol):
            return None
        local = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
        if local.time() < time(9, 15, 30) or local.time() >= time(9, 30):
            return None
        price = float(row.get("last_price") or 0)
        day_open = float(row.get("open") or 0)
        if price <= 0 or day_open <= 0:
            return None
        if state.opening_price is None:
            state.opening_price = day_open
        if abs(day_open - state.opening_price) > 1e-8:
            return None
        if price > state.opening_price:
            state.excursion_up = True
        if price < state.opening_price:
            state.excursion_down = True
        prior = state.last_price
        state.last_price = price
        state.high_after_open = max(state.high_after_open or price, price)
        state.low_after_open = min(state.low_after_open or price, price)
        if prior is not None:
            if state.excursion_up and prior >= state.opening_price and price < state.opening_price:
                state.signal = "PE"
                return "PE"
            if state.excursion_down and prior <= state.opening_price and price > state.opening_price:
                state.signal = "CE"
                return "CE"
        return None

    def mark_traded(self, symbol: str):
        state = self.stocks.get(symbol.upper())
        if state:
            state.traded = True
            state.pending_entry = False
            state.active_position_id = None

    def snapshot(self):
        rows = []
        for state in self.stocks.values():
            row = {
                name: getattr(state, name)
                for name in state.__dataclass_fields__
            }
            if state.signal:
                stage = f"{state.signal}_SIGNAL"
            elif state.opening_price is None:
                stage = "WAITING_FOR_OPEN_TICK"
            elif state.excursion_up and state.excursion_down:
                stage = "MOVED_BOTH_SIDES_WAITING_FOR_CROSS"
            elif state.excursion_up:
                stage = "MOVED_ABOVE_OPEN_WAITING_FOR_DOWN_CROSS"
            elif state.excursion_down:
                stage = "MOVED_BELOW_OPEN_WAITING_FOR_UP_CROSS"
            else:
                stage = "WAITING_FOR_FIRST_OPEN_PRICE_MOVE"
            row["signal_stage"] = stage
            rows.append(row)
        return rows

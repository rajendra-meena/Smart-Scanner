from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Direction = Literal["CE", "PE"]
Mode = Literal["PAPER", "LIVE"]


@dataclass(slots=True)
class BreakoutConfig:
    enabled: bool = False
    start_time: str = "09:15:00"
    end_time: str = "09:30:00"
    initial_wait_seconds: int = 30
    gainers_count: int = 3
    losers_count: int = 3
    max_positions: int = 6
    one_lot: bool = True
    stop_loss_points: float = 15.0
    allow_ce_pe_simultaneously: bool = True
    allow_reentry: bool = True
    require_liquidity: bool = True
    max_spread_percent: float = 1.0
    min_depth_quantity: int = 1


@dataclass(slots=True)
class StockState:
    symbol: str
    instrument_token: int
    previous_close: float
    opening_price: float | None = None
    high_after_open: float | None = None
    low_after_open: float | None = None
    traded: bool = False
    skipped: bool = False
    last_price: float | None = None
    signal: Direction | None = None
    excursion_up: bool = False
    excursion_down: bool = False
    entry_count: int = 0
    pending_entry: bool = False
    active_position_id: str | None = None


@dataclass(slots=True)
class Position:
    id: str
    symbol: str
    option_symbol: str
    option_token: int
    direction: Direction
    quantity: int
    entry_price: float
    stop_price: float
    mode: Mode
    status: str = "OPEN"
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

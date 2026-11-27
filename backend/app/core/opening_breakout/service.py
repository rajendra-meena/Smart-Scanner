
"""Strategy orchestration. Paper engine is tick driven; Live stays fail-closed."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from threading import RLock, Timer
from time import monotonic
from uuid import uuid4

from .engine import OpeningBreakoutEngine
from .models import BreakoutConfig
from .store import OpeningBreakoutStore

IST = ZoneInfo("Asia/Kolkata")


class OpeningBreakoutService:
    def __init__(self, market_data=None):
        self.market_data = market_data
        self.lock = RLock()
        self.config = BreakoutConfig()
        self.engine = OpeningBreakoutEngine(self.config)
        self.store = OpeningBreakoutStore()
        self.selected_mode = "PAPER"
        persisted = self.store.load_settings()
        for key, value in persisted.items():
            if key == "selected_mode" and value in ("PAPER", "LIVE"):
                self.selected_mode = value
            elif hasattr(self.config, key):
                setattr(self.config, key, value)
        self.running = False
        self.mode = None
        self.events = []
        self.positions = {}
        self.started_at = None
        self._day = None
        self._instrument_rows = []
        self._option_token_symbols = {}
        self._pending_contracts = {}
        self._last_option_ticks = {}
        self._last_option_tick_monotonic = {}
        self._pending_quote_started = {}
        self._exit_timer = None
        self._watchdog_timer = None
        self._auto_start_attempted_day = None
        self._auto_start_suppressed_day = None

    PLATFORM_DEFAULTS = {
        "default_mode": "PAPER",
        "auto_start_new_strategies": False,
        "default_max_positions": 6,
        "default_stop_loss_points": 15.0,
        "default_exit_time": "15:15:00",
    }

    def platform_defaults(self):
        saved = self.store.load_settings()
        return {
            key: saved.get(f"platform_{key}", value)
            for key, value in self.PLATFORM_DEFAULTS.items()
        }

    def save_platform_defaults(self, values):
        current = self.platform_defaults()
        current.update(values)
        self.store.save_settings({
            f"platform_{key}": value for key, value in current.items()
        })
        return self.platform_defaults()

    def configure(self, values):
        with self.lock:
            if self.running:
                raise RuntimeError("Stop the strategy before changing its settings.")
            for key, value in values.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
            self.engine.config = self.config
            self.store.save_settings({**values, "selected_mode": self.selected_mode})
            return self.settings()

    def settings(self):
        with self.lock:
            return {
                "mode": self.selected_mode,
                "live_ready": False,
                "live_block_reason": "Live broker order placement, protective SL, partial-fill handling and broker reconciliation are not implemented and verified.",
                "config": {
                    "enabled": self.config.enabled,
                    "auto_start": self.config.auto_start,
                    "gainers_count": self.config.gainers_count,
                    "losers_count": self.config.losers_count,
                    "stop_loss_points": self.config.stop_loss_points,
                    "max_positions": self.config.max_positions,
                    "max_spread_percent": self.config.max_spread_percent,
                    "exit_time": self.config.exit_time,
                },
            }

    def set_mode(self, mode):
        if mode not in ("PAPER", "LIVE"):
            raise ValueError("Mode must be PAPER or LIVE.")
        with self.lock:
            if self.running:
                raise RuntimeError("Stop the strategy before changing execution mode.")
            self.selected_mode = mode
            self.store.save_settings({"selected_mode": mode})
            return self.settings()

    def _exit_clock_time(self):
        try:
            hour, minute, second = (int(part) for part in self.config.exit_time.split(":"))
            return time(hour, minute, second)
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError("Exit time must use HH:MM:SS format.")

    def _load_universe(self, snapshot):
        fno = set(self.market_data.universe_symbols())
        eligible = [
            row for row in snapshot["all_equity"]
            if row["symbol"].upper() in fno and row["instrument_type"] == "EQ"
            and row["open"] > 0 and row["close"] > 0
        ]
        gainers = sorted(
            [row for row in eligible if row["change_percent"] > 0],
            key=lambda row: row["change_percent"], reverse=True,
        )[:self.config.gainers_count]
        losers = sorted(
            [row for row in eligible if row["change_percent"] < 0],
            key=lambda row: row["change_percent"],
        )[:self.config.losers_count]
        self.engine.set_universe(gainers, losers)

    def start(self, mode, access_token):
        if not self.config.enabled:
            raise RuntimeError("Opening Breakout is disabled in Strategy Manager.")
        if mode != "PAPER":
            raise RuntimeError("Live order routing is not enabled.")
        now = datetime.now(IST)
        exit_time = self._exit_clock_time()
        if now.time() < time(9, 15, 30) or now.time() >= exit_time:
            raise RuntimeError(
                f"Opening Breakout can only be started between 09:15:30 and {self.config.exit_time[:5]} IST."
            )
        with self.lock:
            if self.running:
                raise RuntimeError("Opening Breakout is already running.")

            unresolved = [
                trade for trade in self.store.trades()
                if trade.get("mode") == "PAPER" and trade.get("status") == "OPEN"
            ]
            if unresolved:
                symbols = ", ".join(sorted({
                    str(trade.get("symbol") or "UNKNOWN")
                    for trade in unresolved
                }))
                raise RuntimeError(
                    "Opening Breakout has unresolved PAPER position(s) from a "
                    f"previous process ({symbols}). Strategy start is blocked "
                    "until these positions are reconciled; no position will be "
                    "silently discarded."
                )

            snapshot = self.market_data.snapshot()
            if not self.market_data.is_feed_fresh(5):
                raise RuntimeError(
                    "Kite feed is not connected or fresh ticks unavailable."
                )
            self._load_universe(snapshot)
            self.positions = {}
            self._pending_contracts = {}
            self._last_option_ticks = {}
            self._last_option_tick_monotonic = {}
            self._pending_quote_started = {}
            self._option_token_symbols = {}
            self.mode = mode
            self.running = True
            self.started_at = datetime.now(IST).isoformat()
            self._day = datetime.now(IST).date().isoformat()
            seconds = max(0, (
                datetime.combine(
                    datetime.now(IST).date(),
                    exit_time,
                    tzinfo=IST,
                ) - datetime.now(IST)
            ).total_seconds())
            self._exit_timer = Timer(seconds, self._time_exit)
            self._exit_timer.daemon = True
            self._exit_timer.start()
            self._schedule_watchdog()
            self._instrument_rows = self.market_data.option_instruments()
            self.events = [{
                "time": self.started_at,
                "event": "PAPER_STARTED",
                "exit_time": self.config.exit_time,
                "detail": f"{len(self.engine.stocks)} F&O stocks loaded",
            }]
            return self.snapshot()

    def _select_atm(self, underlying, direction):
        today = datetime.now(IST).date()
        options = [
            row for row in self._instrument_rows
            if str(row.get("name") or "").upper() == underlying.upper()
            and row.get("instrument_type") == direction
            and row.get("expiry")
            and row["expiry"] >= today.isoformat()
            and float(row.get("strike") or 0) > 0
        ]
        if not options:
            return None
        expiry = min(row["expiry"] for row in options)
        chain = [row for row in options if row["expiry"] == expiry]
        spot = next(
            (
                state.last_price
                for state in self.engine.stocks.values()
                if state.symbol == underlying
            ),
            None,
        )
        if not spot:
            return None
        return min(
            chain,
            key=lambda row: (
                abs(float(row["strike"]) - spot),
                float(row["strike"]),
            ),
        )

    def _liquidity_prices(self, row):
        depth = row.get("depth") or {}
        buy, sell = depth.get("buy") or [], depth.get("sell") or []
        bid = next(
            (float(item.get("price") or 0) for item in buy if item.get("price")),
            0,
        )
        ask = next(
            (float(item.get("price") or 0) for item in sell if item.get("price")),
            0,
        )
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        spread = (ask - bid) / ((ask + bid) / 2) * 100
        if spread > self.config.max_spread_percent:
            return None
        return bid, ask

    def _cancel_pending_entry(self, token, pending, now, reason):
        pending["status"] = "CANCELLED"
        pending["cancel_reason"] = reason
        self.engine.release_pending_entry(pending["symbol"])
        self.events.append({
            "time": now.isoformat(),
            "event": "PENDING_ENTRY_CANCELLED",
            "symbol": pending["symbol"],
            "option_symbol": pending["option_symbol"],
            "reason": reason,
        })
        self._pending_contracts.pop(token, None)
        self._option_token_symbols.pop(token, None)
        self._pending_quote_started.pop(token, None)

    def _cancel_pending_entries(self, now, reason):
        for token, pending in list(self._pending_contracts.items()):
            self._cancel_pending_entry(token, pending, now, reason)

    def _exit_open_paper_positions(self, now, reason):
        now_monotonic = monotonic()
        for position in list(self.positions.values()):
            if position["status"] != "OPEN":
                continue
            token = int(position["option_token"])
            last_quote = self._last_option_tick_monotonic.get(token)
            if last_quote is None or now_monotonic - last_quote > 5:
                self._record_unpriced_feed_exit(
                    position,
                    now,
                    exit_reason=f"{reason}_UNPRICED",
                )
                continue

            tick = self._last_option_ticks.get(token, {})
            bids = (tick.get("depth") or {}).get("buy") or []
            bid = next(
                (
                    float(level.get("price") or 0)
                    for level in bids
                    if float(level.get("price") or 0) > 0
                ),
                0,
            )
            if bid <= 0:
                self._record_unpriced_feed_exit(
                    position,
                    now,
                    exit_reason=f"{reason}_UNPRICED",
                )
                continue
            self._close_paper(position, bid, reason, now)

    def _record_unpriced_feed_exit(
        self, position, now, exit_reason="FEED_GUARD_EXIT"
    ):
        """Close the paper lifecycle without inventing an exit price."""
        if position["status"] != "OPEN":
            return

        position.update(
            status="CLOSED_UNPRICED",
            exit_price=None,
            exit_reason=exit_reason,
            closed_at=now.isoformat(),
            pnl=None,
        )

        persistence_error = None
        persistence_deferred = False
        try:
            saved = self.store.save_trade({
                "id": position["id"],
                "trade_date": now.date().isoformat(),
                "symbol": position["symbol"],
                **position,
            })
            persistence_deferred = saved is False
        except Exception as exc:
            # A database and recovery-journal failure must not prevent the
            # feed guard from stopping and releasing the in-memory position.
            persistence_error = str(exc)

        self.engine.mark_position_closed(
            position["symbol"],
            position["id"],
        )

        if persistence_deferred:
            self.events.append({
                "time": now.isoformat(),
                "event": "PAPER_EXIT_PERSIST_DEFERRED",
                "symbol": position["symbol"],
                "reason": exit_reason,
                "message": "Trade queued in the durable recovery journal",
            })
        if persistence_error is not None:
            self.events.append({
                "time": now.isoformat(),
                "event": "PAPER_EXIT_PERSIST_FAILED",
                "symbol": position["symbol"],
                "reason": exit_reason,
                "message": persistence_error,
            })

        self.events.append({
            "time": now.isoformat(),
            "event": "PAPER_EXIT_UNPRICED",
            "symbol": position["symbol"],
            "reason": exit_reason,
            "message": "Position closed without a reliable exit quote",
        })

    def _schedule_watchdog(self):
        """Run feed-health checks independently of incoming market ticks."""
        if not self.running or self._watchdog_timer is not None:
            return
        timer = Timer(1, self._watchdog_tick)
        timer.daemon = True
        self._watchdog_timer = timer
        timer.start()

    def _cancel_watchdog(self):
        if self._watchdog_timer:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    def _watchdog_tick(self):
        with self.lock:
            if not self.running:
                self._watchdog_timer = None
                return

            now = datetime.now(IST)
            if not self.market_data.is_feed_fresh(5):
                self._stop_for_feed_failure(now)
                return

            now_monotonic = monotonic()
            stale_position = any(
                position["status"] == "OPEN"
                and now_monotonic - self._last_option_tick_monotonic.get(
                    int(position["option_token"]), 0
                ) > 5
                for position in self.positions.values()
            )
            if stale_position:
                self._stop_for_feed_failure(now, "Option quote is stale")
                return

            for token, pending in list(self._pending_contracts.items()):
                requested_at = self._pending_quote_started.get(token, now_monotonic)
                if now_monotonic - requested_at > 5:
                    self._cancel_pending_entry(
                        token, pending, now, "OPTION_QUOTE_TIMEOUT"
                    )

            self._watchdog_timer = None
            self._schedule_watchdog()

    def _stop_for_feed_failure(
        self, now, reason="Market data connection is not healthy"
    ):
        self._cancel_pending_entries(now, "FEED_UNHEALTHY")

        for position in list(self.positions.values()):
            self._record_unpriced_feed_exit(position, now)

        self.running = False
        self._auto_start_suppressed_day = now.date().isoformat()

        if self._exit_timer:
            self._exit_timer.cancel()
            self._exit_timer = None
        self._cancel_watchdog()

        self.events.append({
            "time": now.isoformat(),
            "event": "FEED_GUARD_STOP",
            "reason": reason,
        })

    def on_market_tick(self, rows):
        now = datetime.now(IST)
        if not self.market_data.is_feed_fresh(5):
            with self.lock:
                if self.running:
                    self._stop_for_feed_failure(now)
            return

        with self.lock:
            today = now.date().isoformat()
            if not self.running:
                exit_time = self._exit_clock_time()
                auto_start_window = time(9, 15, 30) <= now.time() < exit_time
                if (
                    self.config.enabled
                    and self.config.auto_start
                    and self.selected_mode == "PAPER"
                    and auto_start_window
                    and self._auto_start_attempted_day != today
                    and self._auto_start_suppressed_day != today
                ):
                    try:
                        self.start("PAPER", access_token=None)
                        self._auto_start_attempted_day = today
                    except RuntimeError as exc:
                        self._auto_start_attempted_day = today
                        self.events.append({
                            "time": now.isoformat(),
                            "event": "AUTO_START_FAILED",
                            "reason": str(exc),
                        })
                if not self.running:
                    return

            if now.date().isoformat() != self._day:
                self._cancel_pending_entries(now, "TRADING_DAY_CHANGED")
                self._exit_open_paper_positions(now, "TRADING_DAY_CHANGED")
                self.running = False
                if self._exit_timer:
                    self._exit_timer.cancel()
                    self._exit_timer = None
                self._cancel_watchdog()
                return

            if now.time() >= self._exit_clock_time():
                self._square_off_paper(rows, now)
                self.running = False
                if self._exit_timer:
                    self._exit_timer.cancel()
                    self._exit_timer = None
                self._cancel_watchdog()
                return

            for row in rows:
                instrument_type = row.get("instrument_type")
                if instrument_type in ("CE", "PE"):
                    self._handle_option_tick(row, now)
                    continue

                if instrument_type != "EQ":
                    continue

                symbol = str(row.get("symbol") or "").upper()
                signal = self.engine.on_tick(row, now)
                if not signal:
                    continue

                open_count = (
                    sum(
                        position["status"] == "OPEN"
                        for position in self.positions.values()
                    )
                    + len(self._pending_contracts)
                )
                if open_count >= self.config.max_positions:
                    self.events.append({
                        "time": now.isoformat(),
                        "event": "SIGNAL_SKIPPED",
                        "symbol": symbol,
                        "reason": "Maximum open positions reached",
                    })
                    continue

                contract = self._select_atm(symbol, signal)
                if not contract:
                    self.events.append({
                        "time": now.isoformat(),
                        "event": "SIGNAL_SKIPPED",
                        "symbol": symbol,
                        "reason": (
                            "No valid nearest ATM option in current instrument master"
                        ),
                    })
                    continue

                token = int(contract.get("instrument_token") or 0)
                lot_size = int(contract.get("lot_size") or 0)
                option_symbol = contract.get("tradingsymbol")
                if token <= 0 or lot_size <= 0 or not option_symbol:
                    self.events.append({
                        "time": now.isoformat(),
                        "event": "SIGNAL_SKIPPED",
                        "symbol": symbol,
                        "reason": "Invalid option contract details",
                    })
                    continue

                if (
                    token in self._pending_contracts
                    or not self.engine.reserve_entry(symbol)
                ):
                    continue

                pending = {
                    "id": str(uuid4()),
                    "symbol": symbol,
                    "option_symbol": option_symbol,
                    "option_token": token,
                    "direction": signal,
                    "lot_size": lot_size,
                    "mode": "PAPER",
                    "status": "WAITING_QUOTE",
                }
                self._option_token_symbols[token] = option_symbol
                self._pending_contracts[token] = pending
                self._pending_quote_started[token] = monotonic()

                try:
                    subscribed = self.market_data.subscribe_extra(
                        [token],
                        full=True,
                    )
                    if subscribed is False:
                        raise RuntimeError(
                            "Market data engine rejected option subscription."
                        )
                except Exception as exc:
                    self._pending_contracts.pop(token, None)
                    self._option_token_symbols.pop(token, None)
                    self._pending_quote_started.pop(token, None)
                    self.engine.release_pending_entry(symbol)
                    self.events.append({
                        "time": now.isoformat(),
                        "event": "SUBSCRIPTION_FAILED",
                        "symbol": symbol,
                        "reason": str(exc),
                    })
                    continue

                self.events.append({
                    "time": now.isoformat(),
                    "event": "SIGNAL",
                    "symbol": symbol,
                    "direction": signal,
                    "option_symbol": option_symbol,
                    "status": "WAITING_OPTION_TICK",
                    "detail": (
                        "ATM option subscribed; waiting for fresh full-depth option tick."
                    ),
                })

    def _time_exit(self):
        now = datetime.now(IST)
        with self.lock:
            if not self.running:
                return
            self._cancel_pending_entries(now, f"TIME_EXIT_{self.config.exit_time[:5].replace(':', '_')}")
            self._exit_open_paper_positions(now, f"TIME_EXIT_{self.config.exit_time[:5].replace(':', '_')}")
            self.running = False
            if self._exit_timer:
                self._exit_timer.cancel()
                self._exit_timer = None
            self._cancel_watchdog()
            self.events.append({
                "time": now.isoformat(),
                "event": "STRATEGY_TIME_EXIT",
                "exit_time": self.config.exit_time,
            })

    def _handle_option_tick(self, row, now):
        token = int(row.get("instrument_token") or 0)
        symbol = self._option_token_symbols.get(token)
        if not symbol:
            return

        self._last_option_ticks[token] = row
        pending = self._pending_contracts.get(token)
        ltp = float(row.get("last_price") or 0)
        if ltp <= 0:
            return
        self._last_option_tick_monotonic[token] = monotonic()

        if pending:
            prices = self._liquidity_prices(row)
            if not prices:
                return

            bid, ask = prices
            position_id = pending["id"]
            pending.update(
                status="OPEN",
                entry_price=ask,
                stop_price=round(
                    ask - self.config.stop_loss_points,
                    2,
                ),
                ltp=ltp,
                bid=bid,
                ask=ask,
                quantity=int(pending["lot_size"]),
                opened_at=now.isoformat(),
            )
            self.positions[position_id] = pending
            self._pending_contracts.pop(token, None)
            self._pending_quote_started.pop(token, None)
            self.engine.mark_position_open(
                pending["symbol"],
                position_id,
            )
            # Persist the OPEN lifecycle immediately so a backend restart
            # cannot silently erase an active PAPER position.
            try:
                self.store.save_trade({
                    "id": pending["id"],
                    "trade_date": now.date().isoformat(),
                    "symbol": pending["symbol"],
                    **pending,
                })
            except Exception as exc:
                self.events.append({
                    "time": now.isoformat(),
                    "event": "PAPER_OPEN_PERSIST_FAILED",
                    "symbol": pending["symbol"],
                    "message": str(exc),
                })
                self._stop_for_feed_failure(
                    now,
                    "Could not persist open PAPER position",
                )
                return
            self.events.append({
                "time": now.isoformat(),
                "event": "PAPER_ENTRY",
                "symbol": pending["symbol"],
                "option_symbol": symbol,
                "price": ask,
                "quantity": pending["quantity"],
                "entry_number": (
                    self.engine.stocks[pending["symbol"]].entry_count
                ),
            })
            return

        for position in self.positions.values():
            if (
                int(position["option_token"]) == token
                and position["status"] == "OPEN"
            ):
                position["ltp"] = ltp
                prices = self._liquidity_prices(row)
                if prices:
                    position["bid"], position["ask"] = prices
                    # Mark open PAPER positions to the executable bid-side quote.
                    position["pnl"] = round(
                        (prices[0] - position["entry_price"]) * position["quantity"],
                        2,
                    )
                # Stop-loss decisions and simulated exits use the same
                # executable bid-side price; LTP alone is not an exit quote.
                executable_bid = prices[0] if prices else 0
                if executable_bid > 0 and executable_bid <= position["stop_price"]:
                    self._close_paper(
                        position,
                        executable_bid,
                        "STOP_LOSS",
                        now,
                    )

    def _close_paper(self, position, price, reason, now):
        if position["status"] != "OPEN":
            return

        position.update(
            status="CLOSED",
            exit_price=price,
            exit_reason=reason,
            closed_at=now.isoformat(),
        )
        position["pnl"] = round(
            (price - position["entry_price"]) * position["quantity"],
            2,
        )
        persistence_error = None
        persistence_deferred = False
        try:
            saved = self.store.save_trade({
                "id": position["id"],
                "trade_date": now.date().isoformat(),
                "symbol": position["symbol"],
                **position,
            })
            persistence_deferred = saved is False
        except Exception as exc:
            # Keep the paper lifecycle consistent even if both SQLite and
            # the fallback journal are unavailable.
            persistence_error = str(exc)

        self.engine.mark_position_closed(
            position["symbol"],
            position["id"],
        )
        if persistence_deferred:
            self.events.append({
                "time": now.isoformat(),
                "event": "PAPER_EXIT_PERSIST_DEFERRED",
                "symbol": position["symbol"],
                "reason": reason,
                "message": "Trade queued in the durable recovery journal",
            })
        if persistence_error is not None:
            self.events.append({
                "time": now.isoformat(),
                "event": "PAPER_EXIT_PERSIST_FAILED",
                "symbol": position["symbol"],
                "reason": reason,
                "message": persistence_error,
            })
        self.events.append({
            "time": now.isoformat(),
            "event": "PAPER_EXIT",
            "symbol": position["symbol"],
            "reason": reason,
            "price": price,
            "pnl": position["pnl"],
        })

    def _square_off_paper(self, rows, now):
        self._cancel_pending_entries(now, f"TIME_EXIT_{self.config.exit_time[:5].replace(':', '_')}")
        # Use an option tick delivered in this 09:30 batch before checking
        # quote age; an equity-only batch must not refresh an option quote.
        for row in rows:
            if row.get("instrument_type") not in ("CE", "PE"):
                continue
            token = int(row.get("instrument_token") or 0)
            if token not in self._option_token_symbols:
                continue
            if float(row.get("last_price") or 0) <= 0:
                continue
            self._last_option_ticks[token] = row
            self._last_option_tick_monotonic[token] = monotonic()

        self._exit_open_paper_positions(now, "TIME_EXIT_09_30")

    def stop(self):
        with self.lock:
            now = datetime.now(IST)
            self._auto_start_suppressed_day = now.date().isoformat()
            self.running = False
            self._cancel_pending_entries(now, "MANUAL_STOP")
            self._exit_open_paper_positions(now, "MANUAL_STOP")
            if self._exit_timer:
                self._exit_timer.cancel()
                self._exit_timer = None
            self._cancel_watchdog()
            self.events.append({
                "time": now.isoformat(),
                "event": "STOPPED",
            })
            return self.snapshot()

    def snapshot(self):
        snapshot = self.market_data.snapshot()
        return {
            "running": self.running,
            "mode": self.mode,
            "market_data_ready": self.market_data.is_feed_fresh(5),
            "watchlist_count": len(self.engine.stocks),
            "open_positions": (
                sum(
                    position["status"] == "OPEN"
                    for position in self.positions.values()
                )
                + sum(
                    1
                    for pending in self._pending_contracts.values()
                    if pending.get("status") == "WAITING_QUOTE"
                )
            ),
            "trades_count": len(
                self.store.trades(datetime.now(IST).date().isoformat())
            ),
            "watchlist": self.engine.snapshot(),
            "positions": list(self.positions.values()) + [
                pending
                for pending in self._pending_contracts.values()
                if pending.get("status") == "WAITING_QUOTE"
            ],
            "events": self.events[-100:],
            "market_status": snapshot["status"],
            "started_at": self.started_at,
            "live_ready": False,
            "live_block_reason": (
                "Protective stop order lifecycle, partial-fill recovery "
                "and broker reconciliation require verification."
            ),
        }

    def delete_trade(self, trade_id):
        """Delete a completed historical trade, never an active position."""
        with self.lock:
            if any(
                position.get("id") == trade_id and position.get("status") == "OPEN"
                for position in self.positions.values()
            ):
                raise RuntimeError("An open position cannot be deleted from trade history.")
            deleted = self.store.delete_trade(trade_id)
            if not deleted:
                raise KeyError("Trade was not found.")
            self.events.append({
                "time": datetime.now(IST).isoformat(),
                "event": "TRADE_HISTORY_DELETED",
                "trade_id": trade_id,
            })
            return {"deleted": True, "id": trade_id}

    def trades(self, trade_date=None):
        selected_date = trade_date or datetime.now(IST).date().isoformat()
        return self.store.trades(selected_date)
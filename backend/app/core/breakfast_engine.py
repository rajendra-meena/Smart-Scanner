"""Deterministic sector-to-stock selection for Breakfast Strategy."""
from collections import defaultdict


class BreakfastEngine:
    """Selects a leading/lagging sector and its leading/lagging stocks."""

    SECTOR_BY_SYMBOL = {
        **dict.fromkeys(("HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "FEDERALBNK", "IDFCFIRSTB", "AUBANK", "BANDHANBNK"), "BANK"),
        **dict.fromkeys(("TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"), "IT"),
        **dict.fromkeys(("MARUTI", "TATAMOTORS", "M&M", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT", "TVSMOTOR", "ASHOKLEY", "BOSCHLTD"), "AUTO"),
        **dict.fromkeys(("HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "TATACONSUM", "DABUR", "GODREJCP", "MARICO", "COLPAL", "VBL"), "FMCG"),
        **dict.fromkeys(("SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA", "BIOCON", "ALKEM", "MANKIND", "TORNTPHARM"), "PHARMA"),
        **dict.fromkeys(("TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "JINDALSTEL", "SAIL", "NATIONALUM", "NMDC"), "METAL"),
        **dict.fromkeys(("DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD", "LODHA"), "REALTY"),
        **dict.fromkeys(("RELIANCE", "ONGC", "IOC", "BPCL", "GAIL", "OIL", "PETRONET", "IGL", "MGL"), "OIL & GAS"),
        **dict.fromkeys(("ZEEL", "PVRINOX", "SUNTV", "NETWORK18"), "MEDIA"),
        **dict.fromkeys(("APOLLOHOSP", "MAXHEALTH", "FORTIS", "SYNGENE", "LALPATHLAB", "METROPOLIS"), "HEALTHCARE"),
        **dict.fromkeys(("BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "SHRIRAMFIN", "SBICARD", "HDFCAMC", "MUTHOOTFIN", "RECLTD", "PFC", "LICHSGFIN", "MANAPPURAM"), "FINANCIAL SERVICES"),
        **dict.fromkeys(("NTPC", "POWERGRID", "ADANIPOWER", "TATAPOWER", "NHPC", "SJVN"), "ENERGY"),
    }

    def sector_snapshot(self, equity_rows, option_symbols=None):
        option_symbols = set(option_symbols or ())
        sectors = defaultdict(list)
        for row in equity_rows:
            symbol = str(row.get("symbol") or "").upper()
            sector = self.SECTOR_BY_SYMBOL.get(symbol)
            price = float(row.get("last_price") or 0)
            change = float(row.get("change_percent") or 0)
            if sector and price > 0 and row.get("close", 0) > 0 and (not option_symbols or symbol in option_symbols):
                sectors[sector].append({**row, "symbol": symbol, "change_percent": change})
        result = []
        for name, rows in sectors.items():
            if len(rows) < 2:
                continue
            positive = sum(r["change_percent"] > 0 for r in rows)
            negative = sum(r["change_percent"] < 0 for r in rows)
            result.append({
                "sector": name, "stocks_count": len(rows),
                "advances": positive, "declines": negative,
                "breadth_percent": round(max(positive, negative) / len(rows) * 100, 2),
                "average_change_percent": round(sum(r["change_percent"] for r in rows) / len(rows), 3),
                "stocks": sorted(rows, key=lambda r: r["change_percent"], reverse=True),
            })
        return sorted(result, key=lambda s: s["average_change_percent"], reverse=True)

    def select(self, equity_rows, nifty_change, first_candles, option_symbols, top_stock_count=3, excessive_move_percent=5.0, min_sector_breadth_percent=60.0):
        nifty_change = float(nifty_change or 0)
        if nifty_change == 0:
            return {"ready": False, "reason": "NIFTY direction is flat; no directional trade", "direction": None, "sector": None, "candidates": [], "sectors": self.sector_snapshot(equity_rows, option_symbols)}
        direction = "LONG" if nifty_change > 0 else "SHORT"
        sectors = self.sector_snapshot(equity_rows, option_symbols)
        ranked = sectors if direction == "LONG" else list(reversed(sectors))
        for sector in ranked:
            breadth = sector["advances"] if direction == "LONG" else sector["declines"]
            if breadth / sector["stocks_count"] * 100 < min_sector_breadth_percent:
                continue
            ordered = sector["stocks"] if direction == "LONG" else list(reversed(sector["stocks"]))
            candidates = []
            for row in ordered:
                candle = first_candles.get(row["symbol"])
                if not candle or candle.get("open", 0) <= 0:
                    continue
                move = (float(candle["high"]) - float(candle["low"])) / float(candle["open"]) * 100
                if move >= excessive_move_percent:
                    continue
                candidates.append({
                    "symbol": row["symbol"], "price": float(row["last_price"]),
                    "change_percent": float(row["change_percent"]),
                    "first_candle_move_percent": round(move, 3),
                    "first_candle": dict(candle),
                })
                if len(candidates) >= int(top_stock_count):
                    break
            if candidates:
                return {"ready": True, "reason": "Sector breadth and stock ranking confirmed", "direction": direction,
                        "nifty_change_percent": nifty_change, "sector": sector["sector"],
                        "sector_average_change_percent": sector["average_change_percent"],
                        "sector_breadth_percent": sector["breadth_percent"],
                        "candidates": candidates, "sectors": sectors}
        return {"ready": False, "reason": "No sector has sufficient directional breadth and eligible stocks", "direction": direction, "sector": None, "candidates": [], "sectors": sectors}

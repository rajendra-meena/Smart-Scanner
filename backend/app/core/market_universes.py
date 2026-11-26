import csv
import io
import logging
import threading
import time
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Official NSE archive constituent files. Refresh periodically so reconstitutions
# are picked up without embedding a stale symbol list in the application.
INDEX_CSV_URLS = {
    "NIFTY_50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "BANK_NIFTY": "https://nsearchives.nseindia.com/content/indices/ind_niftybanklist.csv",
    "NIFTY_NEXT_50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
}
_CACHE_TTL_SECONDS = 4 * 60 * 60


class OfficialIndexUniverseStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, list[str]]] = {}

    def get(self, key: str) -> list[str]:
        if key not in INDEX_CSV_URLS:
            raise ValueError("Unsupported index universe")
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < _CACHE_TTL_SECONDS:
                return list(cached[1])

        request = Request(
            INDEX_CSV_URLS[key],
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                "Accept": "text/csv,*/*",
                "Referer": "https://www.nseindia.com/",
            },
        )
        with urlopen(request, timeout=12) as response:
            content = response.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames or "Symbol" not in reader.fieldnames:
            raise RuntimeError(f"Official NSE {key} constituent CSV has an unexpected format")
        symbols = sorted({(row.get("Symbol") or "").strip().upper() for row in reader if (row.get("Symbol") or "").strip()})
        if not symbols:
            raise RuntimeError(f"Official NSE {key} constituent list is empty")
        with self._lock:
            self._cache[key] = (now, symbols)
        return list(symbols)


official_index_universes = OfficialIndexUniverseStore()

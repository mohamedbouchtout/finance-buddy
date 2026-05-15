"""ETF look-through data loader.

We treat the ETF universe in `data/etfs.json` as known. For any ETF not
in that list, callers should fall back to treating it as a single position.
"""
import json
from functools import lru_cache
from typing import Optional

from app.config import ETF_DATA_PATH

# The Magnificent 7. Both share classes of Alphabet count toward GOOG/L exposure.
MAG7 = {"AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA"}


@lru_cache(maxsize=1)
def _load() -> dict:
    with open(ETF_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_etf(ticker: str) -> Optional[dict]:
    """Return ETF metadata, or None if we don't have look-through data."""
    if not ticker:
        return None
    data = _load()
    return data.get(ticker.upper())


def known_etfs() -> list[str]:
    return [k for k in _load().keys() if not k.startswith("_")]


def is_known_etf(ticker: str) -> bool:
    return get_etf(ticker) is not None

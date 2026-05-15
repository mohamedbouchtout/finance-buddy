"""Market data fetching with SQLite-backed 15-minute cache.

Uses yfinance for free price data. Falls back to last cached price on failure
so the app never goes down when Yahoo rate-limits us.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Iterable

from app.storage import get_cached_price, set_cached_price

log = logging.getLogger(__name__)
CACHE_TTL_SECONDS = 15 * 60


def _fetch_one(ticker: str) -> float | None:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        # fast_info is much quicker than .info
        price = None
        try:
            price = float(t.fast_info["last_price"])
        except Exception:
            hist = t.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        if price and price > 0:
            return price
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", ticker, e)
    return None


def get_price(ticker: str) -> float | None:
    """Get latest price for a ticker, using cache. Returns None if unknown."""
    ticker = ticker.upper().strip()
    if not ticker:
        return None

    cached = get_cached_price(ticker)
    if cached and (time.time() - cached["fetched_at"]) < CACHE_TTL_SECONDS:
        return cached["price"]

    price = _fetch_one(ticker)
    if price is not None:
        set_cached_price(ticker, price)
        return price

    # Fallback: stale cache better than nothing
    if cached:
        log.info("Using stale cached price for %s", ticker)
        return cached["price"]
    return None


def get_prices(tickers: Iterable[str]) -> Dict[str, float | None]:
    """Bulk price fetch; uses cache where possible."""
    out: Dict[str, float | None] = {}
    for t in tickers:
        out[t.upper().strip()] = get_price(t)
    return out

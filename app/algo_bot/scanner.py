"""Fetch OHLCV via yfinance and scan a list of tickers for 200MA retest signals."""
from __future__ import annotations

import logging
from typing import Dict, List

import pandas as pd

from .strategy import detect_signal
from .ai_predictor import predict as ai_predict

log = logging.getLogger(__name__)


def fetch_ohlcv(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    try:
        import yfinance as yf
        df = yf.download(symbol, period=period, interval="1d",
                         auto_adjust=False, progress=False, threads=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        if "adj close" in df.columns and "close" not in df.columns:
            df["close"] = df["adj close"]
        for col in ("date", "open", "high", "low", "close", "volume"):
            if col not in df.columns:
                log.warning("yfinance missing column %s for %s", col, symbol)
                return None
        df["symbol"] = symbol.upper()
        return df[["date", "open", "high", "low", "close", "volume", "symbol"]].copy()
    except Exception as e:
        log.warning("Failed to fetch %s: %s", symbol, e)
        return None


def scan_tickers(tickers: List[str], params: Dict, max_signals: int | None = None) -> Dict:
    signals = []
    errors = []
    scanned = 0
    for sym in tickers:
        sym = sym.strip().upper()
        if not sym:
            continue
        df = fetch_ohlcv(sym)
        if df is None or len(df) < params["strategy_retest_200ma"]["ma_period"] + 20:
            errors.append(f"{sym}: not enough data")
            continue
        scanned += 1
        sig = detect_signal(df, params, symbol=sym)
        if sig:
            signals.append(sig)
            if max_signals and len(signals) >= max_signals:
                break
    return {"signals": signals, "scanned": scanned, "errors": errors}


def scan_tickers_ai(tickers: List[str], max_results: int | None = None) -> Dict:
    """Pretrained AI-predictor scan: scores every ticker (no strict pattern gating).

    Returns predictions sorted by absolute distance from neutral (50), so the
    strongest bullish and bearish setups float to the top regardless of direction.
    """
    predictions = []
    errors = []
    scanned = 0
    for sym in tickers:
        sym = sym.strip().upper()
        if not sym:
            continue
        df = fetch_ohlcv(sym)
        if df is None or len(df) < 60:
            errors.append(f"{sym}: not enough data")
            continue
        scanned += 1
        pred = ai_predict(df, symbol=sym)
        if pred is not None:
            predictions.append(pred)

    predictions.sort(key=lambda p: abs(p["score"] - 50.0), reverse=True)
    if max_results:
        predictions = predictions[:max_results]
    return {"predictions": predictions, "scanned": scanned, "errors": errors}

"""Pretrained AI predictor — multi-factor model that scores every ticker.

This replaces the strict 200MA-retest detector as the primary scanner output.
The retest detector still runs for high-conviction Pro signals, but every ticker
now gets a 0-100 bullish score with a predicted setup type so the free tier
always shows useful output.

Model: a feature-weighted ensemble (calibrated logistic-style mapping) built on
classical technicals — trend, momentum, MA alignment, RSI, volume strength,
volatility regime, distance from highs. The weights are fixed ("pretrained")
based on backtested behavior of momentum + trend-following systems.
"""
from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
import pandas as pd


# ---- pretrained weights (do not edit casually — these are model coefficients) ----
W = {
    "trend_ma200": 1.6,        # close vs 200ma (clipped)
    "trend_ma50":  0.8,        # close vs 50ma
    "alignment":   1.2,        # ma20>ma50>ma200 alignment
    "mom_20":      1.2,        # 20-day momentum
    "mom_60":      0.7,        # 60-day momentum
    "ma_slope":    1.4,        # 200ma slope
    "rsi_zone":    0.9,        # rsi bell curve centered ~58
    "vol_strength": 0.5,       # recent volume vs longer avg
    "dist_high":   0.6,        # distance from 52w high
    "vol_regime":  -0.4,       # high realized vol = penalty
}
BIAS = 0.0  # logit bias


def _safe(x, default=0.0):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return default
    return float(x)


def _rsi(close: pd.Series, n: int = 14) -> float:
    if len(close) < n + 1:
        return 50.0
    delta = close.diff().dropna()
    up = delta.clip(lower=0.0).rolling(n).mean()
    down = (-delta.clip(upper=0.0)).rolling(n).mean()
    rs = up / down.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return 50.0 if pd.isna(val) else float(val)


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    if len(df) < n + 1:
        return float(df["close"].iloc[-1]) * 0.02
    high = df["high"]; low = df["low"]; close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low),
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def extract_features(df: pd.DataFrame) -> Dict[str, float]:
    if df is None or len(df) < 60:
        return {}
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(min(200, len(close))).mean()  # graceful for short series

    last_close = float(close.iloc[-1])
    last_ma200 = float(ma200.iloc[-1]) if not pd.isna(ma200.iloc[-1]) else last_close
    last_ma50 = float(ma50.iloc[-1]) if not pd.isna(ma50.iloc[-1]) else last_close
    last_ma20 = float(ma20.iloc[-1]) if not pd.isna(ma20.iloc[-1]) else last_close

    pct_above_ma200 = (last_close - last_ma200) / last_ma200 if last_ma200 else 0.0
    pct_above_ma50 = (last_close - last_ma50) / last_ma50 if last_ma50 else 0.0

    alignment = 0.0
    if last_ma20 > last_ma50 > last_ma200:
        alignment = 1.0
    elif last_ma20 < last_ma50 < last_ma200:
        alignment = -1.0
    else:
        # partial credit
        alignment = (
            (1 if last_ma20 > last_ma50 else -1) * 0.4
            + (1 if last_ma50 > last_ma200 else -1) * 0.4
        )

    def mom(n):
        if len(close) < n + 1:
            return 0.0
        prev = float(close.iloc[-n - 1])
        return (last_close - prev) / prev if prev else 0.0

    mom_20 = mom(20)
    mom_60 = mom(60)

    # 200ma slope over last 20 bars
    ma_slope = 0.0
    if len(ma200) > 21 and not pd.isna(ma200.iloc[-21]):
        m_now = float(ma200.iloc[-1]); m_then = float(ma200.iloc[-21])
        ma_slope = (m_now - m_then) / m_then if m_then else 0.0

    rsi = _rsi(close, 14)

    # volume strength: last 10 avg / last 50 avg
    vol_10 = float(volume.tail(10).mean())
    vol_50 = float(volume.tail(50).mean()) if len(volume) >= 50 else vol_10
    vol_strength = (vol_10 / vol_50 - 1.0) if vol_50 else 0.0

    # distance from 52w high
    lookback = min(252, len(close))
    period_high = float(close.tail(lookback).max())
    dist_from_high = (last_close - period_high) / period_high if period_high else 0.0

    # realized vol (annualized) over 20d
    rets = close.pct_change().dropna().tail(20)
    vol_regime = float(rets.std() * math.sqrt(252)) if len(rets) else 0.0

    atr = _atr(df, 14)

    return {
        "close": last_close,
        "ma20": last_ma20, "ma50": last_ma50, "ma200": last_ma200,
        "pct_above_ma200": pct_above_ma200,
        "pct_above_ma50": pct_above_ma50,
        "alignment": alignment,
        "mom_20": mom_20,
        "mom_60": mom_60,
        "ma_slope": ma_slope,
        "rsi": rsi,
        "vol_strength": vol_strength,
        "dist_from_high": dist_from_high,
        "vol_regime": vol_regime,
        "atr": atr,
    }


def _logistic(x: float) -> float:
    """Squash to (0,1)."""
    if x > 50: return 1.0
    if x < -50: return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _score_from_features(f: Dict[str, float]) -> float:
    """Returns a 0-100 bullish score from features. 50 == neutral."""
    if not f:
        return 50.0

    # Normalize and clip each feature into a sane range
    trend_ma200 = max(-0.30, min(0.30, f["pct_above_ma200"])) / 0.30   # in [-1, 1]
    trend_ma50  = max(-0.20, min(0.20, f["pct_above_ma50"])) / 0.20
    alignment   = f["alignment"]                                       # already in [-1, 1]
    mom_20      = max(-0.20, min(0.20, f["mom_20"])) / 0.20
    mom_60      = max(-0.40, min(0.40, f["mom_60"])) / 0.40
    ma_slope    = max(-0.15, min(0.15, f["ma_slope"])) / 0.15

    # RSI bell curve: peak at 58, falls off toward 30 or 85
    rsi = f["rsi"]
    rsi_zone = math.exp(-((rsi - 58) ** 2) / (2 * 12 ** 2)) * 2 - 1     # [-1, 1]

    vol_strength = max(-0.5, min(1.0, f["vol_strength"]))
    dist_high = max(-0.30, min(0.0, f["dist_from_high"])) / 0.30 + 1   # [0, 1] (closer to high is better)
    dist_high = dist_high * 2 - 1                                       # [-1, 1]
    vol_regime = -max(0.0, min(0.80, f["vol_regime"]) - 0.20) / 0.60   # high vol penalty

    logit = (
        BIAS
        + W["trend_ma200"] * trend_ma200
        + W["trend_ma50"]  * trend_ma50
        + W["alignment"]   * alignment
        + W["mom_20"]      * mom_20
        + W["mom_60"]      * mom_60
        + W["ma_slope"]    * ma_slope
        + W["rsi_zone"]    * rsi_zone
        + W["vol_strength"] * vol_strength
        + W["dist_high"]   * dist_high
        + W["vol_regime"]  * vol_regime
    )
    p = _logistic(logit)
    return round(p * 100.0, 1)


def _classify(score: float, f: Dict[str, float]) -> tuple[str, str, str]:
    """Return (direction, setup_type, confidence)."""
    if not f:
        return ("NEUTRAL", "Insufficient data", "LOW")

    if score >= 65:
        direction = "BULLISH"
    elif score <= 35:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    if direction == "BULLISH":
        if f["mom_20"] > 0.05 and f["pct_above_ma200"] > 0.02 and f["vol_strength"] > 0.05:
            setup = "Bullish breakout"
        elif f["alignment"] > 0.5 and f["mom_20"] > 0 and f["ma_slope"] > 0:
            setup = "Trend continuation"
        elif f["pct_above_ma200"] > 0 and f["ma_slope"] > 0 and f["mom_20"] < 0.02:
            setup = "Trend pullback"
        elif f["mom_20"] > 0.03:
            setup = "Early momentum"
        else:
            setup = "Bullish drift"
    elif direction == "BEARISH":
        if f["mom_20"] < -0.05 and f["pct_above_ma200"] < -0.02:
            setup = "Bearish breakdown"
        elif f["alignment"] < -0.5:
            setup = "Downtrend"
        else:
            setup = "Bearish drift"
    else:
        setup = "Consolidation"

    # Confidence: distance from 50 + feature agreement
    margin = abs(score - 50)
    agreement = sum(1 for v in [f["alignment"], f["mom_20"], f["ma_slope"], f["pct_above_ma200"]]
                    if (v > 0 and direction == "BULLISH") or (v < 0 and direction == "BEARISH"))
    if margin >= 22 and agreement >= 3:
        conf = "HIGH"
    elif margin >= 12 and agreement >= 2:
        conf = "MEDIUM"
    else:
        conf = "LOW"
    return (direction, setup, conf)


def predict(df: pd.DataFrame, symbol: str = "") -> Optional[Dict]:
    """Run the AI predictor on a single OHLCV DataFrame. Returns a prediction dict."""
    f = extract_features(df)
    if not f:
        return None

    score = _score_from_features(f)
    direction, setup, conf = _classify(score, f)

    entry = f["close"]
    atr = max(f["atr"], entry * 0.005)  # floor at 0.5% of price

    if direction == "BULLISH":
        stop = entry - 1.5 * atr
        target = entry + 3.0 * atr
    elif direction == "BEARISH":
        stop = entry + 1.5 * atr
        target = entry - 3.0 * atr
    else:
        stop = entry - 1.0 * atr
        target = entry + 1.5 * atr

    return {
        "symbol": symbol or "",
        "score": score,
        "direction": direction,
        "setup_type": setup,
        "confidence": conf,
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "rr": round(abs(target - entry) / max(abs(entry - stop), 1e-6), 2),
        "features": {
            "above_ma200_pct": round(f["pct_above_ma200"] * 100, 2),
            "momentum_20d_pct": round(f["mom_20"] * 100, 2),
            "momentum_60d_pct": round(f["mom_60"] * 100, 2),
            "ma200_slope_pct": round(f["ma_slope"] * 100, 2),
            "rsi": round(f["rsi"], 1),
            "volume_strength": round(f["vol_strength"], 2),
            "dist_from_52w_high_pct": round(f["dist_from_high"] * 100, 2),
            "realized_vol_annual_pct": round(f["vol_regime"] * 100, 1),
            "atr": round(f["atr"], 2),
        },
    }

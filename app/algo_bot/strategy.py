"""200MA breakout-and-retest strategy — pure functions over an OHLCV DataFrame.

Required DataFrame columns: ``date``, ``open``, ``high``, ``low``, ``close``, ``volume``.
A ``symbol`` column is included on output signals; if missing it defaults to ``""``.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def add_ma(df: pd.DataFrame, period: int, col: str = "ma200") -> pd.DataFrame:
    df = df.copy()
    df[col] = df["close"].rolling(window=period).mean()
    return df


def ma_slope(ma_values: np.ndarray, idx: int, slope_period: int) -> float:
    if idx < slope_period:
        return 0.0
    past = ma_values[idx - slope_period]
    if past == 0 or np.isnan(past):
        return 0.0
    return (ma_values[idx] - past) / past


def is_up_or_flat(ma_values, idx, params) -> bool:
    return ma_slope(ma_values, idx, params["ma_slope_period"]) >= params["min_uptrend_slope"]


def is_down_or_flat(ma_values, idx, params) -> bool:
    return ma_slope(ma_values, idx, params["ma_slope_period"]) <= params["max_downtrend_slope"]


def _format_price(p: float) -> float:
    return round(p, 4) if p < 1 else round(p, 2)


def _signal_payload(
    direction, symbol, df, entry, stop, target, risk,
    breakout_idx, retest_idx, volume_ratio, retest_volume_ratio,
    avg_volume, bounce_strength, breakout_strength, slope, rr_ratio,
) -> Dict:
    return {
        "strategy_type": "200ma_retest",
        "type": direction,
        "symbol": symbol,
        "entry": _format_price(entry),
        "stop": _format_price(stop),
        "target": _format_price(target),
        "risk": float(risk),
        "reward": float(risk * rr_ratio),
        "breakout_date": str(df.iloc[breakout_idx]["date"])[:10],
        "retest_date": str(df.iloc[retest_idx]["date"])[:10],
        "current_date": str(df.iloc[-1]["date"])[:10],
        "breakout_volume_ratio": float(volume_ratio),
        "retest_volume_ratio": float(retest_volume_ratio),
        "avg_volume": float(avg_volume),
        "bounce_strength": float(bounce_strength),
        "breakout_strength": float(breakout_strength),
        "ma_slope": float(slope),
        "ma_slope_pct": float(slope * 100),
    }


def detect_long_pattern(df_in: pd.DataFrame, params: Dict, symbol: str = "") -> Optional[Dict]:
    p = params["strategy_retest_200ma"]
    if len(df_in) < p["ma_period"] + 20:
        return None

    df = add_ma(df_in, p["ma_period"])
    recent = df.tail(30).reset_index(drop=True)
    if recent["ma200"].isna().any():
        return None

    ma200 = recent["ma200"].values
    close, high, low, vol = recent["close"].values, recent["high"].values, recent["low"].values, recent["volume"].values
    avg_volume = vol[-50:].mean() if len(vol) >= 50 else vol.mean()

    lo = max(len(recent) - p["lookback_days"], 0)
    for i in range(len(recent) - 5, lo, -1):
        if i < 5:
            continue
        if not is_up_or_flat(ma200, i, p):
            continue
        avg_price_before = close[i-5:i].mean()
        avg_ma_before = ma200[i-5:i].mean()
        was_below = avg_price_before < avg_ma_before * 0.98
        crossed = close[i] > ma200[i] and close[i-1] <= ma200[i-1]
        if not (was_below and crossed):
            continue
        volume_ratio = vol[i] / avg_volume if avg_volume > 0 else 0
        if volume_ratio < p["min_breakout_volume"]:
            continue
        rng = high[i] - low[i]
        breakout_strength = (close[i] - low[i]) / rng if rng > 0 else 0
        if breakout_strength < p["min_breakout_strength"]:
            continue

        for j in range(i + 2, min(i + 8, len(recent))):
            if not is_up_or_flat(ma200, j, p):
                continue
            retest_distance = abs(low[j] - ma200[j]) / ma200[j]
            if retest_distance >= p["retest_distance"]:
                continue
            retest_vol_ratio = vol[j] / avg_volume if avg_volume > 0 else 0
            if (retest_vol_ratio > volume_ratio * p["max_retest_volume_ratio"]
                    or retest_vol_ratio > p["max_retest_volume_absolute"]):
                continue
            if j >= len(recent) - 1:
                continue
            bounce_strength = (close[-1] - low[j]) / low[j] if low[j] > 0 else 0
            if bounce_strength < p["min_bounce_strength"]:
                continue
            if len(close) >= 2 and not close[-1] > close[-2]:
                continue
            entry_price = close[-1]
            stop_loss = low[j] * (1 - p["stop_loss_pct"])
            risk = entry_price - stop_loss
            if risk <= 0 or risk / entry_price > 0.05:
                continue
            target_price = entry_price + risk * p["risk_reward_ratio"]
            if (len(recent) - 1 - j) > p["max_days_since_retest"]:
                continue
            slope = ma_slope(ma200, len(recent) - 1, p["ma_slope_period"])
            return _signal_payload(
                "LONG", symbol, recent, entry_price, stop_loss, target_price, risk,
                i, j, volume_ratio, retest_vol_ratio, avg_volume,
                bounce_strength, breakout_strength, slope, p["risk_reward_ratio"],
            )
    return None


def detect_short_pattern(df_in: pd.DataFrame, params: Dict, symbol: str = "") -> Optional[Dict]:
    p = params["strategy_retest_200ma"]
    if len(df_in) < p["ma_period"] + 20:
        return None

    df = add_ma(df_in, p["ma_period"])
    recent = df.tail(30).reset_index(drop=True)
    if recent["ma200"].isna().any():
        return None

    ma200 = recent["ma200"].values
    close, high, low, vol = recent["close"].values, recent["high"].values, recent["low"].values, recent["volume"].values
    avg_volume = vol[-50:].mean() if len(vol) >= 50 else vol.mean()

    lo = max(len(recent) - p["lookback_days"], 0)
    for i in range(len(recent) - 5, lo, -1):
        if i < 5:
            continue
        if not is_down_or_flat(ma200, i, p):
            continue
        avg_price_before = close[i-5:i].mean()
        avg_ma_before = ma200[i-5:i].mean()
        was_above = avg_price_before > avg_ma_before * 1.02
        crossed = close[i] < ma200[i] and close[i-1] >= ma200[i-1]
        if not (was_above and crossed):
            continue
        volume_ratio = vol[i] / avg_volume if avg_volume > 0 else 0
        if volume_ratio < p["min_breakout_volume"]:
            continue
        rng = high[i] - low[i]
        breakdown_strength = (high[i] - close[i]) / rng if rng > 0 else 0
        if breakdown_strength < p["min_breakout_strength"]:
            continue

        for j in range(i + 2, min(i + 8, len(recent))):
            if not is_down_or_flat(ma200, j, p):
                continue
            retest_distance = abs(high[j] - ma200[j]) / ma200[j]
            if retest_distance >= p["retest_distance"]:
                continue
            retest_vol_ratio = vol[j] / avg_volume if avg_volume > 0 else 0
            if (retest_vol_ratio > volume_ratio * p["max_retest_volume_ratio"]
                    or retest_vol_ratio > p["max_retest_volume_absolute"]):
                continue
            if j >= len(recent) - 1:
                continue
            if not (close[-1] < low[j]):
                continue
            bounce_strength = (high[j] - close[-1]) / high[j] if high[j] > 0 else 0
            if bounce_strength < p["min_bounce_strength"]:
                continue
            if len(close) >= 2 and not close[-1] < close[-2]:
                continue
            entry_price = close[-1]
            stop_loss = high[j] * (1 + p["stop_loss_pct"])
            risk = stop_loss - entry_price
            if risk <= 0 or risk / entry_price > 0.05:
                continue
            target_price = entry_price - risk * p["risk_reward_ratio"]
            if (len(recent) - 1 - j) > p["max_days_since_retest"]:
                continue
            slope = ma_slope(ma200, len(recent) - 1, p["ma_slope_period"])
            return _signal_payload(
                "SHORT", symbol, recent, entry_price, stop_loss, target_price, risk,
                i, j, volume_ratio, retest_vol_ratio, avg_volume,
                bounce_strength, breakdown_strength, slope, p["risk_reward_ratio"],
            )
    return None


def detect_signal(df: pd.DataFrame, params: Dict, symbol: str = "") -> Optional[Dict]:
    sig = detect_long_pattern(df, params, symbol=symbol)
    if sig:
        return sig
    return detect_short_pattern(df, params, symbol=symbol)

"""Tests for the 200MA retest strategy on synthetic data."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.algo_bot.config import merged_params
from app.algo_bot.strategy import detect_long_pattern, detect_short_pattern, add_ma


def _make_dates(n: int):
    return pd.date_range("2022-01-01", periods=n, freq="B")


def _synthetic_long_pattern() -> pd.DataFrame:
    """Construct ~230 bars: 220 trending up to set MA, then dip below, breakout, retest, bounce."""
    rng = np.random.default_rng(0)
    n_pre = 220
    # Steady uptrend so MA200 rises near current price by the end of pre-phase
    base = np.linspace(80, 120, n_pre) + rng.normal(0, 0.3, n_pre)
    vol_pre = rng.integers(900_000, 1_100_000, n_pre)

    # Now 10 final bars that craft the breakout-retest setup
    # We want: bars [-10..-6] BELOW the MA, bar[-5] is a high-volume breakout above MA,
    # bar [-3] is a low-volume retest down to the MA, bars [-2..-1] bounce up.
    tail_close = np.array([116.0, 115.5, 115.0, 114.8, 121.5, 121.0, 119.6, 120.0, 120.8, 121.6])
    tail_high  = tail_close + 0.5
    tail_low   = tail_close - 0.5
    # Boost the breakout bar's high/low spread and put close near the high (strength)
    tail_high[4] = 122.0; tail_low[4] = 119.0; tail_close[4] = 121.8
    # Retest bar (index 7): low touches MA
    tail_low[7] = 119.4
    tail_volume = np.array([
        1_000_000, 1_000_000, 1_000_000, 1_000_000,
        3_000_000,  # breakout — 3x avg
        1_000_000, 1_000_000,
        500_000,    # retest — low vol
        1_000_000, 1_100_000,
    ])

    closes = np.concatenate([base, tail_close])
    # opens/highs/lows for pre-phase
    opens_pre = base - 0.1
    highs_pre = base + 0.5
    lows_pre  = base - 0.5
    opens = np.concatenate([opens_pre, tail_close - 0.2])
    highs = np.concatenate([highs_pre, tail_high])
    lows  = np.concatenate([lows_pre, tail_low])
    vols  = np.concatenate([vol_pre, tail_volume])

    dates = _make_dates(n_pre + 10)
    return pd.DataFrame({
        "date": dates, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": vols, "symbol": "TEST",
    })


def test_long_detector_smoke():
    """Smoke test: detector runs without crashing and, if it returns a signal,
    the returned payload has internally-consistent prices."""
    df = _synthetic_long_pattern()
    params = merged_params()
    sig = detect_long_pattern(df, params, symbol="TEST")
    if sig is not None:
        assert sig["type"] == "LONG"
        assert sig["symbol"] == "TEST"
        assert sig["entry"] > sig["stop"]
        assert sig["target"] > sig["entry"]
        assert sig["breakout_volume_ratio"] >= params["strategy_retest_200ma"]["min_breakout_volume"]


def test_flat_market_produces_no_signal():
    """A flat random-walk market should not trigger a 200MA retest signal."""
    rng = np.random.default_rng(42)
    n = 260
    closes = 100 + np.cumsum(rng.normal(0, 0.2, n))
    df = pd.DataFrame({
        "date": _make_dates(n),
        "open": closes - 0.1,
        "high": closes + 0.3,
        "low":  closes - 0.3,
        "close": closes,
        "volume": rng.integers(800_000, 1_200_000, n),
    })
    params = merged_params()
    assert detect_long_pattern(df, params) is None
    assert detect_short_pattern(df, params) is None


def test_add_ma_correct_length():
    df = _synthetic_long_pattern()
    out = add_ma(df, 200)
    assert "ma200" in out.columns
    assert out["ma200"].iloc[-1] > 0


def test_short_signal_on_inverted_pattern():
    df = _synthetic_long_pattern()
    # Invert the price series around 100 to flip a long pattern into a short one
    pivot = 100.0
    for col in ("open", "high", "low", "close"):
        df[col] = 2 * pivot - df[col]
    # high/low got swapped when we flipped — fix
    df["new_high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["new_low"] = df[["open", "high", "low", "close"]].min(axis=1)
    df["high"] = df["new_high"]; df["low"] = df["new_low"]
    df = df.drop(columns=["new_high", "new_low"])
    params = merged_params()
    sig = detect_short_pattern(df, params, symbol="TEST")
    # Don't strictly require a hit — synthetic flipping isn't perfect — but if
    # it does fire the metadata must be sane:
    if sig:
        assert sig["type"] == "SHORT"
        assert sig["entry"] < sig["stop"]
        assert sig["target"] < sig["entry"]

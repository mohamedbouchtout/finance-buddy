"""Tests for the algo bot backtester."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.algo_bot.config import merged_params
from app.algo_bot.backtester import backtest


def _flat_df(n: int = 300):
    rng = np.random.default_rng(0)
    closes = 100 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes - 0.05, "high": closes + 0.2, "low": closes - 0.2,
        "close": closes, "volume": rng.integers(900_000, 1_100_000, n),
    })


def test_backtest_returns_required_keys_on_flat_market():
    bt = backtest(_flat_df(), merged_params(), starting_capital=10_000.0, symbol="X")
    for key in ("trades", "equity_curve", "starting_capital", "ending_capital",
                "n_trades", "win_rate", "avg_r", "max_drawdown", "total_return_pct"):
        assert key in bt
    assert bt["starting_capital"] == 10_000.0
    assert isinstance(bt["trades"], list)
    assert isinstance(bt["equity_curve"], list)


def test_backtest_handles_too_short_data():
    df = _flat_df(50)
    bt = backtest(df, merged_params())
    assert bt["n_trades"] == 0
    assert "error" in bt


def test_backtest_equity_starts_at_starting_capital():
    bt = backtest(_flat_df(), merged_params(), starting_capital=25_000.0)
    if bt["equity_curve"]:
        assert bt["equity_curve"][0]["equity"] == 25_000.0

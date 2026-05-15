"""Tests for the Pro live-config zip export."""
from __future__ import annotations

import io
import json
import zipfile

from app.algo_bot.config import merged_params
from app.algo_bot.config_export import build_bundle


def test_bundle_is_a_valid_zip_with_expected_files():
    blob = build_bundle(merged_params(), ["AAPL", "MSFT"], plan="pro")
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = set(z.namelist())
    assert {"trading_params.json", "watchlist.json", "README.txt"} <= names

    params = json.loads(z.read("trading_params.json"))
    assert "strategy_retest_200ma" in params
    assert "risk_management" in params

    watch = json.loads(z.read("watchlist.json"))
    assert watch["tickers"] == ["AAPL", "MSFT"]
    assert watch["_meta"]["plan"] == "pro"

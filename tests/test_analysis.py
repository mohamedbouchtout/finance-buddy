"""Tests for the concentration analysis math.

These tests do NOT hit the network — we feed in synthetic prices directly.
"""
from app.analysis import Holding, analyze
from app.etf_holdings import MAG7


def test_single_stock_is_max_concentrated():
    a = analyze([Holding("AAPL", 10, price=200.0)])
    assert a.total_value == 2000.0
    assert a.score < 25
    assert a.hhi >= 0.99
    assert a.mag7_exposure > 0.99


def test_all_voo_is_well_diversified_but_mag7_heavy():
    a = analyze([Holding("VOO", 100, price=500.0)])
    assert a.total_value == 50000.0
    assert 0.30 < a.mag7_exposure < 0.40       # ~33% Mag 7 in S&P 500
    assert a.score > 25                         # better than single stock
    assert "Mag 7" in " ".join(a.insights) or "Magnificent" in " ".join(a.insights)


def test_balanced_portfolio_scores_higher_than_concentrated():
    concentrated = analyze([
        Holding("AAPL", 50, price=200.0),
        Holding("MSFT", 50, price=400.0),
        Holding("NVDA", 50, price=120.0),
    ])
    balanced = analyze([
        Holding("VT", 100, price=120.0),   # global stocks
        Holding("BND", 100, price=75.0),   # bonds
        Holding("GLD", 50, price=200.0),   # gold
        Holding("IBIT", 20, price=50.0),   # crypto
    ])
    assert balanced.score > concentrated.score
    assert balanced.mag7_exposure < concentrated.mag7_exposure


def test_etf_look_through_aggregates_mag7():
    # Hold both VOO and AAPL — Mag 7 exposure should reflect both
    a = analyze([
        Holding("VOO", 100, price=500.0),    # ~33% Mag 7 → 16.5% of total
        Holding("AAPL", 100, price=200.0),    # 100% Mag 7 → 6.7% of total (20000/70000)
    ])
    # Total = 50000 + 20000 = 70000
    # AAPL direct: 20000/70000 = 0.286
    # VOO AAPL: 0.069 * 50000/70000 = 0.0493
    # So AAPL alone is ~33% effective. Mag 7 total should be ~40-50%.
    assert a.mag7_exposure > 0.40
    aapl_eff = dict(a.single_stock_top).get("AAPL", 0)
    assert aapl_eff > 0.30


def test_bond_only_portfolio_no_mag7():
    a = analyze([Holding("BND", 100, price=75.0)])
    assert a.mag7_exposure == 0
    assert "US Bonds" in a.asset_class_mix


def test_zero_value_portfolio_returns_empty():
    a = analyze([Holding("AAPL", 0, price=200.0)])
    assert a.total_value == 0
    assert a.score == 0


def test_unknown_ticker_with_no_price_is_warned():
    a = analyze([Holding("UNKNOWN_TICKER_XYZ", 10, price=None)])
    assert any("UNKNOWN_TICKER_XYZ" in w for w in a.warnings)


def test_score_bands_are_sensible():
    a_bad = analyze([Holding("NVDA", 100, price=120.0)])
    a_good = analyze([
        Holding("VTI", 50, price=240.0),
        Holding("VXUS", 50, price=60.0),
        Holding("BND", 50, price=75.0),
        Holding("GLD", 20, price=200.0),
        Holding("VNQ", 30, price=90.0),
    ])
    assert a_bad.score < a_good.score
    assert a_good.score >= 40
    assert "concentrated" in a_bad.score_band.lower() or a_bad.score < 30


def test_mag7_set_includes_expected_tickers():
    assert "AAPL" in MAG7 and "MSFT" in MAG7 and "GOOGL" in MAG7 and "GOOG" in MAG7
    assert len(MAG7) == 8  # 7 companies but GOOG + GOOGL = 8 tickers


def test_position_weights_sum_to_one():
    a = analyze([
        Holding("AAPL", 10, price=200.0),
        Holding("MSFT", 5, price=400.0),
        Holding("VOO", 3, price=500.0),
    ])
    total_w = sum(p["weight"] for p in a.positions)
    assert abs(total_w - 1.0) < 1e-9


def test_sector_mix_normalized():
    a = analyze([
        Holding("VOO", 50, price=500.0),
        Holding("BND", 50, price=75.0),
    ])
    s = sum(a.sector_mix.values())
    assert abs(s - 1.0) < 0.01

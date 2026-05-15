"""The heart of the product: turn a list of (ticker, shares) into a
diversification analysis, with ETF look-through to the underlying holdings.

Pure functions, no I/O — easy to test.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from app.etf_holdings import MAG7, get_etf, is_known_etf


# How many holdings to treat the "rest" of each ETF as, for HHI purposes.
# Real ETF holdings counts: S&P 500 = 503, Total Market = 3700, Nasdaq 100 = 101,
# International = ~8000, Sector ETFs = ~70, REITs = ~150, Dividend = ~100.
_REST_SPREAD_BY_CATEGORY = {
    "Large Cap Blend": 490,
    "Total Market": 3990,
    "Large Cap Growth": 90,
    "Large Cap Value": 340,
    "Dividend": 90,
    "Total International": 7990,
    "Developed Markets": 4090,
    "Emerging Markets": 990,
    "Total World": 9590,
    "REITs": 150,
    "Sector — Tech": 60,
    "Sector — Financials": 70,
    "Sector — Energy": 20,
    "Sector — Health Care": 60,
    "Thematic — Innovation": 25,
}
_DEFAULT_REST_SPREAD = 100


def _rest_spread_for(meta: dict) -> int:
    return _REST_SPREAD_BY_CATEGORY.get(meta.get("category", ""), _DEFAULT_REST_SPREAD)


# ----- input -----
@dataclass
class Holding:
    ticker: str
    shares: float
    price: float | None = None  # filled by caller before analysis

    @property
    def market_value(self) -> float:
        return (self.price or 0.0) * self.shares


# ----- output -----
@dataclass
class Analysis:
    total_value: float
    positions: List[dict] = field(default_factory=list)          # {ticker, value, weight, is_etf, name}
    asset_class_mix: Dict[str, float] = field(default_factory=dict)
    sector_mix: Dict[str, float] = field(default_factory=dict)
    region_mix: Dict[str, float] = field(default_factory=dict)
    effective_holdings: Dict[str, float] = field(default_factory=dict)  # after look-through
    mag7_exposure: float = 0.0
    single_stock_top: List[tuple[str, float]] = field(default_factory=list)
    hhi: float = 0.0                       # Herfindahl index on effective holdings (0..1)
    score: int = 0                         # 0..100, higher is better diversified
    score_band: str = ""                   # 'Highly concentrated' | 'Concentrated' | 'Moderate' | 'Diversified' | 'Highly diversified'
    insights: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --- helpers ---
def _normalize(d: Dict[str, float]) -> Dict[str, float]:
    s = sum(d.values())
    if s <= 0:
        return d
    return {k: v / s for k, v in d.items()}


def _bucket_add(d: Dict[str, float], key: str, weight: float) -> None:
    if not key:
        return
    d[key] = d.get(key, 0.0) + weight


def _band(score: int) -> str:
    if score < 25: return "Highly concentrated"
    if score < 45: return "Concentrated"
    if score < 65: return "Moderately diversified"
    if score < 85: return "Diversified"
    return "Highly diversified"


def analyze(raw_holdings: List[Holding]) -> Analysis:
    """Compute concentration metrics for a portfolio."""
    a = Analysis(total_value=0.0)

    # Aggregate duplicates by ticker
    by_ticker: Dict[str, Holding] = {}
    for h in raw_holdings:
        t = (h.ticker or "").upper().strip()
        if not t or h.shares <= 0:
            continue
        if t in by_ticker:
            by_ticker[t].shares += h.shares
        else:
            by_ticker[t] = Holding(ticker=t, shares=h.shares, price=h.price)

    holdings = list(by_ticker.values())
    for h in holdings:
        if h.price is None or h.price <= 0:
            a.warnings.append(f"No price found for {h.ticker} — excluded from analysis.")
    holdings = [h for h in holdings if h.price and h.price > 0]

    total = sum(h.market_value for h in holdings)
    a.total_value = total
    if total <= 0:
        a.warnings.append("Total portfolio value is zero. Add holdings to analyze.")
        return a

    # Position-level (top-line, NOT looked through)
    for h in holdings:
        meta = get_etf(h.ticker)
        a.positions.append({
            "ticker": h.ticker,
            "shares": h.shares,
            "price": h.price,
            "value": h.market_value,
            "weight": h.market_value / total,
            "is_etf": meta is not None,
            "name": meta["name"] if meta else h.ticker,
        })
    a.positions.sort(key=lambda p: -p["value"])

    # Look-through: build effective holdings.
    # For each ETF, we distribute the *named* top holdings as discrete buckets
    # and break the *unnamed remainder* into N synthetic equal-weight buckets so
    # that the HHI math correctly treats it as broad exposure (rather than a
    # single concentrated position).
    effective: Dict[str, float] = {}
    asset_class: Dict[str, float] = {}
    region: Dict[str, float] = {}
    sector: Dict[str, float] = {}

    for h in holdings:
        w = h.market_value / total
        meta = get_etf(h.ticker)
        if meta:
            _bucket_add(asset_class, meta["asset_class"], w)
            _bucket_add(region, meta["region"], w)
            for sec, sw in meta["sector_mix"].items():
                _bucket_add(sector, sec, w * sw)
            top = meta["top_holdings"]
            named_weight = sum(top.values())
            for inner_t, inner_w in top.items():
                _bucket_add(effective, inner_t, w * inner_w)
            rest = max(0.0, 1.0 - named_weight)
            if rest > 0:
                if named_weight == 0:
                    # No equity look-through (bonds, gold, BTC, etc.) — single bucket OK.
                    _bucket_add(effective, h.ticker, w)
                else:
                    n_spread = _rest_spread_for(meta)
                    per = (w * rest) / n_spread
                    for i in range(n_spread):
                        effective[f"{h.ticker}_rest_{i}"] = effective.get(f"{h.ticker}_rest_{i}", 0.0) + per
        else:
            # Treat unknown ticker as a single stock
            _bucket_add(effective, h.ticker, w)
            _bucket_add(asset_class, "US Equity (single stock)", w)
            _bucket_add(region, "Unknown", w)
            _bucket_add(sector, "Unknown / Single Stock", w)

    a.effective_holdings = effective
    a.asset_class_mix = _normalize(asset_class)
    a.region_mix = _normalize(region)
    a.sector_mix = _normalize(sector)

    # Mag 7 exposure: sum effective weights of mag7 tickers
    mag7 = sum(w for t, w in effective.items() if t in MAG7)
    a.mag7_exposure = mag7

    # Top single-stock exposures (named ones, ignoring synthetic "_rest_N" buckets)
    named_effective = {t: w for t, w in effective.items() if "_rest_" not in t}
    a.single_stock_top = sorted(named_effective.items(), key=lambda x: -x[1])[:10]

    # HHI on full effective distribution (including _rest buckets — they represent diffuse exposure)
    a.hhi = sum(w * w for w in effective.values())

    # ---------- Score (0..100, higher = more diversified) ----------
    # Components:
    #  - HHI: lower is better. HHI of 1.0 = 1 stock; HHI of 0.01 = 100 equal stocks.
    #  - Mag 7 exposure: penalize above 25%.
    #  - Asset-class breadth: reward broad mixing across stocks/bonds/commodities/crypto/real estate.
    #  - Top single-stock concentration: penalize any single name >10%.
    hhi_score = max(0.0, 1.0 - (a.hhi - 0.01) / (0.5 - 0.01)) * 100
    hhi_score = max(0.0, min(100.0, hhi_score))

    mag7_penalty = max(0.0, (mag7 - 0.25)) * 200  # every 1pt above 25% costs 2 points
    mag7_penalty = min(40.0, mag7_penalty)

    # Asset class diversity: entropy normalized to [0,1] over up to 6 buckets
    ac_weights = list(a.asset_class_mix.values())
    entropy = -sum(w * math.log(w) for w in ac_weights if w > 0)
    max_entropy = math.log(min(6, max(2, len(ac_weights))))
    ac_score = (entropy / max_entropy) * 100 if max_entropy > 0 else 0
    ac_score = max(0.0, min(100.0, ac_score))

    top_single = a.single_stock_top[0][1] if a.single_stock_top else 0.0
    single_penalty = max(0.0, (top_single - 0.10)) * 200
    single_penalty = min(30.0, single_penalty)

    raw = 0.45 * hhi_score + 0.40 * ac_score - mag7_penalty - single_penalty
    a.score = int(max(0, min(100, round(raw))))
    a.score_band = _band(a.score)

    # ---------- Insights (educational, NOT advice) ----------
    if mag7 >= 0.30:
        a.insights.append(
            f"Your portfolio has {mag7*100:.1f}% effective exposure to the Magnificent 7 "
            "(AAPL, MSFT, NVDA, AMZN, META, GOOGL/GOOG, TSLA). If you also hold S&P 500 or Nasdaq "
            "index funds, you may be more concentrated in mega-cap tech than you realize."
        )
    elif mag7 >= 0.20:
        a.insights.append(
            f"Mag 7 exposure is {mag7*100:.1f}%. This is close to S&P 500 weighting — typical, "
            "but worth knowing if you thought you were diversifying with extra tech holdings."
        )

    if top_single >= 0.15:
        t, w = a.single_stock_top[0]
        a.insights.append(
            f"Your largest single-stock exposure is {t} at {w*100:.1f}% (looked through ETFs). "
            "Single-name risk above 15% materially increases portfolio volatility."
        )

    if "US Bonds" not in a.asset_class_mix and "International Bonds" not in a.asset_class_mix:
        a.insights.append(
            "No bond exposure detected. Bonds historically reduce portfolio drawdowns during equity crashes."
        )

    if "Commodities" not in a.asset_class_mix:
        a.insights.append(
            "No commodities exposure (e.g. gold). Commodities can act as an inflation and geopolitical hedge."
        )

    us_weight = sum(w for r, w in a.region_mix.items() if r == "US")
    if us_weight >= 0.95 and a.total_value > 0:
        a.insights.append(
            f"Your equity exposure is {us_weight*100:.1f}% US. International exposure historically "
            "improves long-term risk-adjusted returns through geographic diversification."
        )

    tech_weight = a.sector_mix.get("Technology", 0.0)
    if tech_weight >= 0.40:
        a.insights.append(
            f"Technology sector weight is {tech_weight*100:.1f}%. Higher than S&P 500 (~30%) — "
            "your portfolio is unusually tech-tilted."
        )

    return a

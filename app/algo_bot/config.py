"""Default parameters for the 200MA retest strategy. Pro users can override."""
from copy import deepcopy

DEFAULT_PARAMS = {
    "strategy_retest_200ma": {
        "ma_period": 200,
        "ma_slope_period": 20,
        "min_uptrend_slope": -0.01,
        "max_downtrend_slope": 0.01,
        "risk_reward_ratio": 2.0,
        "stop_loss_pct": 0.03,
        "lookback_days": 250,
        "min_breakout_volume": 1.7,
        "min_breakout_strength": 0.7,
        "min_bounce_strength": 0.02,
        "max_retest_volume_ratio": 0.5,
        "max_retest_volume_absolute": 0.8,
        "max_days_since_retest": 3,
        "retest_distance": 0.005,
    },
    "risk_management": {
        "risk_per_trade_pct": 0.05,
        "max_investment_pct": 0.70,
        "max_positions": 10,
    },
}


def merged_params(overrides: dict | None = None) -> dict:
    out = deepcopy(DEFAULT_PARAMS)
    if not overrides:
        return out
    for section, vals in overrides.items():
        if section in out and isinstance(vals, dict):
            out[section].update(vals)
        else:
            out[section] = vals
    return out

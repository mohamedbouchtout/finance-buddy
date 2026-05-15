"""Historical backtest for the 200MA retest strategy."""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .strategy import detect_signal
from .ai_predictor import predict as ai_predict


def backtest(df: pd.DataFrame, params: Dict, starting_capital: float = 10_000.0,
             symbol: str = "") -> Dict:
    p_strat = params["strategy_retest_200ma"]
    p_risk = params["risk_management"]
    ma_period = p_strat["ma_period"]
    start_i = ma_period + 20

    if len(df) < start_i + 30:
        return {
            "trades": [], "equity_curve": [], "starting_capital": starting_capital,
            "ending_capital": starting_capital, "n_trades": 0, "win_rate": 0.0,
            "avg_r": 0.0, "max_drawdown": 0.0, "total_return_pct": 0.0,
            "error": "Not enough data for backtest (need ~250 bars)",
        }

    equity = starting_capital
    trades: List[Dict] = []
    equity_points: List[Dict] = [{"date": str(df.iloc[start_i]["date"])[:10], "equity": equity}]
    open_position = None
    seen_signal_dates = set()

    for i in range(start_i, len(df)):
        bar = df.iloc[i]
        if open_position:
            hi = float(bar["high"]); lo = float(bar["low"])
            entry = open_position["entry"]; stop = open_position["stop"]
            target = open_position["target"]; direction = open_position["type"]
            shares = open_position["shares"]
            exit_price = None; exit_reason = None
            if direction == "LONG":
                if lo <= stop:
                    exit_price = stop; exit_reason = "stop"
                elif hi >= target:
                    exit_price = target; exit_reason = "target"
            else:
                if hi >= stop:
                    exit_price = stop; exit_reason = "stop"
                elif lo <= target:
                    exit_price = target; exit_reason = "target"

            if exit_price is not None:
                if direction == "LONG":
                    pnl = (exit_price - entry) * shares
                else:
                    pnl = (entry - exit_price) * shares
                equity += pnl
                r_multiple = pnl / open_position["risk_dollars"] if open_position["risk_dollars"] > 0 else 0
                trades.append({
                    **open_position,
                    "exit": float(exit_price),
                    "exit_date": str(bar["date"])[:10],
                    "exit_reason": exit_reason,
                    "pnl": float(pnl),
                    "r_multiple": float(r_multiple),
                    "equity_after": float(equity),
                })
                open_position = None

        equity_points.append({"date": str(bar["date"])[:10], "equity": float(equity)})

        if open_position is None:
            slice_df = df.iloc[:i + 1]
            sig = detect_signal(slice_df, params, symbol=symbol)
            if sig and sig["current_date"] not in seen_signal_dates:
                seen_signal_dates.add(sig["current_date"])
                risk_per_share = abs(sig["entry"] - sig["stop"])
                if risk_per_share <= 0:
                    continue
                risk_dollars = equity * p_risk["risk_per_trade_pct"]
                shares = int(risk_dollars / risk_per_share)
                if shares <= 0:
                    continue
                cost = shares * sig["entry"]
                if cost > equity * p_risk["max_investment_pct"]:
                    shares = int((equity * p_risk["max_investment_pct"]) / sig["entry"])
                    if shares <= 0:
                        continue
                open_position = {
                    "type": sig["type"], "symbol": sig["symbol"],
                    "entry": float(sig["entry"]), "stop": float(sig["stop"]),
                    "target": float(sig["target"]), "shares": shares,
                    "entry_date": str(bar["date"])[:10],
                    "risk_dollars": float(shares * risk_per_share),
                }

    if not trades:
        win_rate = 0.0; avg_r = 0.0
    else:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = wins / len(trades)
        avg_r = float(np.mean([t["r_multiple"] for t in trades]))

    equities = [pt["equity"] for pt in equity_points]
    peak = equities[0]; max_dd = 0.0
    for e in equities:
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

    return {
        "trades": trades,
        "equity_curve": equity_points,
        "starting_capital": starting_capital,
        "ending_capital": float(equity),
        "n_trades": len(trades),
        "win_rate": float(win_rate),
        "avg_r": float(avg_r),
        "max_drawdown": float(max_dd),
        "total_return_pct": float((equity - starting_capital) / starting_capital * 100),
    }


def backtest_ai(df: pd.DataFrame, params: Dict, starting_capital: float = 10_000.0,
                symbol: str = "", score_threshold: float = 65.0,
                cooldown_bars: int = 5, eval_every: int = 3) -> Dict:
    """Walk-forward backtest using the pretrained AI predictor.

    For every `eval_every` bars after enough history we run `ai_predict` on the
    slice. A BULLISH score >= threshold opens a long; BEARISH <= (100-threshold)
    opens a short. Exits are stop or target (the predictor's own levels).
    A cooldown prevents immediately re-entering on the same setup.
    `eval_every` keeps cost bounded — exits are still checked every bar.
    """
    p_risk = params["risk_management"]
    min_history = 120  # ai_predictor needs ~60, but give it room for stable MAs
    start_i = min_history

    if len(df) < start_i + 30:
        return {
            "trades": [], "equity_curve": [], "starting_capital": starting_capital,
            "ending_capital": starting_capital, "n_trades": 0, "win_rate": 0.0,
            "avg_r": 0.0, "max_drawdown": 0.0, "total_return_pct": 0.0,
            "engine": "ai_predictor",
            "error": "Not enough data for AI backtest (need ~150 bars)",
        }

    equity = starting_capital
    trades: List[Dict] = []
    equity_points: List[Dict] = [{"date": str(df.iloc[start_i]["date"])[:10], "equity": equity}]
    open_position = None
    last_exit_idx = -10_000

    for i in range(start_i, len(df)):
        bar = df.iloc[i]

        if open_position:
            hi = float(bar["high"]); lo = float(bar["low"])
            entry = open_position["entry"]; stop = open_position["stop"]
            target = open_position["target"]; direction = open_position["type"]
            shares = open_position["shares"]
            exit_price = None; exit_reason = None
            if direction == "LONG":
                if lo <= stop:
                    exit_price = stop; exit_reason = "stop"
                elif hi >= target:
                    exit_price = target; exit_reason = "target"
            else:
                if hi >= stop:
                    exit_price = stop; exit_reason = "stop"
                elif lo <= target:
                    exit_price = target; exit_reason = "target"

            if exit_price is not None:
                pnl = (exit_price - entry) * shares if direction == "LONG" else (entry - exit_price) * shares
                equity += pnl
                r_multiple = pnl / open_position["risk_dollars"] if open_position["risk_dollars"] > 0 else 0
                trades.append({
                    **open_position,
                    "exit": float(exit_price),
                    "exit_date": str(bar["date"])[:10],
                    "exit_reason": exit_reason,
                    "pnl": float(pnl),
                    "r_multiple": float(r_multiple),
                    "equity_after": float(equity),
                })
                open_position = None
                last_exit_idx = i

        equity_points.append({"date": str(bar["date"])[:10], "equity": float(equity)})

        if open_position is None and (i - last_exit_idx) >= cooldown_bars and (i % eval_every == 0):
            slice_df = df.iloc[:i + 1]
            pred = ai_predict(slice_df, symbol=symbol)
            if not pred:
                continue
            score = pred["score"]
            direction = None
            if pred["direction"] == "BULLISH" and score >= score_threshold:
                direction = "LONG"
            elif pred["direction"] == "BEARISH" and score <= (100.0 - score_threshold):
                direction = "SHORT"
            if direction is None:
                continue

            entry = float(pred["entry"]); stop = float(pred["stop"]); target = float(pred["target"])
            risk_per_share = abs(entry - stop)
            if risk_per_share <= 0:
                continue
            risk_dollars = equity * p_risk["risk_per_trade_pct"]
            shares = int(risk_dollars / risk_per_share)
            if shares <= 0:
                continue
            cost = shares * entry
            if cost > equity * p_risk["max_investment_pct"]:
                shares = int((equity * p_risk["max_investment_pct"]) / entry)
                if shares <= 0:
                    continue
            open_position = {
                "type": direction, "symbol": symbol,
                "entry": entry, "stop": stop, "target": target, "shares": shares,
                "entry_date": str(bar["date"])[:10],
                "score": float(score), "setup_type": pred["setup_type"],
                "confidence": pred["confidence"],
                "risk_dollars": float(shares * risk_per_share),
            }

    if not trades:
        win_rate = 0.0; avg_r = 0.0
    else:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        win_rate = wins / len(trades)
        avg_r = float(np.mean([t["r_multiple"] for t in trades]))

    equities = [pt["equity"] for pt in equity_points]
    peak = equities[0]; max_dd = 0.0
    for e in equities:
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        if dd > max_dd: max_dd = dd

    return {
        "trades": trades,
        "equity_curve": equity_points,
        "starting_capital": starting_capital,
        "ending_capital": float(equity),
        "n_trades": len(trades),
        "win_rate": float(win_rate),
        "avg_r": float(avg_r),
        "max_drawdown": float(max_dd),
        "total_return_pct": float((equity - starting_capital) / starting_capital * 100),
        "engine": "ai_predictor",
        "score_threshold": float(score_threshold),
    }

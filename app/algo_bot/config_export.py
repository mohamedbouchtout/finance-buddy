"""Generate a downloadable bot configuration bundle for Pro users."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_LICENSE_CHECK_PATH = Path(__file__).parent / "_license_check_template.py"


INSTRUCTIONS = """\
Finance Buddy - Algo Signals Bot Config
=============================================
Generated: {ts}
Licensed to: {email}
License key: {license_key}

This bundle was generated for your subscription and contains everything needed
to run the AI-driven signal bot against your live Interactive Brokers account
using the open-source Algo-Bot.

WHAT'S IN THE BOX
-----------------
- trading_params.json   - Strategy + risk + AI parameters (drop in <algo-bot>/config/)
- watchlist.json        - The ticker universe you scanned
- license.json          - Your subscription license (do NOT share)
- license_check.py      - Heartbeat client — verifies your subscription is active
- README.txt            - This file

INSTALL
-------
1. Open your local Algo-Bot repo (e.g. C:\\git repos\\Algo-Bot).
2. Replace `config/trading_params.json` with the one in this bundle.
3. Copy `license.json` and `license_check.py` into the algo-bot root folder.
4. At the top of your `main.py`, add:

       from license_check import verify
       verify(strict=True)   # exits the process if subscription is inactive

5. Save `watchlist.json` somewhere reachable; point your StockTickerFetcher
   at it, or paste the symbols into your existing fetcher.
6. Make sure IB Gateway / TWS is running and accepting API connections.
7. From the algo-bot directory: `python main.py`

LICENSE / SUBSCRIPTION
----------------------
- The bot phones home to {verify_url} on startup and refuses to run if your
  subscription is inactive.
- A 24-hour offline grace window applies if the license server is unreachable.
- Cancelling or letting your subscription lapse will revoke this key within
  minutes. You can regenerate a new key from your dashboard after re-subscribing.
- Do not share this bundle. Each license is tied to your account.

RISK CONTROLS IN THIS CONFIG
----------------------------
- Risk per trade: {risk_per_trade}% of account equity
- Max simultaneous positions: {max_positions}
- Max account deployed: {max_invest}%
- Stop loss: {stop_loss}% beyond retest extreme
- Risk:Reward ratio: 1:{rr}
- AI confidence threshold: {ai_conf}

THIS IS NOT INVESTMENT ADVICE
-----------------------------
You are responsible for verifying every signal and every trade.
Finance Buddy provides a tool, not advice. Past performance does
not guarantee future results. Always test in IB paper mode first.
"""


def _read_license_check_script() -> str:
    return _LICENSE_CHECK_PATH.read_text(encoding="utf-8")


def build_bundle(
    params: Dict,
    tickers: List[str],
    plan: str,
    *,
    license_key: Optional[str] = None,
    verify_url: str = "",
    user_email: str = "",
) -> bytes:
    p_strat = params["strategy_retest_200ma"]
    p_risk = params["risk_management"]
    p_ai = params.get("ai_analyzer", {"confidence_threshold": 0.8})

    readme = INSTRUCTIONS.format(
        ts=_utcnow_iso(),
        email=user_email or "(your account)",
        license_key=license_key or "(none — paper mode)",
        verify_url=verify_url or "(not set)",
        risk_per_trade=int(p_risk["risk_per_trade_pct"] * 100),
        max_positions=p_risk["max_positions"],
        max_invest=int(p_risk["max_investment_pct"] * 100),
        stop_loss=int(p_strat["stop_loss_pct"] * 100),
        rr=p_strat["risk_reward_ratio"],
        ai_conf=p_ai.get("confidence_threshold", 0.8),
    )

    watchlist = {
        "_meta": {
            "generated_at": _utcnow_iso(),
            "plan": plan,
            "n_tickers": len(tickers),
        },
        "tickers": [t.upper() for t in tickers],
    }

    bot_params = {
        "strategy_retest_200ma": p_strat,
        "ai_analyzer": {
            "confidence_threshold": p_ai.get("confidence_threshold", 0.8),
            "risk_reward_ratio": p_strat["risk_reward_ratio"],
            "stop_loss_pct": p_strat["stop_loss_pct"],
            "lookback_days": 2000,
        },
        "risk_management": p_risk,
        "timing": {"scan_interval": 1800, "market_check_interval": 900},
    }

    license_file = {
        "key": license_key or "",
        "verify_url": verify_url,
        "issued_at": _utcnow_iso(),
        "user_email": user_email,
        "heartbeat_interval_seconds": 1800,
        "offline_grace_hours": 24,
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("trading_params.json", json.dumps(bot_params, indent=2))
        z.writestr("watchlist.json", json.dumps(watchlist, indent=2))
        z.writestr("license.json", json.dumps(license_file, indent=2))
        z.writestr("license_check.py", _read_license_check_script())
        z.writestr("README.txt", readme)
    return buf.getvalue()


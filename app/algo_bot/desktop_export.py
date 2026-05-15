"""Build a self-contained Algo Bot desktop application zip for Pro members.

The zip is the *runtime* of the desktop app: it contains the GUI script
(bot_app.py), the algo_bot package (ai_predictor, strategy, scanner, config),
the user's personal license + heartbeat client, a frozen copy of trading_params
and watchlist, the signed liability waiver, a Windows launcher and a README.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

PKG_ROOT = Path(__file__).resolve().parent          # app/algo_bot
APP_ROOT = PKG_ROOT.parent                          # app
PROJECT_ROOT = APP_ROOT.parent                      # repo root

DESKTOP_DIR = PKG_ROOT / "desktop"
LICENSE_CHECK_TEMPLATE = PKG_ROOT / "_license_check_template.py"
WAIVER_PATH = APP_ROOT / "legal" / "waiver.txt"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Files copied verbatim from app/algo_bot into the bundle's `algo_bot/` folder.
_ALGO_BOT_FILES = ["__init__.py", "ai_predictor.py", "scanner.py", "strategy.py", "config.py"]


README_TEMPLATE = """\
Finance Buddy — Algo Bot Desktop Application
===================================================
Generated: {ts}
Licensed to: {email}
License key: {license_key}

This bundle contains everything you need to run the AI Algo Bot locally on your
own machine, with a graphical user interface. Paper trading works out of the
box; live trading requires Interactive Brokers (TWS or IB Gateway) and the
optional `ib_insync` Python package.

QUICK START (Windows)
---------------------
1. Unzip this archive anywhere (e.g. C:\\CC-AlgoBot).
2. Make sure Python 3.10+ is installed and on your PATH.
3. Double-click `run.bat` — it will install dependencies the first time and
   launch the GUI. (Or run `python bot_app.py` from a terminal.)

QUICK START (macOS / Linux)
---------------------------
1. Unzip the archive.
2. `pip install -r requirements.txt`
3. `python3 bot_app.py`

USING THE APP
-------------
- The **Signal Matrix** tab shows every ticker the bot scored on the most
  recent scan, sorted by conviction (distance from the neutral 50 score).
- The **Config** tab has sliders for risk per trade, AI confidence threshold,
  max simultaneous positions, and the scan interval. Edits take effect on the
  next scan after you press **Save config**. The same tab has a watchlist
  editor.
- The **Positions & P&L** tab tracks open positions and cumulative trades.
- The **Activity Log** tab shows scan + license + order messages.
- **Start / Stop** controls the background scan loop. Switch **Mode** to
  **Live (IB)** and click **Connect IB…** to route orders to your Interactive
  Brokers paper or live account.

LICENSE
-------
The bot phones home to {verify_url} every 30 minutes. If your subscription
lapses, the next heartbeat will fail and the bot will refuse to place new
trades. You can also revoke or regenerate your license at any time from your
account dashboard.

LIABILITY WAIVER
----------------
This bundle includes `WAIVER.txt` — the same waiver you accepted online when
you upgraded to Pro. By unzipping and running this software you reaffirm your
acceptance of those terms. We are not liable for trading losses.

RISK CONTROLS (current defaults in this bundle)
-----------------------------------------------
- Risk per trade: {risk_per_trade}% of account equity
- Max simultaneous positions: {max_positions}
- AI confidence threshold: {ai_conf}
- Scan interval: {scan_interval} seconds

THIS IS NOT INVESTMENT ADVICE. Past performance is not predictive. Always test
in IB paper mode for at least several weeks before connecting real money.
"""


RUN_BAT = """\
@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo Python is not installed or not on PATH. Install Python 3.10+ from python.org.
    pause
    exit /b 1
)
if not exist ".deps_installed" (
    echo Installing dependencies (first run only)...
    python -m pip install --disable-pip-version-check -r requirements.txt || goto :deperr
    echo done > .deps_installed
)
python bot_app.py
if errorlevel 1 (
    echo.
    echo The bot exited with an error. See bot_app_error.log in this folder for details.
    pause
)
goto :eof
:deperr
echo Failed to install dependencies.
pause
exit /b 2
"""

RUN_SH = """\
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.10+." >&2
  exit 1
fi
if [ ! -f .deps_installed ]; then
  echo "Installing dependencies (first run only)..."
  python3 -m pip install --disable-pip-version-check -r requirements.txt
  touch .deps_installed
fi
exec python3 bot_app.py
"""

REQUIREMENTS = """\
numpy>=1.26
pandas>=2.0
yfinance>=0.2.40
# Optional — only needed for Live mode against Interactive Brokers.
# ib_insync>=0.9.86
"""


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def build_desktop_bundle(
    params: Dict,
    tickers: List[str],
    *,
    license_key: Optional[str] = None,
    verify_url: str = "",
    user_email: str = "",
    waiver_accepted_at: Optional[int] = None,
    waiver_version: str = "",
) -> bytes:
    """Return the bytes of a zip containing the full desktop app."""
    p_strat = params["strategy_retest_200ma"]
    p_risk = params["risk_management"]
    p_ai = params.get("ai_analyzer", {"confidence_threshold": 0.7})
    timing = params.get("timing", {"scan_interval": 1800})

    bot_params = {
        "strategy_retest_200ma": p_strat,
        "ai_analyzer": {
            "confidence_threshold": p_ai.get("confidence_threshold", 0.7),
            "risk_reward_ratio": p_strat["risk_reward_ratio"],
            "stop_loss_pct": p_strat["stop_loss_pct"],
            "lookback_days": 2000,
        },
        "risk_management": p_risk,
        "timing": timing,
    }

    watchlist = {
        "_meta": {
            "generated_at": _utcnow_iso(),
            "n_tickers": len(tickers),
        },
        "tickers": [t.upper() for t in tickers],
    }

    license_file = {
        "key": license_key or "",
        "verify_url": verify_url,
        "issued_at": _utcnow_iso(),
        "user_email": user_email,
        "heartbeat_interval_seconds": 1800,
        "offline_grace_hours": 24,
    }

    waiver_meta = {
        "version": waiver_version,
        "accepted_at": waiver_accepted_at,
        "accepted_at_iso": (
            datetime.fromtimestamp(waiver_accepted_at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if waiver_accepted_at else None
        ),
        "user_email": user_email,
    }

    readme = README_TEMPLATE.format(
        ts=_utcnow_iso(),
        email=user_email or "(your account)",
        license_key=(license_key[:6] + "…" + license_key[-4:]) if license_key and len(license_key) > 12 else (license_key or "(none)"),
        verify_url=verify_url or "(not set)",
        risk_per_trade=round(p_risk["risk_per_trade_pct"] * 100, 2),
        max_positions=p_risk["max_positions"],
        ai_conf=p_ai.get("confidence_threshold", 0.7),
        scan_interval=timing.get("scan_interval", 1800),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # GUI app
        z.writestr("bot_app.py", _read(DESKTOP_DIR / "bot_app.py"))
        # algo_bot package (only the runtime modules — no FastAPI server code)
        z.writestr("algo_bot/__init__.py", "")
        for fname in _ALGO_BOT_FILES:
            if fname == "__init__.py":
                continue
            z.writestr(f"algo_bot/{fname}", _read(PKG_ROOT / fname))
        # License heartbeat
        z.writestr("license_check.py", _read(LICENSE_CHECK_TEMPLATE))
        # User-specific data
        z.writestr("license.json", json.dumps(license_file, indent=2))
        z.writestr("trading_params.json", json.dumps(bot_params, indent=2))
        z.writestr("watchlist.json", json.dumps(watchlist, indent=2))
        z.writestr("waiver_acceptance.json", json.dumps(waiver_meta, indent=2))
        # Launchers + docs
        z.writestr("run.bat", RUN_BAT)
        z.writestr("run.sh", RUN_SH)
        z.writestr("requirements.txt", REQUIREMENTS)
        z.writestr("README.txt", readme)
        if WAIVER_PATH.exists():
            z.writestr("WAIVER.txt", _read(WAIVER_PATH))
    return buf.getvalue()

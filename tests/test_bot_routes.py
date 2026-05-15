"""End-to-end tests for the algo bot routes."""
from __future__ import annotations

import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import time

from app.main import app
from app.auth import COOKIE_NAME, make_session_cookie
from app.storage import upsert_user, update_subscription, record_waiver_acceptance, get_user


def _fake_df(n: int = 300, base: float = 100.0):
    rng = np.random.default_rng(0)
    closes = base + np.cumsum(rng.normal(0, 0.2, n))
    return pd.DataFrame({
        "date": pd.date_range("2022-01-01", periods=n, freq="B"),
        "open": closes - 0.05, "high": closes + 0.3, "low": closes - 0.3,
        "close": closes, "volume": rng.integers(900_000, 1_100_000, n),
        "symbol": "MOCK",
    })


@pytest.fixture
def client(monkeypatch):
    """A TestClient with yfinance stubbed so no network calls happen."""
    def fake_fetch(symbol, period="2y"):
        df = _fake_df()
        df["symbol"] = symbol.upper()
        return df
    monkeypatch.setattr("app.main.fetch_ohlcv", fake_fetch)
    monkeypatch.setattr("app.algo_bot.scanner.fetch_ohlcv", fake_fetch)
    return TestClient(app)


def _login_as_pro(client: TestClient, email: str = "pro@example.com", accept_waiver: bool = True):
    upsert_user(email)
    update_subscription(
        email, stripe_customer_id="cus_test", plan="pro_monthly",
        status="active", period_end=int(time.time()) + 86400,
    )
    if accept_waiver:
        u = get_user(email)
        if u:
            record_waiver_acceptance(u["id"], ip="127.0.0.1")
    client.cookies.set(COOKIE_NAME, make_session_cookie(email))


def test_bot_landing_renders(client):
    r = client.get("/bot")
    assert r.status_code == 200
    assert "Algo Signals" in r.text or "Algo" in r.text


def test_bot_scan_form_renders(client):
    r = client.get("/bot/scan")
    assert r.status_code == 200
    assert "Run scan" in r.text or "scan" in r.text.lower()


def test_bot_scan_requires_tickers(client):
    r = client.post("/bot/scan", data={"tickers_text": ""})
    assert r.status_code == 200
    assert "at least one ticker" in r.text.lower()


def test_bot_scan_free_user_capped_at_10(client):
    tickers = " ".join([f"T{i}" for i in range(20)])
    r = client.post("/bot/scan", data={"tickers_text": tickers})
    assert r.status_code == 200
    # warning text appears
    assert "Free plan" in r.text or "Upgrade" in r.text


def test_bot_scan_runs_for_pro_with_no_cap_warning(client):
    _login_as_pro(client)
    tickers = " ".join([f"T{i}" for i in range(30)])
    r = client.post("/bot/scan", data={"tickers_text": tickers})
    assert r.status_code == 200
    assert "Free plan" not in r.text


def test_bot_live_setup_redirects_for_free(client):
    # Anonymous: goes to login
    r = client.get("/bot/live-setup", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/login" in r.headers.get("location", "")


def test_bot_live_setup_renders_for_pro(client):
    _login_as_pro(client)
    r = client.get("/bot/live-setup")
    assert r.status_code == 200
    assert "Live trading" in r.text or "trading setup" in r.text.lower()


def test_bot_live_config_download_blocked_for_free(client):
    r = client.post("/bot/live-config", data={"tickers_json": '["AAPL"]'}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/pricing" in r.headers.get("location", "")


def test_bot_live_config_download_zip_for_pro(client):
    _login_as_pro(client, email="pro2@example.com")
    r = client.post("/bot/live-config", data={"tickers_json": '["AAPL","MSFT"]'})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert "trading_params.json" in names
    assert "watchlist.json" in names
    params = json.loads(z.read("trading_params.json"))
    assert "strategy_retest_200ma" in params


# ---------------------------------------------------------------------------
# Waiver flow
# ---------------------------------------------------------------------------

def test_waiver_page_renders(client):
    r = client.get("/waiver")
    assert r.status_code == 200
    assert "Liability Waiver" in r.text
    assert "ASSUMPTION OF RISK" in r.text


def test_waiver_accept_requires_login(client):
    r = client.post("/waiver/accept", data={"confirm": "yes"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/login" in r.headers.get("location", "")


def test_waiver_accept_records_and_redirects(client):
    _login_as_pro(client, email="waiver@example.com", accept_waiver=False)
    r = client.post(
        "/waiver/accept",
        data={"confirm": "yes", "next": "/bot/app", "version": "1.0-2026-05"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303, 307)
    assert r.headers.get("location") == "/bot/app"
    u = get_user("waiver@example.com")
    assert u and u.get("waiver_accepted_at")
    assert u.get("waiver_version") == "1.0-2026-05"


# ---------------------------------------------------------------------------
# Desktop app gating + bundle contents
# ---------------------------------------------------------------------------

def test_bot_app_page_requires_login(client):
    r = client.get("/bot/app", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/login" in r.headers.get("location", "")


def test_bot_app_page_requires_pro(client):
    upsert_user("free@example.com")
    client.cookies.set(COOKIE_NAME, make_session_cookie("free@example.com"))
    r = client.get("/bot/app", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/pricing" in r.headers.get("location", "")


def test_bot_app_download_blocked_without_waiver(client):
    _login_as_pro(client, email="nowaiver@example.com", accept_waiver=False)
    r = client.post("/bot/app/download", data={"tickers_text": "AAPL MSFT"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert "/waiver" in r.headers.get("location", "")


def test_bot_app_download_zip_for_pro_with_waiver(client):
    _login_as_pro(client, email="appuser@example.com")
    r = client.post("/bot/app/download", data={"tickers_text": "AAPL MSFT NVDA"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    # Core runtime
    assert "bot_app.py" in names
    assert "license_check.py" in names
    assert "license.json" in names
    assert "trading_params.json" in names
    assert "watchlist.json" in names
    # Legal + launchers + docs
    assert "WAIVER.txt" in names
    assert "waiver_acceptance.json" in names
    assert "run.bat" in names
    assert "run.sh" in names
    assert "requirements.txt" in names
    assert "README.txt" in names
    # algo_bot package files
    for f in ("algo_bot/__init__.py", "algo_bot/ai_predictor.py",
              "algo_bot/scanner.py", "algo_bot/strategy.py", "algo_bot/config.py"):
        assert f in names, f
    # bot_app.py is syntactically valid Python
    compile(z.read("bot_app.py").decode("utf-8"), "bot_app.py", "exec")
    # Watchlist contains the tickers we posted
    wl = json.loads(z.read("watchlist.json"))
    assert set(wl["tickers"]) == {"AAPL", "MSFT", "NVDA"}
    # Waiver acceptance metadata captured
    wa = json.loads(z.read("waiver_acceptance.json"))
    assert wa["user_email"] == "appuser@example.com"
    assert wa["accepted_at"] is not None


def test_bot_results_page_has_no_mojibake(client):
    """The results page used to render `â€"` instead of an em dash. Regression guard."""
    _login_as_pro(client, email="utf8@example.com")
    r = client.post("/bot/scan", data={"tickers_text": "AAPL MSFT"})
    assert r.status_code == 200
    assert "â€" not in r.text
    assert "â†" not in r.text

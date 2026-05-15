"""End-to-end tests using FastAPI TestClient.

Network is mocked via monkeypatching market_data.get_prices so tests are
deterministic and don't hit Yahoo Finance.
"""
import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.main import app


@pytest.fixture(autouse=True)
def stub_prices(monkeypatch):
    """Stub price fetching to a deterministic table."""
    fake_prices = {
        "VOO": 500.0, "QQQ": 450.0, "VTI": 240.0, "VXUS": 60.0,
        "BND": 75.0, "GLD": 200.0, "IBIT": 50.0,
        "AAPL": 200.0, "MSFT": 400.0, "NVDA": 120.0, "TSLA": 250.0,
    }
    def _get_prices(tickers):
        return {t.upper(): fake_prices.get(t.upper()) for t in tickers}
    monkeypatch.setattr(app_main, "get_prices", _get_prices)


client = TestClient(app)


def test_landing_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "Finance Buddy" in r.text
    assert "S&amp;P 500" in r.text or "S&P 500" in r.text


def test_analyze_form_renders():
    r = client.get("/analyze")
    assert r.status_code == 200
    assert "Analyze your portfolio" in r.text


def test_pricing_renders():
    r = client.get("/pricing")
    assert r.status_code == 200
    assert "$9" in r.text and "$79" in r.text


def test_healthcheck():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_analyze_submit_happy_path():
    r = client.post("/analyze", data={"holdings_text": "VOO 100\nBND 50\nGLD 10"})
    assert r.status_code == 200
    assert "/100" in r.text
    assert "Magnificent 7" in r.text
    assert "Asset Class Mix" in r.text


def test_analyze_submit_validation_error():
    r = client.post("/analyze", data={"holdings_text": ""})
    assert r.status_code == 200
    assert "at least one holding" in r.text.lower()


def test_api_analyze_endpoint():
    r = client.post("/api/analyze", json={"holdings": [["VOO", 100], ["BND", 50]]})
    assert r.status_code == 200
    data = r.json()
    assert "score" in data
    assert 0 <= data["score"] <= 100
    assert data["mag7_exposure"] > 0
    assert "US Equity" in data["asset_class_mix"]
    assert "US Bonds" in data["asset_class_mix"]


def test_api_analyze_requires_holdings():
    r = client.post("/api/analyze", json={"holdings": []})
    assert r.status_code == 400


def test_login_flow_sends_link(capsys):
    r = client.post("/login", data={"email": "tester@example.com", "next": "/dashboard"})
    assert r.status_code == 200
    assert "Check your email" in r.text
    captured = capsys.readouterr()
    assert "tester@example.com" in (captured.out + captured.err)


def test_dashboard_requires_login():
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_dev_mode_checkout_auto_upgrades(monkeypatch):
    # Sign in via magic-link flow
    from app.auth import make_session_cookie
    from app.storage import upsert_user
    upsert_user("buyer@example.com")
    cookie = make_session_cookie("buyer@example.com")
    client.cookies.set("cc_session", cookie)

    r = client.post("/billing/checkout", data={"plan": "monthly"}, follow_redirects=False)
    assert r.status_code == 303
    # In dev mode we redirect to /billing/success?dev=1
    assert "billing/success" in r.headers["location"]

    # Now user should be Pro
    from app.storage import get_user, user_is_pro
    u = get_user("buyer@example.com")
    assert user_is_pro(u) is True

    client.cookies.clear()


def test_pdf_export_requires_pro(monkeypatch):
    r = client.post("/export/pdf",
                    data={"holdings_json": '[["VOO",10]]', "name": "Test"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "/pricing" in r.headers["location"]


def test_pdf_export_works_for_pro_user():
    from app.auth import make_session_cookie
    from app.storage import upsert_user, update_subscription
    import time
    upsert_user("pdfpro@example.com")
    update_subscription(
        "pdfpro@example.com",
        stripe_customer_id="cus_test",
        plan="pro_monthly",
        status="active",
        period_end=int(time.time()) + 86400,
    )
    client.cookies.set("cc_session", make_session_cookie("pdfpro@example.com"))
    r = client.post("/export/pdf",
                    data={"holdings_json": '[["VOO",10],["BND",5]]', "name": "Roth"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    client.cookies.clear()

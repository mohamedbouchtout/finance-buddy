"""Tests for bot license issuance, verification, and API endpoint."""
from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import COOKIE_NAME, make_session_cookie
from app.licensing import (
    ensure_license, get_active_license, issue_license,
    regenerate_license, revoke_all_for_user, verify,
)
from app.storage import upsert_user, update_subscription, record_waiver_acceptance


@pytest.fixture
def pro_user():
    email = "lic-pro@example.com"
    user = upsert_user(email)
    update_subscription(
        email, stripe_customer_id="cus_lic_test", plan="pro_monthly",
        status="active", period_end=int(time.time()) + 86400,
    )
    return upsert_user(email)  # refresh


@pytest.fixture
def free_user():
    email = "lic-free@example.com"
    return upsert_user(email)


@pytest.fixture
def client():
    return TestClient(app)


def _login(client: TestClient, email: str):
    client.cookies.set(COOKIE_NAME, make_session_cookie(email))


# ----- core licensing module -----

def test_issue_license_creates_active_key(pro_user):
    lic = issue_license(pro_user["id"])
    assert lic["key"].startswith("CC-")
    assert lic["status"] == "active"
    assert lic["user_id"] == pro_user["id"]


def test_ensure_license_returns_existing(pro_user):
    a = ensure_license(pro_user["id"])
    b = ensure_license(pro_user["id"])
    assert a["key"] == b["key"]


def test_regenerate_license_rotates_key(pro_user):
    a = issue_license(pro_user["id"])
    b = regenerate_license(pro_user["id"])
    assert a["key"] != b["key"]
    # Old key should now be revoked
    resp = verify(a["key"])
    assert resp["valid"] is False
    # New key is active
    resp2 = verify(b["key"])
    assert resp2["valid"] is True


def test_verify_active_license_for_pro(pro_user):
    lic = issue_license(pro_user["id"])
    resp = verify(lic["key"], machine_id="machine-A", ip="127.0.0.1")
    assert resp["valid"] is True
    assert resp["plan"] == "pro_monthly"
    # heartbeat recorded
    fresh = get_active_license(pro_user["id"])
    assert fresh["machine_id"] == "machine-A"
    assert fresh["last_seen_at"] is not None


def test_verify_rejects_unknown_key():
    resp = verify("CC-AAAAAA-BBBBBB-CCCCCC-DDDDDD")
    assert resp["valid"] is False
    assert resp["status"] == "unknown"


def test_verify_rejects_malformed_key():
    resp = verify("hax0r")
    assert resp["valid"] is False
    assert resp["status"] == "invalid"


def test_verify_rejects_after_subscription_canceled(pro_user):
    lic = issue_license(pro_user["id"])
    # Cancel the subscription
    update_subscription(
        pro_user["email"], stripe_customer_id="cus_lic_test", plan="free",
        status="canceled", period_end=None,
    )
    resp = verify(lic["key"])
    assert resp["valid"] is False
    assert resp["status"] in ("no_subscription", "revoked")


def test_revoke_all_for_user_blocks_verify(pro_user):
    lic = issue_license(pro_user["id"])
    n = revoke_all_for_user(pro_user["id"])
    assert n >= 1
    resp = verify(lic["key"])
    assert resp["valid"] is False
    assert resp["status"] == "revoked"


# ----- API endpoint -----

def test_api_license_verify_valid(client, pro_user):
    lic = issue_license(pro_user["id"])
    r = client.post("/api/license/verify", json={"key": lic["key"], "machine_id": "test-mc"})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["plan"] == "pro_monthly"


def test_api_license_verify_unknown_is_403(client):
    r = client.post("/api/license/verify", json={"key": "CC-DEADBE-EFDEAD-BEEFDE-ADBEEF"})
    assert r.status_code == 403
    assert r.json()["valid"] is False


def test_api_license_verify_missing_key(client):
    r = client.post("/api/license/verify", json={})
    assert r.status_code == 403
    assert r.json()["valid"] is False


# ----- /account/license page -----

def test_account_license_requires_login(client):
    client.cookies.clear()
    r = client.get("/account/license", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_account_license_requires_pro(client, free_user):
    _login(client, free_user["email"])
    r = client.get("/account/license", follow_redirects=False)
    assert r.status_code == 303
    assert "/pricing" in r.headers["location"]


def test_account_license_shows_key_for_pro(client, pro_user):
    _login(client, pro_user["email"])
    r = client.get("/account/license")
    assert r.status_code == 200
    assert "CC-" in r.text  # key is displayed
    assert "Regenerate" in r.text


def test_account_license_regenerate_creates_new_key(client, pro_user):
    _login(client, pro_user["email"])
    first = ensure_license(pro_user["id"])
    r = client.post("/account/license/regenerate", follow_redirects=False)
    assert r.status_code == 303
    second = get_active_license(pro_user["id"])
    assert second["key"] != first["key"]


def test_account_license_revoke_kills_active(client, pro_user):
    _login(client, pro_user["email"])
    ensure_license(pro_user["id"])
    r = client.post("/account/license/revoke", follow_redirects=False)
    assert r.status_code == 303
    assert get_active_license(pro_user["id"]) is None


# ----- bundle contents -----

def test_bot_config_download_includes_license(client, pro_user):
    _login(client, pro_user["email"])
    record_waiver_acceptance(pro_user["id"], ip="127.0.0.1")
    r = client.post("/bot/live-config", data={"tickers_json": json.dumps(["AAPL", "MSFT"])})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert "trading_params.json" in names
    assert "watchlist.json" in names
    assert "license.json" in names
    assert "license_check.py" in names
    assert "README.txt" in names
    lic_blob = json.loads(z.read("license.json"))
    assert lic_blob["key"].startswith("CC-")
    assert "/api/license/verify" in lic_blob["verify_url"]
    assert lic_blob["user_email"] == pro_user["email"]
    # license_check.py must be valid python
    src = z.read("license_check.py").decode("utf-8")
    compile(src, "license_check.py", "exec")


def test_bot_config_download_blocked_for_non_pro(client, free_user):
    _login(client, free_user["email"])
    r = client.post(
        "/bot/live-config",
        data={"tickers_json": json.dumps(["AAPL"])},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/pricing" in r.headers["location"]

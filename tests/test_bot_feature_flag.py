"""Ensure bot routes are gated by ENABLE_BOT_UI."""
from fastapi.testclient import TestClient

from app import config as cfg
from app.main import app

client = TestClient(app)

BOT_PATHS = [
    "/bot",
    "/bot/scan",
    "/bot/app",
    "/waiver",
    "/account/license",
]


def test_bot_routes_visible_when_flag_on():
    # conftest sets ENABLE_BOT_UI=1, so flag should already be on.
    assert cfg.ENABLE_BOT_UI is True
    r = client.get("/bot/scan")
    # 200 (rendered) — not 404
    assert r.status_code == 200


def test_bot_routes_404_when_flag_off(monkeypatch):
    monkeypatch.setattr(cfg, "ENABLE_BOT_UI", False)
    for p in BOT_PATHS:
        r = client.get(p, follow_redirects=False)
        assert r.status_code == 404, f"{p} should 404 when ENABLE_BOT_UI is off (got {r.status_code})"


def test_landing_hides_bot_cta_when_flag_off(monkeypatch):
    monkeypatch.setattr(cfg, "ENABLE_BOT_UI", False)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Try the algo bot" not in body
    assert "Algo Bot" not in body


def test_landing_shows_bot_cta_when_flag_on():
    r = client.get("/")
    assert r.status_code == 200
    assert "Try the algo bot" in r.text

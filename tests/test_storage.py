"""Tests for storage layer."""
import time
from app import storage


def test_user_upsert_idempotent():
    u1 = storage.upsert_user("test@example.com")
    u2 = storage.upsert_user("test@example.com")
    assert u1["id"] == u2["id"]
    assert u1["plan"] == "free"


def test_magic_token_lifecycle():
    storage.upsert_user("magic@example.com")
    storage.create_magic_token("magic@example.com", "TOKEN_A", ttl_seconds=60)
    email = storage.consume_magic_token("TOKEN_A")
    assert email == "magic@example.com"
    # second consumption fails
    assert storage.consume_magic_token("TOKEN_A") is None


def test_magic_token_expires():
    storage.upsert_user("expired@example.com")
    storage.create_magic_token("expired@example.com", "TOKEN_B", ttl_seconds=-10)
    assert storage.consume_magic_token("TOKEN_B") is None


def test_subscription_update_marks_pro():
    storage.upsert_user("pro@example.com")
    storage.update_subscription(
        "pro@example.com",
        stripe_customer_id="cus_xxx",
        plan="pro_monthly",
        status="active",
        period_end=int(time.time()) + 86400,
    )
    u = storage.get_user("pro@example.com")
    assert storage.user_is_pro(u) is True

    storage.update_subscription(
        "pro@example.com",
        stripe_customer_id="cus_xxx",
        plan="free",
        status="canceled",
        period_end=None,
    )
    u = storage.get_user("pro@example.com")
    assert storage.user_is_pro(u) is False


def test_portfolio_save_and_fetch():
    u = storage.upsert_user("port@example.com")
    pid = storage.save_portfolio(u["id"], "Roth", [("VOO", 10), ("BND", 5)])
    p = storage.get_portfolio(pid, u["id"])
    assert p["name"] == "Roth"
    assert {h["ticker"] for h in p["holdings"]} == {"VOO", "BND"}


def test_price_cache_roundtrip():
    storage.set_cached_price("ZZZZ", 42.5)
    c = storage.get_cached_price("ZZZZ")
    assert c["price"] == 42.5
    assert c["fetched_at"] > 0


def test_event_logging():
    storage.log_event("test_event", "payload")
    counts = storage.event_counts(60)
    assert "test_event" in counts and counts["test_event"] >= 1

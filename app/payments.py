"""Stripe payments — Checkout Session + Webhook handling.

Works in test mode automatically (use Stripe TEST keys in .env).
Falls back to a 'mock' flow in dev when no Stripe key is configured.
"""
from __future__ import annotations

import logging
import time
from typing import Tuple

from app.config import (
    APP_BASE_URL,
    DEV_MODE,
    STRIPE_PRICE_MONTHLY,
    STRIPE_PRICE_YEARLY,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from app.storage import (
    get_user,
    get_user_by_customer,
    update_subscription,
    upsert_user,
)

log = logging.getLogger(__name__)

_stripe = None
def _stripe_client():
    global _stripe
    if _stripe is None and STRIPE_SECRET_KEY:
        import stripe  # type: ignore
        stripe.api_key = STRIPE_SECRET_KEY
        _stripe = stripe
    return _stripe


def create_checkout_session(email: str, plan: str) -> Tuple[str, str]:
    """Returns (checkout_url, mode). mode is 'stripe' or 'dev'."""
    email = email.strip().lower()
    upsert_user(email)
    price_id = STRIPE_PRICE_MONTHLY if plan == "monthly" else STRIPE_PRICE_YEARLY

    s = _stripe_client()
    if not s or not price_id:
        # Dev fallback: instantly "upgrade" the user
        log.info("[DEV] No Stripe configured — auto-upgrading %s to %s", email, plan)
        plan_value = "pro_monthly" if plan == "monthly" else "pro_yearly"
        period = int(time.time()) + (30 * 86400 if plan == "monthly" else 365 * 86400)
        update_subscription(
            email,
            stripe_customer_id=f"dev_cust_{email}",
            plan=plan_value,
            status="active",
            period_end=period,
        )
        return (f"{APP_BASE_URL}/billing/success?dev=1", "dev")

    user = get_user(email)
    customer_kwargs = {}
    if user and user.get("stripe_customer_id"):
        customer_kwargs["customer"] = user["stripe_customer_id"]
    else:
        customer_kwargs["customer_email"] = email

    session = s.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{APP_BASE_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{APP_BASE_URL}/pricing",
        allow_promotion_codes=True,
        **customer_kwargs,
        metadata={"app_email": email, "plan": plan},
    )
    return (session.url, "stripe")


def create_billing_portal_session(stripe_customer_id: str) -> str | None:
    s = _stripe_client()
    if not s or not stripe_customer_id:
        return None
    portal = s.billing_portal.Session.create(
        customer=stripe_customer_id,
        return_url=f"{APP_BASE_URL}/dashboard",
    )
    return portal.url


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process Stripe webhook events. Returns a dict for logging."""
    s = _stripe_client()
    if not s:
        return {"ok": False, "error": "Stripe not configured"}

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = s.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            log.error("Webhook signature verification failed: %s", e)
            return {"ok": False, "error": "invalid signature"}
    else:
        import json
        event = json.loads(payload)

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        email = (data.get("customer_email")
                 or data.get("metadata", {}).get("app_email")
                 or "").lower()
        plan_meta = data.get("metadata", {}).get("plan", "monthly")
        plan_value = "pro_monthly" if plan_meta == "monthly" else "pro_yearly"
        if email:
            update_subscription(
                email,
                stripe_customer_id=data.get("customer"),
                plan=plan_value,
                status="active",
                period_end=None,
            )
        return {"ok": True, "handled": etype, "email": email}

    if etype in ("customer.subscription.updated", "customer.subscription.created"):
        cust = data.get("customer")
        user = get_user_by_customer(cust) if cust else None
        if user:
            price = (data.get("items", {}).get("data", [{}])[0]
                     .get("price", {}).get("id", ""))
            plan_value = "pro_yearly" if price == STRIPE_PRICE_YEARLY else "pro_monthly"
            new_status = data.get("status", "active")
            update_subscription(
                user["email"],
                stripe_customer_id=cust,
                plan=plan_value,
                status=new_status,
                period_end=data.get("current_period_end"),
            )
            # If sub isn't active anymore (past_due/unpaid/incomplete_expired), kill bot access.
            if new_status != "active":
                from app.licensing import revoke_all_for_user
                revoke_all_for_user(user["id"])
        return {"ok": True, "handled": etype}

    if etype == "customer.subscription.deleted":
        cust = data.get("customer")
        user = get_user_by_customer(cust) if cust else None
        if user:
            update_subscription(
                user["email"],
                stripe_customer_id=cust,
                plan="free",
                status="canceled",
                period_end=data.get("current_period_end"),
            )
            from app.licensing import revoke_all_for_user
            revoke_all_for_user(user["id"])
        return {"ok": True, "handled": etype}

    return {"ok": True, "ignored": etype}

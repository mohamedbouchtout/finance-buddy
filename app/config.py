"""Centralized config loaded from environment with sane dev defaults."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")
APP_SECRET = os.getenv("APP_SECRET", "dev-secret-change-me")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY", "")
STRIPE_PRICE_YEARLY = os.getenv("STRIPE_PRICE_YEARLY", "")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "hello@financebuddy.com")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")

# Feature flags
# Default OFF on main — the Algo Bot lives on the `algo-bot` branch and only
# returns to the public site once it has a live track record. Set
# ENABLE_BOT_UI=1 locally or on the algo-bot branch to expose it.
ENABLE_BOT_UI = os.getenv("ENABLE_BOT_UI", "0").lower() in ("1", "true", "yes", "on")

DB_PATH = DATA_DIR / "app.db"
ETF_DATA_PATH = DATA_DIR / "etfs.json"

DEV_MODE = not STRIPE_SECRET_KEY  # If Stripe not configured, we're in dev

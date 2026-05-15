# Finance Buddy

> **You think you own the S&P 500. You actually own 7 tech stocks.**
> Paste your holdings, see your true diversification score with full ETF look-through.
> Then turn on the algo signal scanner.

Educational investing tool. Two products in one:

1. **Portfolio analyzer** — Mag-7 exposure %, HHI, sector/asset-class look-through, 0–100 diversification score
2. **Algo Signals (AI)** — self-detecting pattern engine using RBM + CNN-style scoring layered on a 200MA breakout-and-retest framework with volume, MA-slope and risk/reward filters. Free: paper-mode scanner + historical backtest. Pro: a **downloadable desktop app** (Tkinter GUI — start/stop, live signal matrix, risk + AI-confidence sliders, paper or Interactive Brokers live mode) plus a licensed bot bundle for the open-source CLI Algo-Bot.

### Pro liability waiver
Live-trading software for retail users carries real risk. The first time a Pro subscriber visits `/bot/app` (or tries to download any bundle) they're sent through `/waiver` — a full assumption-of-risk, release, and indemnification agreement. We record their email, IP, version, and timestamp, store the same metadata inside every downloaded zip (`waiver_acceptance.json` + `WAIVER.txt`), and refuse downloads until acceptance.

### Bot licensing & subscription enforcement
Pro bundles include a `license.json` key + `license_check.py` heartbeat script. The bot phones home to `/api/license/verify` on startup and every 30 min. The moment a subscription is canceled (Stripe webhook → `customer.subscription.deleted` / `past_due`), all of that user's licenses are revoked and the next heartbeat fails — the bot exits. Users can also regenerate/revoke manually from `/account/license`. 24-hour offline grace window so brief network blips don't kill the bot.

Pro subscription is $9/mo or $79/yr (save portfolios, CSV import, PDF reports, 200-ticker scans, licensed bot bundle).

---

## Stack
- **Python 3.12+** · **FastAPI** · **Jinja2** · **Tailwind (CDN)** · **Chart.js (CDN)**
- **SQLite** (zero-ops persistence)
- **yfinance** (free market data, 15-min cache + OHLCV history for the scanner)
- **Stripe** (Checkout + webhooks)
- **ReportLab** (PDF export)

## Quick start (local)

```powershell
cd C:\Users\bouchtom\.copilot\session-state\03918fa1-c0e6-4759-9a69-5aca86b7f99e\files\finance-buddy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # edit as needed; works empty for dev
uvicorn app.main:app --reload
```

Open http://localhost:8000

### Dev mode shortcuts
With no Stripe keys configured:
- Magic-link emails print to the console (look for `📧 [DEV]`)
- The Pro upgrade flow auto-completes without payment

This lets you exercise the entire UX without setting up any external services.

## Run the tests

```powershell
pytest -v
```

Includes:
- Unit tests for the concentration math (`tests/test_analysis.py`)
- Storage / auth round-trips (`tests/test_storage.py`)
- CSV parser tests (`tests/test_csv_import.py`)
- End-to-end FastAPI route tests (`tests/test_e2e.py`)

## Going live — checklist

See `LAUNCH_CHECKLIST.md` for the full launch process. TL;DR:
1. Register a domain (e.g., `financebuddy.com`)
2. Create a Stripe account → make 2 recurring prices (monthly $9, yearly $79)
3. Set up SMTP (SendGrid free tier is fine for <100/day) or Resend
4. Deploy via Docker to Fly.io / Railway / Render
5. Set env vars from `.env.example`
6. Add Stripe webhook → point to `https://yourdomain.com/webhooks/stripe`
7. Launch on r/investing, r/Bogleheads, X/Twitter

## Project layout
```
app/                 — FastAPI app + analysis engine
  main.py            — Routes
  analysis.py        — Concentration math + scoring
  etf_holdings.py    — ETF look-through loader
  market_data.py     — yfinance with SQLite cache
  storage.py         — SQLite schema & queries
  auth.py            — Magic-link auth
  payments.py        — Stripe Checkout + webhook
  pdf_report.py      — PDF export
  csv_import.py      — Broker CSV parser
templates/           — Jinja2 templates
static/              — Minimal CSS
data/                — etfs.json + app.db (created on first run)
tests/               — pytest suite
```

## License
Proprietary — all rights reserved. Not investment advice.

# Finance Buddy

> **You think you own the S&P 500. You actually own 7 tech stocks.**
> Paste your holdings, see your true diversification score with full ETF look-through.

Free, educational portfolio analyzer for retail investors.

- **Mag-7 exposure %** — see how much of your portfolio is really in just 7 companies
- **Full ETF look-through** — decomposes the top 40 retail ETFs into their underlying holdings
- **Sector / asset class breakdown** — re-aggregated across stocks *and* funds
- **0–100 diversification score** — one number that tells you the truth
- **Free forever for one-off analysis.** Pro ($9/mo or $79/yr) adds saved portfolios, CSV import from any broker, and PDF reports.

> Educational tool, not investment advice. See [DISCLAIMER.md](./DISCLAIMER.md).

## Branches

- **`main`** — the public product: free portfolio analyzer. This is what gets deployed.
- **`algo-bot`** — work-in-progress AI signal scanner + downloadable desktop trading app. Hidden from `main` behind the `ENABLE_BOT_UI` feature flag until it has a public live-money track record.
- **`v0.1-full-stack`** — tag snapshotting the full original build (analyzer + algo bot together).

To experiment with the algo bot locally on `main`, set `ENABLE_BOT_UI=1` in your `.env`. To work on it for real, check out the `algo-bot` branch.

---

## Stack
- **Python 3.12+** · **FastAPI** · **Jinja2** · **Tailwind (CDN)** · **Chart.js (CDN)**
- **SQLite** (zero-ops persistence)
- **yfinance** (free market data, 15-min cache)
- **Stripe** (Checkout + webhooks)
- **ReportLab** (PDF export)

## Quick start (local)

```powershell
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
[MIT](./LICENSE) — see also [DISCLAIMER.md](./DISCLAIMER.md). Not investment advice.

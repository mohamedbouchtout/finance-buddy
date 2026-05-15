"""FastAPI app — routes, page rendering, and API."""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.algo_bot.backtester import backtest as algo_backtest, backtest_ai as algo_backtest_ai
from app.algo_bot.config import merged_params
from app.algo_bot.config_export import build_bundle as build_bot_bundle
from app.algo_bot.desktop_export import build_desktop_bundle
from app.algo_bot.scanner import fetch_ohlcv, scan_tickers, scan_tickers_ai
from app.analysis import Holding, analyze
from app.auth import (
    COOKIE_MAX_AGE, COOKIE_NAME, current_user,
    make_session_cookie, send_magic_link, verify_magic_token,
)
from app.config import (
    APP_BASE_URL, ADMIN_EMAIL, DEV_MODE, ENABLE_BOT_UI,
    STRIPE_PRICE_MONTHLY, STRIPE_PRICE_YEARLY, ROOT,
)
from app.csv_import import parse_csv
from app.legal import WAIVER_VERSION, waiver_text
from app.licensing import (
    ensure_license, get_active_license, list_licenses,
    regenerate_license, revoke_all_for_user, verify as verify_license,
)
from app.market_data import get_prices
from app.payments import (
    create_billing_portal_session, create_checkout_session, handle_webhook,
)
from app.pdf_report import render_pdf
from app.storage import (
    delete_portfolio, event_counts, get_portfolio, get_user_by_id,
    has_accepted_waiver, list_portfolios, log_event, record_waiver_acceptance,
    save_portfolio, user_is_pro,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Finance Buddy")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


# When the Algo Bot UI is disabled (default on `main`), 404 every bot-flavored
# route so the public surface is analyzer-only. The bot code stays in the tree
# (open source / re-enabled on the `algo-bot` branch) but is unreachable here.
_BOT_PREFIXES = ("/bot", "/waiver", "/account/license", "/api/license")


@app.middleware("http")
async def _gate_bot_routes(request: Request, call_next):
    # Re-read flag at request time so tests that monkey-patch config still work.
    from app import config as _cfg
    if not _cfg.ENABLE_BOT_UI:
        path = request.url.path
        if any(path == p or path.startswith(p + "/") for p in _BOT_PREFIXES):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


# ---------- helpers ----------
def _ctx(request: Request, **extra) -> dict:
    user = current_user(request)
    # Re-read flag at request time so test overrides take effect.
    from app import config as _cfg
    return {
        "user": user,
        "is_pro": user_is_pro(user),
        "waiver_accepted": has_accepted_waiver(user),
        "base_url": APP_BASE_URL,
        "dev_mode": DEV_MODE,
        "bot_enabled": _cfg.ENABLE_BOT_UI,
        **extra,
    }


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, _ctx(request, **ctx))


def _parse_holdings_form(text: str) -> list[tuple[str, float]]:
    """Parse the textarea input on /analyze.
    Accepts lines like: 'AAPL 10', 'AAPL,10', 'AAPL: 10', or CSV header."""
    if "," in text and any(h in text.lower() for h in ("symbol", "ticker", "quantity", "shares")):
        return parse_csv(text)
    out = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (",", "\t", ":", " "):
            if sep in line:
                parts = [p.strip() for p in line.split(sep) if p.strip()]
                break
        else:
            parts = [line]
        if len(parts) < 2:
            continue
        ticker = parts[0].upper()
        qty_str = parts[1].replace(",", "").replace("$", "")
        try:
            qty = float(qty_str)
        except ValueError:
            continue
        if qty > 0:
            out.append((ticker, qty))
    return out


def _run_analysis(holdings: list[tuple[str, float]]):
    tickers = list({t for t, _ in holdings})
    prices = get_prices(tickers)
    raw = [Holding(ticker=t, shares=s, price=prices.get(t.upper())) for t, s in holdings]
    return analyze(raw)


# ---------- pages ----------
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    log_event("page_view", "landing")
    return _render(request, "landing.html")


@app.get("/analyze", response_class=HTMLResponse)
def analyze_form(request: Request):
    log_event("page_view", "analyze")
    return _render(request, "analyze.html")


@app.post("/analyze", response_class=HTMLResponse)
async def analyze_submit(
    request: Request,
    holdings_text: str = Form(""),
    csv_file: Optional[UploadFile] = File(None),
):
    holdings: list[tuple[str, float]] = []
    if csv_file is not None and csv_file.filename:
        content = (await csv_file.read()).decode("utf-8", errors="replace")
        holdings = parse_csv(content)
    if not holdings and holdings_text.strip():
        holdings = _parse_holdings_form(holdings_text)

    if not holdings:
        return _render(request, "analyze.html", error="Please enter at least one holding (e.g. 'VOO 50').")

    analysis = _run_analysis(holdings)
    log_event("analysis_run", json.dumps({"n_positions": len(holdings), "score": analysis.score}))

    response = _render(
        request, "results.html",
        analysis=analysis, holdings=holdings, holdings_json=json.dumps(holdings),
    )
    response.set_cookie(
        "cc_last", json.dumps(holdings), max_age=86400,
        httponly=False, samesite="lax",
    )
    return response


@app.get("/share/{score}", response_class=HTMLResponse)
def share_card(request: Request, score: int):
    band = "Highly diversified" if score >= 85 else ("Diversified" if score >= 65 else (
        "Moderate" if score >= 45 else ("Concentrated" if score >= 25 else "Highly concentrated")))
    return _render(request, "share_card.html", score=score, band=band)


@app.get("/pricing", response_class=HTMLResponse)
def pricing(request: Request):
    return _render(request, "pricing.html")


# ---------- auth ----------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/dashboard", error: str = ""):
    return _render(request, "login.html", next=next, error=error, sent=False)


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(...), next: str = Form("/dashboard")):
    email = email.strip().lower()
    if "@" not in email or "." not in email:
        return _render(request, "login.html", next=next, error="Invalid email.", sent=False)
    send_magic_link(email)
    log_event("magic_link_sent", email)
    return _render(request, "login.html", next=next, error="", sent=True, sent_to=email)


@app.get("/auth/verify")
def auth_verify(token: str, next: str = "/dashboard"):
    email = verify_magic_token(token)
    if not email:
        return RedirectResponse(url="/login?error=Invalid+or+expired+link", status_code=303)
    resp = RedirectResponse(url=next, status_code=303)
    resp.set_cookie(
        COOKIE_NAME, make_session_cookie(email),
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    log_event("login_success", email)
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------- dashboard / Pro ----------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/dashboard", status_code=303)
    portfolios = list_portfolios(user["id"])
    return _render(request, "dashboard.html", portfolios=portfolios)


@app.post("/portfolios/save")
def portfolio_save(request: Request, name: str = Form(...), holdings_json: str = Form(...)):
    user = current_user(request)
    if not user:
        raise HTTPException(401)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=save", status_code=303)
    holdings = json.loads(holdings_json)
    save_portfolio(user["id"], name, holdings)
    log_event("portfolio_saved", str(user["id"]))
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/portfolios/{pid}/delete")
def portfolio_delete(pid: int, request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401)
    delete_portfolio(pid, user["id"])
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/portfolios/{pid}", response_class=HTMLResponse)
def portfolio_view(pid: int, request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    p = get_portfolio(pid, user["id"])
    if not p:
        raise HTTPException(404)
    holdings = [(h["ticker"], h["shares"]) for h in p["holdings"]]
    analysis = _run_analysis(holdings)
    return _render(
        request, "results.html",
        analysis=analysis, holdings=holdings,
        holdings_json=json.dumps(holdings), portfolio_name=p["name"],
    )


@app.post("/export/pdf")
def export_pdf(request: Request, holdings_json: str = Form(...), name: str = Form("My Portfolio")):
    user = current_user(request)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=pdf", status_code=303)
    holdings = json.loads(holdings_json)
    analysis = _run_analysis(holdings)
    pdf_bytes = render_pdf(analysis, portfolio_name=name)
    log_event("pdf_exported", str(user["id"]) if user else "")
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="finance-buddy-{name}.pdf"'},
    )


# ---------- billing ----------
@app.post("/billing/checkout")
def billing_checkout(request: Request, plan: str = Form("monthly")):
    user = current_user(request)
    if not user:
        # Save plan choice in query so we can redirect post-login
        return RedirectResponse(url=f"/login?next=/pricing", status_code=303)
    url, _mode = create_checkout_session(user["email"], plan)
    log_event("checkout_started", f"{user['email']}:{plan}")
    return RedirectResponse(url=url, status_code=303)


@app.get("/billing/success", response_class=HTMLResponse)
def billing_success(request: Request, session_id: str = "", dev: int = 0):
    log_event("checkout_success", session_id or ("dev" if dev else ""))
    return _render(request, "billing_success.html")


@app.post("/billing/portal")
def billing_portal(request: Request):
    user = current_user(request)
    if not user or not user.get("stripe_customer_id"):
        return RedirectResponse(url="/dashboard", status_code=303)
    url = create_billing_portal_session(user["stripe_customer_id"])
    return RedirectResponse(url=url or "/dashboard", status_code=303)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    result = handle_webhook(payload, sig)
    log_event("stripe_webhook", json.dumps(result))
    return JSONResponse(result)


# ---------- admin / health ----------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    user = current_user(request)
    if not user or (ADMIN_EMAIL and user["email"] != ADMIN_EMAIL.lower()):
        raise HTTPException(403)
    counts = event_counts(86400 * 7)
    return _render(request, "admin.html", counts=counts)


# ---------- API (handy for scripts) ----------
@app.post("/api/analyze")
def api_analyze(payload: dict):
    """POST {"holdings": [["AAPL", 10], ["VOO", 5]]} -> analysis JSON."""
    holdings = [(t, float(s)) for t, s in payload.get("holdings", [])]
    if not holdings:
        raise HTTPException(400, "Provide holdings: [[ticker, shares], ...]")
    a = _run_analysis(holdings)
    return {
        "score": a.score,
        "band": a.score_band,
        "total_value": a.total_value,
        "mag7_exposure": a.mag7_exposure,
        "hhi": a.hhi,
        "asset_class_mix": a.asset_class_mix,
        "sector_mix": a.sector_mix,
        "region_mix": a.region_mix,
        "single_stock_top": a.single_stock_top,
        "positions": a.positions,
        "insights": a.insights,
        "warnings": a.warnings,
    }


# ---------- algo bot ----------
FREE_MAX_TICKERS = 10
PRO_MAX_TICKERS = 200


def _parse_tickers(text: str) -> list[str]:
    raw = text.replace(",", " ").replace("\n", " ").replace("\t", " ")
    out, seen = [], set()
    for t in raw.split():
        t = t.strip().upper()
        if t and t not in seen and t.replace(".", "").replace("-", "").isalnum():
            out.append(t); seen.add(t)
    return out


@app.get("/bot", response_class=HTMLResponse)
def bot_landing(request: Request):
    log_event("page_view", "bot_landing")
    return _render(request, "bot_landing.html")


@app.get("/bot/scan", response_class=HTMLResponse)
def bot_scan_form(request: Request):
    user = current_user(request)
    cap = PRO_MAX_TICKERS if user_is_pro(user) else FREE_MAX_TICKERS
    return _render(request, "bot_scan.html", cap=cap, error="", default_tickers="AAPL MSFT NVDA GOOGL META AMZN TSLA SPY QQQ VOO")


@app.post("/bot/scan", response_class=HTMLResponse)
def bot_scan_submit(
    request: Request,
    tickers_text: str = Form(""),
    do_backtest: str = Form(""),
    backtest_symbol: str = Form(""),
):
    user = current_user(request)
    is_pro = user_is_pro(user)
    cap = PRO_MAX_TICKERS if is_pro else FREE_MAX_TICKERS

    tickers = _parse_tickers(tickers_text)
    if not tickers:
        return _render(request, "bot_scan.html", cap=cap, error="Enter at least one ticker.", default_tickers=tickers_text)
    if len(tickers) > cap:
        truncated = tickers[:cap]
        warn = f"Free plan limited to {cap} tickers — scanning only the first {cap}. Upgrade for up to {PRO_MAX_TICKERS}."
        tickers = truncated
    else:
        warn = ""

    params = merged_params()
    # Primary scan output: pretrained AI predictor — scores every ticker so the
    # free scan always produces useful results. The strict 200MA pattern detector
    # still runs for Pro users (high-conviction signals + downloadable bot bundle).
    ai_result = scan_tickers_ai(tickers)
    predictions = ai_result["predictions"]
    # Strict 200MA signals are a Pro-only overlay (kept for the live-bot bundle).
    if is_pro:
        result = scan_tickers(tickers, params)
        signals = result["signals"]
        scan_errors = result["errors"]
        scanned = max(ai_result["scanned"], result["scanned"])
    else:
        signals = []
        scan_errors = ai_result["errors"]
        scanned = ai_result["scanned"]

    # Optional: run a backtest on one ticker (Pro only) or, free, on first scanned ticker
    bt = None
    bt_symbol = ""
    if is_pro and backtest_symbol.strip():
        bt_symbol = backtest_symbol.strip().upper()
    elif do_backtest and tickers:
        bt_symbol = tickers[0]
    if bt_symbol:
        df = fetch_ohlcv(bt_symbol)
        if df is not None:
            # Use the AI predictor backtester so the metrics reflect the same
            # model that produced the predictions table above.
            bt = algo_backtest_ai(df, params, symbol=bt_symbol)

    log_event("bot_scan", json.dumps({
        "n": len(tickers), "predictions": len(predictions),
        "signals": len(signals), "pro": is_pro,
    }))

    return _render(
        request, "bot_results.html",
        tickers=tickers, predictions=predictions, signals=signals, errors=scan_errors,
        scanned=scanned, warn=warn, plan=("pro" if is_pro else "free"),
        backtest=bt, backtest_symbol=bt_symbol,
        tickers_json=json.dumps(tickers),
    )


@app.get("/bot/live-setup", response_class=HTMLResponse)
def bot_live_setup(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/bot/live-setup", status_code=303)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=bot_live", status_code=303)
    return _render(request, "bot_live_setup.html")


@app.post("/bot/live-config")
def bot_live_config(request: Request, tickers_json: str = Form("[]")):
    """Pro-only: download trading_params.json + watchlist + license + README as a zip."""
    user = current_user(request)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=bot_live", status_code=303)
    if not has_accepted_waiver(user):
        return RedirectResponse(url="/waiver?next=/bot/scan", status_code=303)
    try:
        tickers = json.loads(tickers_json) or []
    except Exception:
        tickers = []
    lic = ensure_license(user["id"])
    verify_url = f"{APP_BASE_URL.rstrip('/')}/api/license/verify"
    blob = build_bot_bundle(
        merged_params(), tickers, plan="pro",
        license_key=lic["key"], verify_url=verify_url, user_email=user["email"],
    )
    log_event("bot_config_downloaded", json.dumps({"user_id": user["id"], "license_id": lic["id"]}))
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="finance-buddy-bot-config.zip"'},
    )


# ---------- liability waiver ----------
@app.get("/waiver", response_class=HTMLResponse)
def waiver_page(request: Request, next: str = "/bot/app"):
    user = current_user(request)
    accepted_at_str = ""
    accepted_version = ""
    if user and has_accepted_waiver(user):
        # Re-fetch fresh row so the page reflects the latest acceptance details.
        u = get_user_by_id(user["id"]) or user
        ts = u.get("waiver_accepted_at")
        if ts:
            from datetime import datetime as _dt
            accepted_at_str = _dt.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M UTC")
        accepted_version = u.get("waiver_version") or ""
    return _render(
        request, "waiver.html",
        waiver_text=waiver_text(), version=WAIVER_VERSION,
        next=next, already_accepted=has_accepted_waiver(user),
        accepted_at_str=accepted_at_str, accepted_version=accepted_version,
    )


@app.post("/waiver/accept")
def waiver_accept(
    request: Request,
    confirm: str = Form(""),
    next: str = Form("/bot/app"),
    version: str = Form(WAIVER_VERSION),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url=f"/login?next=/waiver", status_code=303)
    if confirm != "yes":
        return RedirectResponse(url="/waiver?next=" + next, status_code=303)
    ip = request.client.host if request.client else ""
    record_waiver_acceptance(user["id"], ip=ip, version=version or WAIVER_VERSION)
    log_event("waiver_accepted", json.dumps({"user_id": user["id"], "version": version, "ip": ip}))
    safe_next = next if next.startswith("/") else "/bot/app"
    return RedirectResponse(url=safe_next, status_code=303)


# ---------- desktop app download (Pro + waiver gated) ----------
@app.get("/bot/app", response_class=HTMLResponse)
def bot_app_page(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/bot/app", status_code=303)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=bot_app", status_code=303)
    accepted_at_str = ""
    accepted_version = ""
    if has_accepted_waiver(user):
        u = get_user_by_id(user["id"]) or user
        ts = u.get("waiver_accepted_at")
        if ts:
            from datetime import datetime as _dt
            accepted_at_str = _dt.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M UTC")
        accepted_version = u.get("waiver_version") or ""
    return _render(
        request, "bot_app_download.html",
        default_tickers="AAPL MSFT NVDA GOOGL META AMZN TSLA SPY QQQ VOO",
        approx_size_kb=60,
        accepted_at_str=accepted_at_str, accepted_version=accepted_version,
    )


@app.post("/bot/app/download")
def bot_app_download(request: Request, tickers_text: str = Form("")):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/bot/app", status_code=303)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=bot_app", status_code=303)
    if not has_accepted_waiver(user):
        return RedirectResponse(url="/waiver?next=/bot/app", status_code=303)

    tickers = _parse_tickers(tickers_text) if tickers_text.strip() else [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "SPY", "QQQ", "VOO",
    ]
    lic = ensure_license(user["id"])
    verify_url = f"{APP_BASE_URL.rstrip('/')}/api/license/verify"
    u = get_user_by_id(user["id"]) or user
    blob = build_desktop_bundle(
        merged_params(), tickers,
        license_key=lic["key"], verify_url=verify_url, user_email=user["email"],
        waiver_accepted_at=u.get("waiver_accepted_at"),
        waiver_version=u.get("waiver_version") or WAIVER_VERSION,
    )
    log_event("bot_app_downloaded", json.dumps({"user_id": user["id"], "license_id": lic["id"], "n_tickers": len(tickers)}))
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="finance-buddy-algo-bot.zip"'},
    )


# ---------- license API + account page ----------
@app.post("/api/license/verify")
async def api_license_verify(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    key = (body.get("key") or "").strip()
    machine_id = (body.get("machine_id") or "")[:128]
    ip = request.client.host if request.client else ""
    result = verify_license(key, machine_id=machine_id, ip=ip)
    log_event(
        "license_verify",
        json.dumps({"key_tail": key[-6:] if key else "", "valid": result.get("valid"), "status": result.get("status")}),
    )
    status_code = 200 if result.get("valid") else 403
    return JSONResponse(result, status_code=status_code)


@app.get("/account/license", response_class=HTMLResponse)
def account_license(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/account/license", status_code=303)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=license", status_code=303)
    active = get_active_license(user["id"])
    if not active:
        active = ensure_license(user["id"])
    all_licenses = list_licenses(user["id"])
    verify_url = f"{APP_BASE_URL.rstrip('/')}/api/license/verify"
    return _render(
        request, "account_license.html",
        active=active, licenses=all_licenses, verify_url=verify_url,
    )


@app.post("/account/license/regenerate")
def account_license_regenerate(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/account/license", status_code=303)
    if not user_is_pro(user):
        return RedirectResponse(url="/pricing?reason=license", status_code=303)
    lic = regenerate_license(user["id"])
    log_event("license_regenerated", json.dumps({"user_id": user["id"], "license_id": lic["id"]}))
    return RedirectResponse(url="/account/license?regenerated=1", status_code=303)


@app.post("/account/license/revoke")
def account_license_revoke(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/account/license", status_code=303)
    n = revoke_all_for_user(user["id"])
    log_event("license_revoked", json.dumps({"user_id": user["id"], "count": n}))
    return RedirectResponse(url="/account/license?revoked=1", status_code=303)

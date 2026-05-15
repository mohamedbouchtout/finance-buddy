"""Finance Buddy — Algo Bot Desktop Application.

A self-contained Tkinter GUI that wraps the AI predictor + 200MA retest
strategy and runs them on a loop against the user's watchlist. Trades are
either paper-simulated inside the app or routed to Interactive Brokers via
ib_insync (live mode requires the user to install ib_insync and run IB
Gateway/TWS themselves).

This script is intended to be unzipped from the Pro download and run as:

    python bot_app.py

It deliberately uses only stdlib + numpy/pandas/yfinance (already required to
backtest the strategy locally). ib_insync is optional and only imported when
live mode is enabled.
"""
from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

BUNDLE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BUNDLE_DIR))

# In the downloaded bundle the algo_bot package sits next to this file. When
# the same script is run straight out of the source repo (app/algo_bot/desktop/
# bot_app.py) the package lives one level up.
_PARENT = BUNDLE_DIR.parent
if (_PARENT / "ai_predictor.py").exists() and not (BUNDLE_DIR / "algo_bot").exists():
    sys.path.insert(0, str(_PARENT.parent))


def _fatal_error_dialog(exc: BaseException):
    """Show *any* fatal startup error so the window doesn't just vanish."""
    tb = traceback.format_exc()
    log_path = BUNDLE_DIR / "bot_app_error.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.utcnow().isoformat()}Z ===\n{tb}\n")
    except Exception:
        pass
    try:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "Finance Buddy Algo Bot — startup error",
            f"{exc.__class__.__name__}: {exc}\n\n"
            f"A full traceback has been written to:\n{log_path}\n\n"
            f"If this is your first run, run 'run.bat' (Windows) or 'pip install -r "
            f"requirements.txt' first so numpy / pandas / yfinance are installed.",
        )
        root.destroy()
    except Exception:
        print(tb, file=sys.stderr)
        print(f"\nCrash log: {log_path}", file=sys.stderr)
        try:
            input("Press Enter to close…")
        except Exception:
            pass


# ---- defensive imports of heavy deps + the algo_bot package ----
try:
    try:
        from license_check import verify as verify_license  # type: ignore
    except Exception:
        verify_license = None  # type: ignore
    try:
        from algo_bot import ai_predictor, scanner, strategy  # type: ignore
        from algo_bot.config import merged_params  # type: ignore
    except ModuleNotFoundError:
        from app.algo_bot import ai_predictor, scanner, strategy  # type: ignore
        from app.algo_bot.config import merged_params  # type: ignore
except BaseException as _e:  # noqa: BLE001
    _fatal_error_dialog(_e)
    raise SystemExit(1)


CONFIG_PATH = BUNDLE_DIR / "trading_params.json"
WATCHLIST_PATH = BUNDLE_DIR / "watchlist.json"
STATE_PATH = BUNDLE_DIR / ".bot_state.json"


# --------------------- theme (matches website) ---------------------

THEME = {
    "ink_950": "#000000",
    "ink_900": "#06070a",
    "ink_800": "#0b0d12",
    "ink_700": "#11141b",
    "ink_600": "#1a1f2b",
    "ink_500": "#242a38",
    "cc_400":  "#60a5fa",
    "cc_500":  "#3b82f6",
    "cc_600":  "#2563eb",
    "cc_700":  "#1d4ed8",
    "cc_cyan": "#06b6d4",
    "cc_cyan_light": "#67e8f9",
    "slate_100": "#f1f5f9",
    "slate_200": "#e2e8f0",
    "slate_300": "#cbd5e1",
    "slate_400": "#94a3b8",
    "slate_500": "#64748b",
    "green":   "#22c55e",
    "green_light": "#34d399",
    "red":     "#ef4444",
    "red_light":   "#f87171",
    "amber":   "#fbbf24",
    "border":  "#1f2532",
}


def _pick_font(*candidates: str) -> str:
    """Return the first font in *candidates* installed on this system, else fallback."""
    try:
        from tkinter import font as tkfont
        avail = set(tkfont.families())
        for c in candidates:
            if c in avail:
                return c
    except Exception:
        pass
    return candidates[-1]


# ----------------------- bot engine -----------------------

class BotEngine:
    """Background scan + paper/live trade loop. Communicates with the GUI via a Queue."""

    def __init__(self, params: dict, watchlist: list[str], event_q: "queue.Queue[dict]"):
        self.params = params
        self.watchlist = list(dict.fromkeys(t.upper() for t in watchlist if t.strip()))
        self.event_q = event_q
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.mode = "paper"  # 'paper' | 'live'
        self.ib = None
        # Paper portfolio state
        self.cash = 100_000.0
        self.positions: dict[str, dict] = {}  # symbol -> {qty, entry, stop, target}
        self.trades: list[dict] = []
        self._load_state()

    # ---- state ----
    def _load_state(self):
        if not STATE_PATH.exists():
            return
        try:
            s = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.cash = float(s.get("cash", self.cash))
            self.positions = s.get("positions", {})
            self.trades = s.get("trades", [])
        except Exception:
            pass

    def _save_state(self):
        try:
            STATE_PATH.write_text(
                json.dumps({"cash": self.cash, "positions": self.positions, "trades": self.trades}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # ---- runtime control ----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._emit("status", text="Bot started")

    def stop(self):
        self._stop.set()
        self._emit("status", text="Stop requested")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- live broker ----
    def connect_live(self, host: str, port: int, client_id: int) -> bool:
        try:
            from ib_insync import IB  # type: ignore
        except Exception as e:
            self._emit("log", level="error", text=f"ib_insync not installed: {e}")
            return False
        try:
            ib = IB()
            ib.connect(host, port, clientId=client_id, timeout=8)
            self.ib = ib
            self.mode = "live"
            self._emit("status", text=f"Connected to IB at {host}:{port}")
            return True
        except Exception as e:
            self._emit("log", level="error", text=f"IB connect failed: {e}")
            return False

    def disconnect_live(self):
        if self.ib is not None:
            try:
                self.ib.disconnect()
            except Exception:
                pass
            self.ib = None
        self.mode = "paper"

    # ---- main loop ----
    def _run_loop(self):
        scan_interval = int(self.params.get("timing", {}).get("scan_interval", 1800))
        while not self._stop.is_set():
            try:
                self._heartbeat_license()
                rows = self._scan_once()
                self._emit("matrix", rows=rows)
                self._consider_trades(rows)
                self._save_state()
            except Exception as e:
                self._emit("log", level="error", text=f"Scan loop error: {e}\n{traceback.format_exc()}")
            # Sleep in small chunks so Stop is responsive
            slept = 0
            while slept < scan_interval and not self._stop.is_set():
                time.sleep(1)
                slept += 1
        self._emit("status", text="Bot stopped")

    def _heartbeat_license(self):
        if verify_license is None:
            return
        try:
            verify_license(strict=False)
        except Exception as e:
            self._emit("log", level="warn", text=f"License heartbeat failed: {e}")

    # ---- scanning ----
    def _scan_once(self) -> list[dict]:
        rows: list[dict] = []
        ai_conf = float(self.params.get("ai_analyzer", {}).get("confidence_threshold", 0.7))
        for sym in self.watchlist:
            if self._stop.is_set():
                break
            try:
                df = scanner.fetch_ohlcv(sym)
            except Exception as e:
                self._emit("log", level="warn", text=f"{sym}: fetch failed ({e})")
                continue
            if df is None or len(df) < 60:
                continue
            pred = ai_predictor.predict(df, symbol=sym)
            sig = None
            try:
                sig = strategy.detect_signal(df, self.params, symbol=sym)
            except Exception:
                sig = None
            row = {
                "symbol": sym,
                "score": pred["score"] if pred else 50.0,
                "direction": pred["direction"] if pred else "NEUTRAL",
                "setup": pred["setup_type"] if pred else "",
                "confidence": pred["confidence"] if pred else "LOW",
                "price": pred["entry"] if pred else float(df["close"].iloc[-1]),
                "stop": pred["stop"] if pred else None,
                "target": pred["target"] if pred else None,
                "strict_signal": bool(sig),
                "passes_threshold": bool(pred and (pred["score"] / 100.0) >= ai_conf),
                "ts": datetime.utcnow().strftime("%H:%M:%S"),
            }
            rows.append(row)
            self._emit("log", level="info", text=f"{sym}: score={row['score']} dir={row['direction']} setup={row['setup']}")
        return rows

    # ---- order routing ----
    def _consider_trades(self, rows: list[dict]):
        risk_pct = float(self.params.get("risk_management", {}).get("risk_per_trade_pct", 0.01))
        max_positions = int(self.params.get("risk_management", {}).get("max_positions", 5))
        equity = self._equity(rows)
        for r in rows:
            if not r["passes_threshold"] or r["direction"] != "BULLISH":
                continue
            if r["symbol"] in self.positions:
                continue
            if len(self.positions) >= max_positions:
                break
            risk_dollars = equity * risk_pct
            per_share_risk = max(r["price"] - (r["stop"] or r["price"] * 0.98), 0.01)
            qty = max(int(risk_dollars / per_share_risk), 1)
            cost = qty * r["price"]
            if cost > self.cash:
                self._emit("log", level="warn", text=f"{r['symbol']}: insufficient cash for {qty} shares")
                continue
            self._open_position(r, qty, cost)

        # Exits: check stops/targets on current positions using latest scanned price
        price_map = {r["symbol"]: r["price"] for r in rows}
        for sym, pos in list(self.positions.items()):
            px = price_map.get(sym)
            if px is None:
                continue
            if px <= pos["stop"]:
                self._close_position(sym, px, reason="stop")
            elif px >= pos["target"]:
                self._close_position(sym, px, reason="target")

    def _equity(self, rows: list[dict]) -> float:
        price_map = {r["symbol"]: r["price"] for r in rows}
        mv = sum(p["qty"] * price_map.get(s, p["entry"]) for s, p in self.positions.items())
        return self.cash + mv

    def _open_position(self, row: dict, qty: int, cost: float):
        if self.mode == "live" and self.ib is not None:
            placed = self._place_ib_order(row["symbol"], qty, "BUY")
            if not placed:
                return
        self.cash -= cost
        self.positions[row["symbol"]] = {
            "qty": qty, "entry": row["price"], "stop": row["stop"] or row["price"] * 0.97,
            "target": row["target"] or row["price"] * 1.06, "opened_at": time.time(),
        }
        self.trades.append({
            "ts": time.time(), "symbol": row["symbol"], "side": "BUY",
            "qty": qty, "price": row["price"], "mode": self.mode,
        })
        self._emit("trade", side="BUY", symbol=row["symbol"], qty=qty, price=row["price"], mode=self.mode)

    def _close_position(self, sym: str, px: float, reason: str):
        pos = self.positions.pop(sym, None)
        if not pos:
            return
        if self.mode == "live" and self.ib is not None:
            self._place_ib_order(sym, pos["qty"], "SELL")
        proceeds = pos["qty"] * px
        pnl = proceeds - pos["qty"] * pos["entry"]
        self.cash += proceeds
        self.trades.append({
            "ts": time.time(), "symbol": sym, "side": "SELL",
            "qty": pos["qty"], "price": px, "pnl": pnl, "reason": reason, "mode": self.mode,
        })
        self._emit("trade", side="SELL", symbol=sym, qty=pos["qty"], price=px, pnl=pnl, reason=reason, mode=self.mode)

    def _place_ib_order(self, symbol: str, qty: int, side: str) -> bool:
        try:
            from ib_insync import Stock, MarketOrder  # type: ignore
            contract = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            order = MarketOrder(side, qty)
            trade = self.ib.placeOrder(contract, order)
            self._emit("log", level="info", text=f"IB order sent: {side} {qty} {symbol} ({trade.order.orderId})")
            return True
        except Exception as e:
            self._emit("log", level="error", text=f"IB order failed for {symbol}: {e}")
            return False

    def _emit(self, kind: str, **payload):
        payload["kind"] = kind
        self.event_q.put(payload)


# ----------------------- GUI -----------------------

class BotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Finance Buddy — Algo Bot")
        self.geometry("1180x760")
        self.minsize(960, 620)
        self.configure(bg=THEME["ink_900"])

        # Resolve fonts once
        self.font_ui      = _pick_font("Inter", "Segoe UI Variable", "Segoe UI", "Arial")
        self.font_mono    = _pick_font("JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New")
        self.font_display = self.font_ui

        self.params = self._load_params()
        self.watchlist = self._load_watchlist()
        self.event_q: "queue.Queue[dict]" = queue.Queue()
        self.engine = BotEngine(self.params, self.watchlist, self.event_q)

        self._build_style()
        self._build_layout()
        self._verify_license_blocking()
        self.after(250, self._drain_events)

    # ---- bootstrap ----
    def _load_params(self) -> dict:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return merged_params()

    def _load_watchlist(self) -> list[str]:
        if WATCHLIST_PATH.exists():
            try:
                d = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
                if isinstance(d, list):
                    return d
                return d.get("tickers", [])
            except Exception:
                pass
        return ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "SPY", "QQQ", "VOO"]

    def _verify_license_blocking(self):
        if verify_license is None:
            self._log("warn", "No license_check module bundled — running in unlicensed mode.")
            return
        try:
            resp = verify_license(strict=False)
            if not resp.get("valid"):
                messagebox.showerror(
                    "License invalid",
                    f"Your Finance Buddy Pro subscription is not active.\n\n"
                    f"Server says: {resp.get('message','(no message)')}\n\n"
                    f"The bot will not place trades until your subscription is active.",
                )
                self.btn_start.configure(state="disabled")
                self._log("error", "License invalid — start button disabled.")
            else:
                self._log("info", f"License OK — plan={resp.get('plan')}")
        except Exception as e:
            self._log("warn", f"License check failed: {e}")

    # ---- styling ----
    def _build_style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass

        T = THEME
        ui   = (self.font_ui, 10)
        ui_b = (self.font_ui, 10, "bold")

        # Base
        s.configure(".", background=T["ink_900"], foreground=T["slate_200"],
                    fieldbackground=T["ink_700"], font=ui)

        s.configure("TFrame", background=T["ink_900"])
        s.configure("Card.TFrame", background=T["ink_800"], relief="flat")
        s.configure("Panel.TFrame", background=T["ink_700"], relief="flat")
        s.configure("Header.TFrame", background=T["ink_900"])

        s.configure("TLabel", background=T["ink_900"], foreground=T["slate_200"], font=ui)
        s.configure("Muted.TLabel", background=T["ink_900"], foreground=T["slate_400"], font=ui)
        s.configure("CardMuted.TLabel", background=T["ink_800"], foreground=T["slate_400"], font=ui)
        s.configure("CardTitle.TLabel", background=T["ink_800"], foreground=T["slate_200"],
                    font=(self.font_ui, 11, "bold"))
        s.configure("Title.TLabel", background=T["ink_900"], foreground=T["slate_100"],
                    font=(self.font_display, 16, "bold"))
        s.configure("Accent.TLabel", background=T["ink_900"], foreground=T["cc_400"], font=ui_b)
        s.configure("Pill.TLabel", background=T["ink_700"], foreground=T["cc_cyan_light"],
                    padding=(12, 6), font=ui_b)
        s.configure("Brand.TLabel", background=T["cc_500"], foreground=T["ink_950"],
                    padding=(8, 4), font=(self.font_display, 14, "bold"))

        # Notebook (tabs)
        s.configure("TNotebook", background=T["ink_900"], borderwidth=0, tabmargins=(0, 4, 0, 0))
        s.configure("TNotebook.Tab", background=T["ink_900"], foreground=T["slate_400"],
                    padding=(18, 9), borderwidth=0, font=ui_b)
        s.map("TNotebook.Tab",
              background=[("selected", T["ink_800"]), ("active", T["ink_800"])],
              foreground=[("selected", T["cc_400"]), ("active", T["slate_200"])])

        # Buttons — primary (blue), secondary (outline), danger (red), ghost
        s.configure("Primary.TButton",
                    background=T["cc_500"], foreground="#ffffff",
                    padding=(16, 9), font=ui_b, borderwidth=0, focusthickness=0)
        s.map("Primary.TButton",
              background=[("active", T["cc_600"]), ("pressed", T["cc_700"]),
                          ("disabled", T["ink_600"])],
              foreground=[("disabled", T["slate_500"])])

        s.configure("Secondary.TButton",
                    background=T["ink_700"], foreground=T["slate_200"],
                    padding=(14, 8), font=ui_b, borderwidth=1, focusthickness=0,
                    bordercolor=T["border"])
        s.map("Secondary.TButton",
              background=[("active", T["ink_600"])],
              bordercolor=[("active", T["cc_500"])],
              foreground=[("active", "#ffffff")])

        s.configure("Danger.TButton",
                    background=T["ink_700"], foreground=T["red_light"],
                    padding=(14, 8), font=ui_b, borderwidth=1, focusthickness=0,
                    bordercolor=T["border"])
        s.map("Danger.TButton",
              background=[("active", T["red"])],
              foreground=[("active", "#ffffff")],
              bordercolor=[("active", T["red"])])

        s.configure("Ghost.TButton",
                    background=T["ink_900"], foreground=T["slate_400"],
                    padding=(10, 6), borderwidth=0, focusthickness=0, font=ui)
        s.map("Ghost.TButton",
              background=[("active", T["ink_700"])],
              foreground=[("active", T["slate_100"])])

        # Treeview (data tables)
        s.configure("Treeview",
                    background=T["ink_800"], fieldbackground=T["ink_800"],
                    foreground=T["slate_200"], rowheight=28, borderwidth=0,
                    font=(self.font_mono, 10))
        s.configure("Treeview.Heading",
                    background=T["ink_700"], foreground=T["slate_400"],
                    relief="flat", padding=(8, 8), font=ui_b, borderwidth=0)
        s.map("Treeview.Heading", background=[("active", T["ink_600"])])
        s.map("Treeview",
              background=[("selected", T["cc_600"])],
              foreground=[("selected", "#ffffff")])

        # Combobox / Entry
        s.configure("TCombobox",
                    fieldbackground=T["ink_700"], background=T["ink_700"],
                    foreground=T["slate_100"], arrowcolor=T["cc_400"],
                    bordercolor=T["border"], lightcolor=T["border"], darkcolor=T["border"],
                    padding=6)
        s.map("TCombobox", fieldbackground=[("readonly", T["ink_700"])])
        self.option_add("*TCombobox*Listbox.background", T["ink_700"])
        self.option_add("*TCombobox*Listbox.foreground", T["slate_100"])
        self.option_add("*TCombobox*Listbox.selectBackground", T["cc_600"])
        self.option_add("*TCombobox*Listbox.font", ui)
        s.configure("TEntry", fieldbackground=T["ink_700"], foreground=T["slate_100"],
                    bordercolor=T["border"], lightcolor=T["border"], darkcolor=T["border"],
                    padding=6)

        # Scale (slider)
        s.configure("Horizontal.TScale",
                    background=T["ink_900"], troughcolor=T["ink_700"],
                    bordercolor=T["ink_900"], lightcolor=T["cc_500"], darkcolor=T["cc_500"])

        # LabelFrame
        s.configure("TLabelframe",
                    background=T["ink_800"], foreground=T["slate_300"],
                    bordercolor=T["border"], lightcolor=T["border"], darkcolor=T["border"],
                    borderwidth=1, relief="solid")
        s.configure("TLabelframe.Label",
                    background=T["ink_800"], foreground=T["slate_300"], font=ui_b)

        # Mode indicator (paper vs live)
        s.configure("ModePaper.TLabel", background=T["ink_700"], foreground=T["cc_cyan_light"],
                    padding=(10, 4), font=ui_b)
        s.configure("ModeLive.TLabel", background=T["ink_700"], foreground=T["green_light"],
                    padding=(10, 4), font=ui_b)

    # ---- helpers ----
    def _hline(self, parent, color=None):
        c = color or THEME["border"]
        line = tk.Frame(parent, bg=c, height=1, bd=0)
        return line

    def _card(self, parent, **pack):
        """Container with the dark-card look from the website."""
        outer = tk.Frame(parent, bg=THEME["border"], bd=0)
        outer.pack_propagate(False) if "height" in pack else None
        inner = tk.Frame(outer, bg=THEME["ink_800"], bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        outer.pack(**pack)
        return inner

    def _build_layout(self):
        T = THEME
        # ---- header bar ----
        header = tk.Frame(self, bg=T["ink_900"], height=68)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=T["ink_900"])
        brand.pack(side="left", padx=22, pady=14)
        logo = tk.Label(brand, text="C", bg=T["cc_500"], fg=T["ink_950"],
                        font=(self.font_display, 14, "bold"), width=2, height=1, bd=0)
        logo.pack(side="left", padx=(0, 10), ipadx=2, ipady=2)
        title_box = tk.Frame(brand, bg=T["ink_900"])
        title_box.pack(side="left")
        tk.Label(title_box, text="Finance Buddy",
                 bg=T["ink_900"], fg=T["slate_100"],
                 font=(self.font_display, 14, "bold")).pack(anchor="w")
        tk.Label(title_box, text="Algo Bot  ·  Desktop Terminal",
                 bg=T["ink_900"], fg=T["cc_400"],
                 font=(self.font_ui, 9)).pack(anchor="w")

        # status pill on the right
        right = tk.Frame(header, bg=T["ink_900"])
        right.pack(side="right", padx=22)
        self.status_var = tk.StringVar(value="Idle — press Start to begin scanning.")
        self.status_dot = tk.Label(right, text="●", bg=T["ink_700"], fg=T["slate_500"],
                                   font=(self.font_ui, 12), padx=10, pady=4)
        self.status_dot.pack(side="left")
        self.status_label = tk.Label(right, textvariable=self.status_var,
                                     bg=T["ink_700"], fg=T["cc_cyan_light"],
                                     font=(self.font_ui, 10, "bold"), padx=12, pady=6)
        self.status_label.pack(side="left")

        # underline like the site's border-b border-white/5
        self._hline(self).place(relx=0, rely=0, relwidth=1, y=68)
        tk.Frame(self, bg=T["border"], height=1).pack(fill="x")

        # ---- control bar ----
        control_bar = tk.Frame(self, bg=T["ink_900"])
        control_bar.pack(fill="x", padx=22, pady=(16, 8))

        self.btn_start = ttk.Button(control_bar, text="▶  Start Bot",
                                    style="Primary.TButton", command=self._on_start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(control_bar, text="■  Stop",
                                   style="Danger.TButton", command=self._on_stop)
        self.btn_stop.pack(side="left", padx=8)

        tk.Frame(control_bar, bg=T["border"], width=1, height=24).pack(side="left", padx=14)

        tk.Label(control_bar, text="Mode",
                 bg=T["ink_900"], fg=T["slate_400"],
                 font=(self.font_ui, 9, "bold")).pack(side="left", padx=(0, 8))
        self.mode_var = tk.StringVar(value="Paper")
        ttk.Combobox(control_bar, textvariable=self.mode_var,
                     values=["Paper", "Live (IB)"],
                     width=11, state="readonly").pack(side="left")
        ttk.Button(control_bar, text="Connect IB…",
                   style="Secondary.TButton",
                   command=self._on_connect_ib).pack(side="left", padx=10)

        # version chip on the far right
        tk.Label(control_bar, text="PRO",
                 bg=T["cc_500"], fg="#ffffff",
                 font=(self.font_ui, 8, "bold"),
                 padx=8, pady=2).pack(side="right")

        # ---- notebook ----
        nb_wrap = tk.Frame(self, bg=T["ink_900"])
        nb_wrap.pack(fill="both", expand=True, padx=22, pady=(8, 22))
        self.nb = ttk.Notebook(nb_wrap)
        self.nb.pack(fill="both", expand=True)

        self._build_matrix_tab(self.nb)
        self._build_config_tab(self.nb)
        self._build_positions_tab(self.nb)
        self._build_log_tab(self.nb)

    def _build_matrix_tab(self, nb):
        T = THEME
        f = tk.Frame(nb, bg=T["ink_900"]); nb.add(f, text="Signal Matrix")

        # subtitle
        sub = tk.Frame(f, bg=T["ink_900"])
        sub.pack(fill="x", padx=4, pady=(14, 8))
        tk.Label(sub, text="Live signal matrix",
                 bg=T["ink_900"], fg=T["slate_100"],
                 font=(self.font_display, 13, "bold")).pack(side="left")
        tk.Label(sub, text="  ·  AI + 200MA retest strategy across your watchlist",
                 bg=T["ink_900"], fg=T["slate_500"],
                 font=(self.font_ui, 9)).pack(side="left")

        # card around the treeview
        card_outer = tk.Frame(f, bg=T["border"])
        card_outer.pack(fill="both", expand=True, padx=4, pady=4)
        card = tk.Frame(card_outer, bg=T["ink_800"])
        card.pack(fill="both", expand=True, padx=1, pady=1)

        cols = ("symbol", "score", "direction", "setup", "confidence",
                "price", "stop", "target", "strict", "pass", "ts")
        headers = {"symbol":"SYMBOL","score":"SCORE","direction":"DIR","setup":"SETUP",
                   "confidence":"CONF","price":"PRICE","stop":"STOP","target":"TARGET",
                   "strict":"STRICT","pass":"PASS","ts":"TIME"}
        widths = {"symbol":80,"score":70,"direction":90,"setup":180,"confidence":90,
                  "price":90,"stop":90,"target":90,"strict":70,"pass":70,"ts":90}
        tv = ttk.Treeview(card, columns=cols, show="headings", height=20)
        for c in cols:
            tv.heading(c, text=headers[c])
            tv.column(c, width=widths[c], anchor="center")
        tv.pack(fill="both", expand=True, padx=8, pady=8)

        # row tags for direction color coding (uses the site's accents)
        tv.tag_configure("bullish", foreground=T["green_light"])
        tv.tag_configure("bearish", foreground=T["red_light"])
        tv.tag_configure("neutral", foreground=T["slate_300"])
        tv.tag_configure("alt",    background=T["ink_700"])
        self.matrix_tv = tv

    def _build_config_tab(self, nb):
        T = THEME
        f = tk.Frame(nb, bg=T["ink_900"]); nb.add(f, text="Config")

        tk.Label(f, text="Trading configuration",
                 bg=T["ink_900"], fg=T["slate_100"],
                 font=(self.font_display, 13, "bold")).pack(anchor="w", padx=4, pady=(14, 2))
        tk.Label(f, text="Tune risk, AI confidence, and scan cadence. Changes apply on next Start.",
                 bg=T["ink_900"], fg=T["slate_500"],
                 font=(self.font_ui, 9)).pack(anchor="w", padx=4, pady=(0, 10))

        # config card
        card_outer = tk.Frame(f, bg=T["border"])
        card_outer.pack(fill="x", padx=4, pady=4)
        card = tk.Frame(card_outer, bg=T["ink_800"])
        card.pack(fill="x", padx=1, pady=1)
        grid = tk.Frame(card, bg=T["ink_800"])
        grid.pack(fill="x", padx=22, pady=20)
        grid.columnconfigure(1, weight=1)

        risk_pct = float(self.params.get("risk_management", {}).get("risk_per_trade_pct", 0.01)) * 100
        ai_conf = float(self.params.get("ai_analyzer", {}).get("confidence_threshold", 0.7)) * 100
        max_pos = int(self.params.get("risk_management", {}).get("max_positions", 5))
        scan_interval = int(self.params.get("timing", {}).get("scan_interval", 1800))

        self.var_risk = tk.DoubleVar(value=risk_pct)
        self.var_ai = tk.DoubleVar(value=ai_conf)
        self.var_max = tk.IntVar(value=max_pos)
        self.var_interval = tk.IntVar(value=scan_interval)

        self._slider(grid, 0, "Risk per trade",       "% of equity",     self.var_risk, 0.1, 5.0, 0.1, fmt="{:.2f}%")
        self._slider(grid, 1, "AI confidence threshold", "minimum probability", self.var_ai, 50.0, 95.0, 1.0, fmt="{:.0f}%")
        self._slider(grid, 2, "Max simultaneous positions", "concurrent open trades", self.var_max, 1, 20, 1, integer=True)
        self._slider(grid, 3, "Scan interval",        "seconds between scans", self.var_interval, 60, 3600, 60, integer=True, fmt="{:d}s")

        btn_row = tk.Frame(card, bg=T["ink_800"])
        btn_row.pack(fill="x", padx=22, pady=(0, 18))
        ttk.Button(btn_row, text="Save config", style="Primary.TButton",
                   command=self._on_save_config).pack(side="right")

        # Watchlist
        wl_outer = tk.Frame(f, bg=T["border"])
        wl_outer.pack(fill="both", expand=True, padx=4, pady=(14, 4))
        wl_card = tk.Frame(wl_outer, bg=T["ink_800"])
        wl_card.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(wl_card, text="Watchlist",
                 bg=T["ink_800"], fg=T["slate_200"],
                 font=(self.font_ui, 11, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(wl_card, text="One ticker per line — or comma-separated.",
                 bg=T["ink_800"], fg=T["slate_500"],
                 font=(self.font_ui, 9)).pack(anchor="w", padx=18, pady=(0, 8))
        self.txt_watchlist = tk.Text(wl_card, height=8,
                                     bg=T["ink_700"], fg=T["slate_100"],
                                     insertbackground=T["cc_400"],
                                     borderwidth=0, highlightthickness=1,
                                     highlightbackground=T["border"],
                                     highlightcolor=T["cc_500"],
                                     font=(self.font_mono, 10), padx=12, pady=10)
        self.txt_watchlist.pack(fill="both", expand=True, padx=18, pady=4)
        self.txt_watchlist.insert("1.0", "\n".join(self.watchlist))
        ttk.Button(wl_card, text="Save watchlist", style="Secondary.TButton",
                   command=self._on_save_watchlist).pack(anchor="e", padx=18, pady=(8, 14))

    def _slider(self, parent, row, label, sublabel, var, lo, hi, step,
                integer=False, fmt="{:.0f}"):
        T = THEME
        col = tk.Frame(parent, bg=T["ink_800"])
        col.grid(row=row, column=0, sticky="w", pady=10, padx=(0, 16))
        tk.Label(col, text=label, bg=T["ink_800"], fg=T["slate_100"],
                 font=(self.font_ui, 10, "bold")).pack(anchor="w")
        tk.Label(col, text=sublabel, bg=T["ink_800"], fg=T["slate_500"],
                 font=(self.font_ui, 8)).pack(anchor="w")

        s = ttk.Scale(parent, from_=lo, to=hi, variable=var, length=420,
                      orient="horizontal")
        s.grid(row=row, column=1, sticky="we", pady=10)

        val_lbl = tk.Label(parent,
                           text=(str(int(var.get())) if integer else fmt.format(var.get())),
                           bg=T["cc_500"], fg="#ffffff",
                           font=(self.font_mono, 10, "bold"),
                           padx=12, pady=4, width=8)
        val_lbl.grid(row=row, column=2, padx=14)

        def _update(*_):
            v = var.get()
            if integer:
                v = int(round(v)); var.set(v)
                val_lbl.configure(text=(fmt.format(v) if "{:d}" in fmt else str(v)))
            else:
                val_lbl.configure(text=fmt.format(v))
        var.trace_add("write", _update)

    def _build_positions_tab(self, nb):
        T = THEME
        f = tk.Frame(nb, bg=T["ink_900"]); nb.add(f, text="Positions & P&L")

        tk.Label(f, text="Open positions",
                 bg=T["ink_900"], fg=T["slate_100"],
                 font=(self.font_display, 13, "bold")).pack(anchor="w", padx=4, pady=(14, 8))

        card_outer = tk.Frame(f, bg=T["border"])
        card_outer.pack(fill="both", expand=True, padx=4, pady=4)
        card = tk.Frame(card_outer, bg=T["ink_800"])
        card.pack(fill="both", expand=True, padx=1, pady=1)

        cols = ("symbol", "qty", "entry", "stop", "target", "opened")
        headers = {"symbol":"SYMBOL","qty":"QTY","entry":"ENTRY",
                   "stop":"STOP","target":"TARGET","opened":"OPENED"}
        tv = ttk.Treeview(card, columns=cols, show="headings", height=14)
        for c in cols:
            tv.heading(c, text=headers[c]); tv.column(c, width=120, anchor="center")
        tv.pack(fill="both", expand=True, padx=8, pady=8)
        tv.tag_configure("alt", background=T["ink_700"])
        self.positions_tv = tv

        # Equity stat strip
        stats_outer = tk.Frame(f, bg=T["border"])
        stats_outer.pack(fill="x", padx=4, pady=(8, 4))
        stats = tk.Frame(stats_outer, bg=T["ink_800"])
        stats.pack(fill="x", padx=1, pady=1)
        self._stat_equity = self._stat_block(stats, "Equity", "$100,000.00", T["cc_400"])
        self._stat_cash   = self._stat_block(stats, "Cash",   "$100,000.00", T["cc_cyan_light"])
        self._stat_trades = self._stat_block(stats, "Trades", "0",           T["slate_200"])

    def _stat_block(self, parent, label, value, color):
        T = THEME
        wrap = tk.Frame(parent, bg=T["ink_800"])
        wrap.pack(side="left", expand=True, fill="x", padx=2, pady=2)
        tk.Label(wrap, text=label.upper(), bg=T["ink_800"], fg=T["slate_500"],
                 font=(self.font_ui, 9, "bold")).pack(anchor="w", padx=18, pady=(14, 0))
        v = tk.StringVar(value=value)
        tk.Label(wrap, textvariable=v, bg=T["ink_800"], fg=color,
                 font=(self.font_mono, 16, "bold")).pack(anchor="w", padx=18, pady=(2, 14))
        return v

    def _build_log_tab(self, nb):
        T = THEME
        f = tk.Frame(nb, bg=T["ink_900"]); nb.add(f, text="Activity Log")

        tk.Label(f, text="Activity log",
                 bg=T["ink_900"], fg=T["slate_100"],
                 font=(self.font_display, 13, "bold")).pack(anchor="w", padx=4, pady=(14, 8))

        card_outer = tk.Frame(f, bg=T["border"])
        card_outer.pack(fill="both", expand=True, padx=4, pady=4)
        card = tk.Frame(card_outer, bg=T["ink_900"])
        card.pack(fill="both", expand=True, padx=1, pady=1)

        self.log_text = tk.Text(card, bg=T["ink_900"], fg=T["slate_400"],
                                insertbackground=T["cc_400"], borderwidth=0,
                                highlightthickness=0, padx=14, pady=10,
                                font=(self.font_mono, 10), wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_config("error", foreground=T["red_light"])
        self.log_text.tag_config("warn",  foreground=T["amber"])
        self.log_text.tag_config("info",  foreground=T["slate_400"])
        self.log_text.tag_config("trade", foreground=T["green_light"])
        self.log_text.tag_config("ts",    foreground=T["slate_500"])

    # ---- actions ----
    def _on_start(self):
        self._sync_params_from_ui()
        self.engine.params = self.params
        self.engine.watchlist = self._current_watchlist()
        self.engine.start()
        self.status_var.set("Running…")
        self.status_dot.configure(fg=THEME["green_light"])

    def _on_stop(self):
        self.engine.stop()
        self.status_var.set("Stopping…")
        self.status_dot.configure(fg=THEME["amber"])

    def _on_connect_ib(self):
        T = THEME
        if self.mode_var.get() != "Live (IB)":
            messagebox.showinfo("Live mode", "Switch Mode to 'Live (IB)' first.")
            return
        dlg = tk.Toplevel(self)
        dlg.title("Connect Interactive Brokers")
        dlg.configure(bg=T["ink_800"])
        dlg.resizable(False, False)

        tk.Label(dlg, text="Connect Interactive Brokers",
                 bg=T["ink_800"], fg=T["slate_100"],
                 font=(self.font_display, 12, "bold")).grid(row=0, column=0, columnspan=2,
                                                            padx=18, pady=(16, 4), sticky="w")
        tk.Label(dlg, text="Requires IB Gateway or TWS running locally.",
                 bg=T["ink_800"], fg=T["slate_500"],
                 font=(self.font_ui, 9)).grid(row=1, column=0, columnspan=2,
                                              padx=18, pady=(0, 14), sticky="w")

        def _row(r, label, var):
            tk.Label(dlg, text=label, bg=T["ink_800"], fg=T["slate_300"],
                     font=(self.font_ui, 10)).grid(row=r, column=0, padx=(18, 8),
                                                   pady=6, sticky="e")
            e = tk.Entry(dlg, textvariable=var,
                         bg=T["ink_700"], fg=T["slate_100"],
                         insertbackground=T["cc_400"],
                         relief="flat", highlightthickness=1,
                         highlightbackground=T["border"],
                         highlightcolor=T["cc_500"],
                         font=(self.font_mono, 10), width=22)
            e.grid(row=r, column=1, padx=(0, 18), pady=6, ipady=4)

        host_v = tk.StringVar(value="127.0.0.1")
        port_v = tk.IntVar(value=7497)
        cid_v = tk.IntVar(value=42)
        _row(2, "Host", host_v)
        _row(3, "Port", port_v)
        _row(4, "Client ID", cid_v)

        def _connect():
            ok = self.engine.connect_live(host_v.get(), int(port_v.get()), int(cid_v.get()))
            if ok:
                dlg.destroy()
        ttk.Button(dlg, text="Connect", style="Primary.TButton",
                   command=_connect).grid(row=5, column=0, columnspan=2,
                                          padx=18, pady=(10, 18), sticky="we")

    def _on_save_config(self):
        self._sync_params_from_ui()
        try:
            CONFIG_PATH.write_text(json.dumps(self.params, indent=2), encoding="utf-8")
            self._log("info", f"Saved config to {CONFIG_PATH.name}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _on_save_watchlist(self):
        wl = self._current_watchlist()
        try:
            WATCHLIST_PATH.write_text(json.dumps({"tickers": wl}, indent=2), encoding="utf-8")
            self._log("info", f"Saved {len(wl)} tickers to {WATCHLIST_PATH.name}")
            self.watchlist = wl
            self.engine.watchlist = wl
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _current_watchlist(self) -> list[str]:
        raw = self.txt_watchlist.get("1.0", "end").replace(",", " ").split()
        out, seen = [], set()
        for t in raw:
            t = t.strip().upper()
            if t and t not in seen and t.replace(".", "").replace("-", "").isalnum():
                out.append(t); seen.add(t)
        return out

    def _sync_params_from_ui(self):
        rm = self.params.setdefault("risk_management", {})
        rm["risk_per_trade_pct"] = float(self.var_risk.get()) / 100.0
        rm["max_positions"] = int(self.var_max.get())
        ai = self.params.setdefault("ai_analyzer", {})
        ai["confidence_threshold"] = float(self.var_ai.get()) / 100.0
        self.params.setdefault("timing", {})["scan_interval"] = int(self.var_interval.get())

    # ---- event pump ----
    def _drain_events(self):
        try:
            while True:
                ev = self.event_q.get_nowait()
                k = ev.get("kind")
                if k == "matrix":
                    self._render_matrix(ev["rows"])
                elif k == "log":
                    self._log(ev.get("level", "info"), ev.get("text", ""))
                elif k == "trade":
                    side = ev["side"]; sym = ev["symbol"]
                    if side == "BUY":
                        self._log("trade", f"BUY  {ev['qty']:>4} {sym:<6} @ {ev['price']:.2f}  [{ev['mode']}]")
                    else:
                        pnl = ev.get("pnl", 0.0)
                        sign = "+" if pnl >= 0 else ""
                        self._log("trade",
                                  f"SELL {ev['qty']:>4} {sym:<6} @ {ev['price']:.2f}  "
                                  f"PnL {sign}{pnl:.2f}  reason={ev.get('reason')}  [{ev['mode']}]")
                    self._refresh_positions()
                elif k == "status":
                    text = ev.get("text", "")
                    self.status_var.set(text)
                    t = text.lower()
                    if "started" in t or "running" in t or "connected" in t:
                        self.status_dot.configure(fg=THEME["green_light"])
                    elif "stopped" in t or "stop" in t:
                        self.status_dot.configure(fg=THEME["slate_500"])
                    elif "error" in t or "fail" in t:
                        self.status_dot.configure(fg=THEME["red_light"])
        except queue.Empty:
            pass
        self.after(250, self._drain_events)

    def _render_matrix(self, rows: list[dict]):
        self.matrix_tv.delete(*self.matrix_tv.get_children())
        for i, r in enumerate(sorted(rows, key=lambda x: abs(x["score"] - 50), reverse=True)):
            d = (r.get("direction") or "").upper()
            tag = "bullish" if d == "BULLISH" else "bearish" if d == "BEARISH" else "neutral"
            tags = (tag,) + (("alt",) if i % 2 else ())
            self.matrix_tv.insert("", "end", tags=tags, values=(
                r["symbol"], f"{r['score']:.1f}", d or "—", r["setup"], r["confidence"],
                f"{r['price']:.2f}",
                f"{r['stop']:.2f}" if r.get("stop") else "—",
                f"{r['target']:.2f}" if r.get("target") else "—",
                "✓" if r["strict_signal"] else "",
                "✓" if r["passes_threshold"] else "",
                r["ts"],
            ))
        self._refresh_positions()

    def _refresh_positions(self):
        self.positions_tv.delete(*self.positions_tv.get_children())
        for i, (sym, pos) in enumerate(self.engine.positions.items()):
            tags = ("alt",) if i % 2 else ()
            self.positions_tv.insert("", "end", tags=tags, values=(
                sym, pos["qty"], f"{pos['entry']:.2f}",
                f"{pos['stop']:.2f}", f"{pos['target']:.2f}",
                datetime.fromtimestamp(pos["opened_at"]).strftime("%Y-%m-%d %H:%M"),
            ))
        mv = sum(p["qty"] * p["entry"] for p in self.engine.positions.values())
        equity = self.engine.cash + mv
        self._stat_equity.set(f"${equity:,.2f}")
        self._stat_cash.set(f"${self.engine.cash:,.2f}")
        self._stat_trades.set(str(len(self.engine.trades)))

    def _log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{ts}] ", "ts")
        self.log_text.insert("end", f"{msg}\n", level)
        self.log_text.see("end")


def _fatal_error_dialog(exc: BaseException):
    """Show *any* fatal startup error so the window doesn't just vanish."""
    tb = traceback.format_exc()
    log_path = BUNDLE_DIR / "bot_app_error.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.utcnow().isoformat()}Z ===\n{tb}\n")
    except Exception:
        pass
    # Try to show a GUI dialog; fall back to console.
    try:
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "Finance Buddy Algo Bot — startup error",
            f"{exc.__class__.__name__}: {exc}\n\n"
            f"A full traceback has been written to:\n{log_path}\n\n"
            f"If this is your first run, make sure you ran 'run.bat' (Windows) or "
            f"'pip install -r requirements.txt' so numpy / pandas / yfinance are installed.",
        )
        root.destroy()
    except Exception:
        print(tb, file=sys.stderr)
        print(f"\nCrash log: {log_path}", file=sys.stderr)
        try:
            input("Press Enter to close…")
        except Exception:
            pass


def main():
    try:
        app = BotApp()
        app.mainloop()
    except BaseException as e:  # noqa: BLE001 — we want absolutely everything
        _fatal_error_dialog(e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

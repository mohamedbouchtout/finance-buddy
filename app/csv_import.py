"""Parse common broker CSV exports into (ticker, shares) tuples.

Supports loose/flexible parsing — we look for columns named like
'Symbol'/'Ticker' and 'Quantity'/'Shares'. Falls back to first two columns
if no headers detected.
"""
from __future__ import annotations

import csv
import io
from typing import List, Tuple

_TICKER_KEYS = {"symbol", "ticker", "stock", "security"}
_QTY_KEYS = {"quantity", "shares", "qty", "units"}


def parse_csv(text: str) -> List[Tuple[str, float]]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if not rows:
        return []

    # Detect header
    header = [c.strip().lower() for c in rows[0]]
    ticker_idx = qty_idx = None
    for i, col in enumerate(header):
        if ticker_idx is None and col in _TICKER_KEYS:
            ticker_idx = i
        elif qty_idx is None and col in _QTY_KEYS:
            qty_idx = i

    out: List[Tuple[str, float]] = []
    data_rows = rows[1:] if ticker_idx is not None else rows
    if ticker_idx is None:
        ticker_idx = 0
    if qty_idx is None:
        qty_idx = 1

    for row in data_rows:
        if len(row) <= max(ticker_idx, qty_idx):
            continue
        ticker = row[ticker_idx].strip().upper()
        if not ticker or not ticker[0].isalpha():
            continue
        qty_str = row[qty_idx].strip().replace(",", "").replace("$", "")
        if not qty_str:
            continue
        try:
            shares = float(qty_str)
        except ValueError:
            continue
        if shares <= 0:
            continue
        out.append((ticker, shares))
    return out

"""license_check.py — bundled with every Finance Buddy bot config.

This script phones home to the Finance Buddy license server to verify
your subscription is still active. It's called from the bot's startup and
periodically while running.

If your subscription is canceled, the next heartbeat will fail and the bot
will refuse to start (or exit on its next scheduled check).

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GRACE_HOURS = 24            # offline grace window
HEARTBEAT_TIMEOUT = 10      # seconds
CACHE_FILENAME = ".cc_license_cache.json"


def _bundle_dir() -> Path:
    return Path(__file__).resolve().parent


def _load_license() -> dict:
    path = _bundle_dir() / "license.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _cache_path() -> Path:
    return _bundle_dir() / CACHE_FILENAME


def _read_cache() -> dict:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_cache(payload: dict) -> None:
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _machine_id() -> str:
    return f"{socket.gethostname()}|{os.environ.get('USERNAME') or os.environ.get('USER') or '?'}"


def _post(url: str, body: dict, timeout: int = HEARTBEAT_TIMEOUT) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "cc-bot/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify(strict: bool = True) -> dict:
    """Returns the verification response dict. If strict and invalid, exits."""
    try:
        lic = _load_license()
    except Exception as e:
        msg = f"[license_check] Cannot read license.json: {e}"
        print(msg, file=sys.stderr)
        if strict:
            sys.exit(2)
        return {"valid": False, "message": msg}

    key = lic.get("key", "")
    verify_url = lic.get("verify_url", "")
    if not key or not verify_url:
        if strict:
            print("[license_check] Invalid license file.", file=sys.stderr)
            sys.exit(2)
        return {"valid": False, "message": "Invalid license file."}

    try:
        resp = _post(verify_url, {"key": key, "machine_id": _machine_id()})
        _write_cache({"ts": int(time.time()), "resp": resp})
        if resp.get("valid"):
            print(f"[license_check] OK — plan={resp.get('plan')}")
            return resp
        print(f"[license_check] DENIED — {resp.get('message', 'invalid license')}", file=sys.stderr)
        if strict:
            sys.exit(3)
        return resp
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Offline path: allow within grace window using last good cache
        cache = _read_cache()
        last = cache.get("resp", {})
        last_ts = cache.get("ts", 0)
        age_h = (time.time() - last_ts) / 3600
        if last.get("valid") and age_h < GRACE_HOURS:
            print(
                f"[license_check] Offline ({e}); using cached OK ({age_h:.1f}h old, grace={GRACE_HOURS}h).",
                file=sys.stderr,
            )
            return last
        msg = f"[license_check] Cannot reach license server and no valid cache within {GRACE_HOURS}h grace ({e})."
        print(msg, file=sys.stderr)
        if strict:
            sys.exit(4)
        return {"valid": False, "message": msg}


if __name__ == "__main__":
    verify(strict=True)
    print("OK")

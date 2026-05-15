"""Pytest config — make app importable."""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Use a separate test DB
os.environ["APP_SECRET"] = "test-secret"
import app.config as cfg
cfg.DB_PATH = ROOT / "data" / "test_app.db"
if cfg.DB_PATH.exists():
    cfg.DB_PATH.unlink()

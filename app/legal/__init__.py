from pathlib import Path

WAIVER_VERSION = "1.0-2026-05"
WAIVER_PATH = Path(__file__).parent / "waiver.txt"


def waiver_text() -> str:
    return WAIVER_PATH.read_text(encoding="utf-8")

# -*- coding: utf-8 -*-
"""Bootstrap layer: connect the Streamlit dashboard to MetaTrader 5 automatically.

When the dashboard starts, ``ensure_mt5_pipeline`` will (on Windows):
  1. Locate and launch the MT5 terminal if it is not already running (reuses
     ``scripts.launch_mt5``).
  2. Initialize the Python API connection via ``MetaTraderConnector``.
  3. Run an incremental historical-data extraction for every Market Watch
     symbol so fresh CSVs land in ``market_data/historical/`` for the dashboard.

On non-Windows machines (or when MT5 is unavailable) the function degrades
gracefully: existing cached CSVs remain usable by the dashboard and a clear
status report explains what happened.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.python.data.config import DEFAULT_TIMEFRAMES  # noqa: E402
from src.python.data.mt5_connector import MetaTraderConnector, MT5ConnectionError  # noqa: E402

# متغیرهای محیطی برای کنترل چرخه خودکار از بیرون (مثلاً CI یا اجرای دستی)
AUTO_MT5 = os.environ.get("MT5_AUTO_BOOTSTRAP", "1").strip().lower() not in {"0", "false", "no", "off"}
EXTRACTION_COUNT = int(os.environ.get("MT5_BOOTSTRAP_COUNT", "1000"))


@dataclass
class PipelineStatus:
    """Outcome of the MT5 bootstrap attempt."""

    mt5_package: bool = False
    windows: bool = False
    terminal_running: bool = False
    launched_terminal: bool = False
    connected: bool = False
    extracted_symbols: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.connected


def _terminal_is_running() -> bool:
    """Return True if a MetaTrader 5 terminal process is already running (Windows)."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import subprocess

        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.lower()
        return "terminal64.exe" in out
    except Exception:
        return False


def _launch_terminal() -> bool:
    """Start the MT5 terminal through scripts.launch_mt5; True on success."""
    try:
        from scripts.launch_mt5 import find_mt5_executable, launch_mt5

        executable = find_mt5_executable(None)
        launch_mt5(executable, wait=False)
        return True
    except Exception:
        return False


def ensure_mt5_pipeline(
    *,
    timeframes: list[str] | None = None,
    count: int = 1000,
    startup_wait_s: float = 15.0,
    poll_interval_s: float = 1.0,
) -> PipelineStatus:
    """Bring MT5 up and sync historical data. Never raises — reports status instead."""
    status = PipelineStatus()

    try:
        import MetaTrader5  # noqa: F401

        status.mt5_package = True
    except Exception:
        status.message = (
            "پکیج MetaTrader5 در این محیط نصب نیست (فقط ویندوز). "
            "داشبورد با داده‌های کش‌شده کار می‌کند."
        )
        return status

    status.windows = sys.platform.startswith("win")

    # Step 1: make sure the terminal itself is up (Windows only).
    if status.windows:
        status.terminal_running = _terminal_is_running()
        if not status.terminal_running:
            status.launched_terminal = _launch_terminal()
            deadline = time.monotonic() + max(startup_wait_s, 0)
            while time.monotonic() < deadline and not _terminal_is_running():
                time.sleep(poll_interval_s)
            status.terminal_running = _terminal_is_running()

    # Step 2: API connection (initialize() attaches to a running terminal or
    # launches one via MT5_PATH when needed).
    connector = MetaTraderConnector()
    try:
        connector.connect()
    except MT5ConnectionError as exc:
        status.message = f"اتصال API به MT5 برقرار نشد: {exc}"
        return status
    except Exception as exc:  # pragma: no cover - defensive
        status.message = f"خطای غیرمنتظره در اتصال MT5: {exc}"
        return status

    status.connected = True

    # Step 3: incremental extraction of Market Watch history into the CSV cache.
    try:
        from src.python.data.main import extract_market_watch_data, save_extraction

        result = extract_market_watch_data(
            symbols=None,  # all Market Watch symbols
            timeframes=timeframes or DEFAULT_TIMEFRAMES,
            count=count,
        )
        # save_extraction expects the full payload (summary + data), like main.py CLI.
        summary = save_extraction(result, REPO_ROOT / "market_data" / "historical")
        status.extracted_symbols = sorted((result.get("data") or {}).keys())
        record_count = summary.get("stored_record_count", 0)
        status.message = (
            f"چرخه کامل: MT5 بالا آمد، {len(status.extracted_symbols)} نماد استخراج و "
            f"{record_count} رکورد در کش داشبورد ذخیره شد ✅"
        )
    except Exception as exc:
        status.message = f"MT5 متصل است اما استخراج داده خطا داد: {exc}"
    finally:
        try:
            connector.disconnect()
        except Exception:
            pass

    return status


def main() -> int:
    status = ensure_mt5_pipeline()
    print(f"MT5 package : {status.mt5_package}")
    print(f"Windows     : {status.windows}")
    print(f"Terminal    : running={status.terminal_running} launched={status.launched_terminal}")
    print(f"API ready   : {status.connected}")
    print(f"Symbols     : {', '.join(status.extracted_symbols) or '—'}")
    print(f"Message     : {status.message}")
    return 0 if status.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

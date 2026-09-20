"""Display symbols currently selected in MetaTrader 5 Market Watch."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import MutableMapping
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

# Keep generated files at the project-level directory used by training and
# backtesting, rather than inside the Python package.
DATA_DIR = Path(__file__).resolve().parents[3] / "market_data"
WATCHER_LOCK_PATH = DATA_DIR / ".market_watch.lock"
INITIAL_CANDLE_COUNT = 1_000
DEFAULT_HISTORY_COUNT = 10_000
TIMEFRAMES = {
    "M1": (mt5.TIMEFRAME_M1, INITIAL_CANDLE_COUNT),
    "M5": (mt5.TIMEFRAME_M5, INITIAL_CANDLE_COUNT),
    "M15": (mt5.TIMEFRAME_M15, INITIAL_CANDLE_COUNT),
    "M30": (mt5.TIMEFRAME_M30, INITIAL_CANDLE_COUNT),
    "H1": (mt5.TIMEFRAME_H1, INITIAL_CANDLE_COUNT),
    "H4": (mt5.TIMEFRAME_H4, INITIAL_CANDLE_COUNT),
    "D1": (mt5.TIMEFRAME_D1, INITIAL_CANDLE_COUNT),
    "W1": (mt5.TIMEFRAME_W1, INITIAL_CANDLE_COUNT),
    "MN1": (mt5.TIMEFRAME_MN1, INITIAL_CANDLE_COUNT),
}


def dashboard_direct_mode_enabled(
    environment: MutableMapping[str, str] | None = None,
) -> bool:
    """Return whether the dashboard owns the direct MT5 connection."""
    values = environment if environment is not None else os.environ
    return values.get("MT5_DASHBOARD_DIRECT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_market_data_subscriptions(
    visible_symbols: list[str],
    saved_settings: MutableMapping[str, object] | None,
    configured_timeframes: list[str] | tuple[str, ...],
    history_count: int = DEFAULT_HISTORY_COUNT,
) -> list[tuple[str, str, int]]:
    """Resolve dashboard-selected symbols into deterministic data subscriptions."""
    if history_count < 1:
        raise ValueError("history_count must be positive")
    visible = {str(symbol).strip() for symbol in visible_symbols if str(symbol).strip()}
    saved = saved_settings or {}
    pairs = saved.get("symbol_timeframes")
    selected = (
        {str(symbol).strip() for symbol in pairs if str(symbol).strip()}
        if isinstance(pairs, dict)
        else set()
    )
    symbols = sorted(selected & visible) or sorted(visible)
    timeframes = [
        str(value).strip().upper()
        for value in configured_timeframes
        if str(value).strip().upper() in TIMEFRAMES
    ]
    if not timeframes:
        timeframes = list(TIMEFRAMES)
    return [
        (symbol, timeframe, history_count)
        for symbol in symbols
        for timeframe in dict.fromkeys(timeframes)
    ]


def mt5_is_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "terminal64.exe" in result.stdout.lower()


def _safe_symbol_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)


def _json_value(value: object) -> int | float | str | bool | None:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _rates_to_records(rates: object) -> list[dict[str, object]]:
    if rates is None:
        return []
    records = []
    for rate in rates:
        record = {
            name: _json_value(value) for name, value in zip(rate.dtype.names, rate)
        }
        record["time_iso"] = datetime.fromtimestamp(
            float(record["time"]), timezone.utc
        ).isoformat()
        records.append(record)
    return records


def _read_candles(paths: list[Path]) -> list[dict[str, object]]:
    """Read and merge legacy snapshots before the canonical file is updated."""

    candles: dict[str, dict[str, object]] = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as file:
            stored = json.load(file)
        if not isinstance(stored, dict):
            continue
        for candle in stored.get("candles", []):
            if isinstance(candle, dict) and "time" in candle:
                candles[str(candle["time"])] = candle
    return sorted(candles.values(), key=lambda candle: float(candle["time"]))


def save_rates(
    symbol: str,
    timeframe_name: str,
    timeframe: int,
    count: int = INITIAL_CANDLE_COUNT,
    cache: MutableMapping[str, list[dict[str, object]]] | None = None,
) -> int:
    """Keep one rolling, bounded snapshot for a symbol/timeframe.

    The initial synchronization requests ``count`` bars. Subsequent calls only
    request the current and the immediately preceding bar, which is enough to
    revise the still-open candle and append a newly closed candle.
    """

    if count <= 0:
        raise ValueError("count must be greater than zero")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_stem = f"{_safe_symbol_name(symbol)}_{timeframe_name}"
    canonical_path = DATA_DIR / f"{file_stem}.json"
    legacy_paths = sorted(DATA_DIR.glob(f"{file_stem}_*.json"))
    key = file_stem
    existing = cache.get(key) if cache is not None else None
    if existing is None:
        existing = _read_candles([canonical_path, *legacy_paths])

    fetch_count = count if len(existing) < count else 2
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, fetch_count)
    if rates is None:
        raise RuntimeError(
            f"دریافت کندل‌های {symbol} {timeframe_name} ناموفق بود: {mt5.last_error()}"
        )

    new_records = _rates_to_records(rates)
    return save_candle_records(
        symbol,
        timeframe_name,
        new_records,
        count=count,
        cache=cache,
        data_dir=DATA_DIR,
        existing=existing,
        legacy_paths=legacy_paths,
    )


def save_candle_records(
    symbol: str,
    timeframe_name: str,
    records: list[dict[str, object]],
    count: int = INITIAL_CANDLE_COUNT,
    cache: MutableMapping[str, list[dict[str, object]]] | None = None,
    data_dir: Path = DATA_DIR,
    existing: list[dict[str, object]] | None = None,
    legacy_paths: list[Path] | None = None,
) -> int:
    """Persist normalized candles without requiring direct MT5 ownership."""
    if count <= 0:
        raise ValueError("count must be greater than zero")

    data_dir.mkdir(parents=True, exist_ok=True)
    file_stem = f"{_safe_symbol_name(symbol)}_{timeframe_name}"
    canonical_path = data_dir / f"{file_stem}.json"
    duplicate_paths = (
        legacy_paths
        if legacy_paths is not None
        else sorted(data_dir.glob(f"{file_stem}_*.json"))
    )
    if existing is None:
        existing = _read_candles([canonical_path, *duplicate_paths])
    def normalize(record: dict[str, object]) -> dict[str, object]:
        normalized = {
            key: _json_value(value) for key, value in record.items()
        }
        if not normalized.get("time_iso"):
            normalized["time_iso"] = datetime.fromtimestamp(
                float(normalized["time"]), timezone.utc
            ).isoformat()
        return normalized

    normalized_existing = [normalize(record) for record in existing]
    normalized_records = [normalize(record) for record in records]
    merged = {str(record["time"]): record for record in normalized_existing}
    merged.update({str(record["time"]): record for record in normalized_records})
    candles = sorted(merged.values(), key=lambda record: float(record["time"]))[-count:]
    payload = {
        "symbol": symbol,
        "timeframe": timeframe_name,
        "requested_candles": count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "candles": candles,
    }
    temporary_path = canonical_path.with_suffix(".json.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    temporary_path.replace(canonical_path)
    if cache is not None:
        cache[file_stem] = candles
    for duplicate_path in duplicate_paths:
        if duplicate_path != canonical_path and duplicate_path.is_file():
            duplicate_path.unlink()
    return len(payload["candles"])


def sync_market_data_from_connector(
    connector: object,
    subscriptions: list[tuple[str, str, int]],
    data_dir: Path = DATA_DIR,
    cache: MutableMapping[str, list[dict[str, object]]] | None = None,
) -> int:
    """Fetch selected candles through an existing dashboard MT5 connector."""
    saved = 0
    for symbol, timeframe_name, count in subscriptions:
        try:
            records = connector.get_historical_candles(
                symbol, timeframe=timeframe_name, count=count
            )
            if not records:
                continue
            save_candle_records(
                symbol,
                timeframe_name,
                records,
                count=count,
                cache=cache,
                data_dir=data_dir,
            )
            saved += 1
        except (RuntimeError, OSError, ValueError, TypeError) as error:
            print(
                f"خطا در ذخیره دادهٔ {symbol} {timeframe_name}: {error}",
                flush=True,
            )
    return saved


def save_market_data(
    subscriptions: list[tuple[str, str, int]],
    cache: MutableMapping[str, list[dict[str, object]]] | None = None,
) -> None:
    saved = 0
    for symbol_name, timeframe_name, count in subscriptions:
        timeframe, _ = TIMEFRAMES[timeframe_name]
        try:
            candle_count = save_rates(
                symbol_name, timeframe_name, timeframe, count, cache=cache
            )
            saved += 1
            print(
                f"ذخیره شد: {symbol_name} {timeframe_name} ({candle_count} کندل)",
                flush=True,
            )
        except (RuntimeError, OSError, ValueError, TypeError) as error:
            print(f"خطا در دریافت داده: {error}", flush=True)
    print(f"به‌روزرسانی داده‌ها کامل شد: {saved} فایل.", flush=True)


def display_market_watch(
    cache: MutableMapping[str, list[dict[str, object]]] | None = None,
) -> None:
    symbols = mt5.symbols_get()
    if symbols is None:
        raise RuntimeError(f"دریافت Market Watch ناموفق بود: {mt5.last_error()}")

    selected_symbols = sorted(
        (symbol for symbol in symbols if symbol.visible),
        key=lambda symbol: symbol.name,
    )
    print("\033[H\033[J", end="")
    if not selected_symbols:
        print("هیچ نمادی در Market Watch انتخاب نشده است.", flush=True)
        return

    print(f"تعداد نمادهای انتخاب‌شده: {len(selected_symbols)}")
    print(f"{'نماد':<20} {'Bid':>14} {'Ask':>14}  توضیح")
    print("-" * 72)
    for symbol in selected_symbols:
        tick = mt5.symbol_info_tick(symbol.name)
        bid = tick.bid if tick is not None else symbol.bid
        ask = tick.ask if tick is not None else symbol.ask
        print(
            f"{symbol.name:<20} {bid:>14.5f} {ask:>14.5f}  {symbol.description or ''}"
        )
    settings_path = DATA_DIR.parent / "data" / "dashboard_settings.json"
    try:
        saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved_settings = {}
    configured_timeframes = os.getenv("DATA_TIMEFRAMES", "").split(",")
    subscriptions = resolve_market_data_subscriptions(
        [symbol.name for symbol in selected_symbols],
        saved_settings,
        configured_timeframes,
    )
    save_market_data(subscriptions, cache=cache)
    print("برای توقف، Ctrl+C را بزنید.", flush=True)


def _acquire_watcher_lock():
    """Acquire the process-wide collector lock and return its open handle."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        lock_handle = WATCHER_LOCK_PATH.open("x+", encoding="ascii")
    except FileExistsError:
        try:
            owner_pid = int(WATCHER_LOCK_PATH.read_text(encoding="ascii").strip())
            os.kill(owner_pid, 0)
        except (OSError, ValueError):
            WATCHER_LOCK_PATH.unlink(missing_ok=True)
            lock_handle = WATCHER_LOCK_PATH.open("x+", encoding="ascii")
        else:
            print(
                f"جمع‌آوری‌کنندهٔ دیگری در حال اجراست (PID={owner_pid}).",
                flush=True,
            )
            return None
    lock_handle.write(str(os.getpid()))
    lock_handle.flush()
    return lock_handle


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if dashboard_direct_mode_enabled():
        print(
            "MT5_DASHBOARD_DIRECT فعال است؛ جمع‌آورندهٔ Market Watch اجرا نمی‌شود.",
            flush=True,
        )
        return

    lock_handle = _acquire_watcher_lock()
    if lock_handle is None:
        return

    connected = False
    waiting_reported = False
    candle_cache: dict[str, list[dict[str, object]]] = {}
    try:
        while True:
            if not connected:
                if not mt5_is_running():
                    if not waiting_reported:
                        print(
                            "MetaTrader 5 باز نیست؛ فقط در انتظار بازشدن برنامه هستم.",
                            flush=True,
                        )
                        waiting_reported = True
                    time.sleep(1)
                    continue

                connected = mt5.initialize()

                if connected:
                    print("به MetaTrader 5 بازِ موجود متصل شد.", flush=True)
                    waiting_reported = False
                else:
                    time.sleep(1)
                    continue

            terminal = mt5.terminal_info()
            if terminal is None or not getattr(terminal, "connected", True):
                print(
                    "اتصال به MetaTrader 5 از بین رفت؛ در انتظار بازشدن برنامه.",
                    flush=True,
                )
                connected = False
                mt5.shutdown()
                time.sleep(1)
                continue

            try:
                display_market_watch(cache=candle_cache)
            except RuntimeError as error:
                print(f"دریافت Market Watch ناموفق بود: {error}", flush=True)
                connected = False
                mt5.shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nبروزرسانی Market Watch متوقف شد.")
    finally:
        mt5.shutdown()
        lock_handle.close()
        WATCHER_LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

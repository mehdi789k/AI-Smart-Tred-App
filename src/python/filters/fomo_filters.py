"""Configurable FOMO filter that blocks late entries after a large move."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indicators"))

from indicators.common import load_candles

PROGRESS_THRESHOLD = 0.50
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_fomo_filter(
    candles: list[dict[str, Any]],
    entry_signals: list[str] | None = None,
    entry_prices: list[float | None] | None = None,
    take_profits: list[float | None] | None = None,
    progress_threshold: float = PROGRESS_THRESHOLD,
) -> list[dict[str, Any]]:
    """Block entries when price has already covered the configured path to TP."""
    if not 0 < progress_threshold <= 1:
        raise ValueError("progress_threshold must be greater than 0 and at most 1")
    size = len(candles)
    for values, name in (
        (entry_signals, "entry_signals"),
        (entry_prices, "entry_prices"),
        (take_profits, "take_profits"),
    ):
        if values is not None and len(values) != size:
            raise ValueError(f"{name} must have the same length as candles")
    signals = entry_signals or ["none"] * size
    prices = entry_prices or [None] * size
    targets = take_profits or [None] * size
    result: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        signal = str(signals[index]).lower()
        if signal not in {"buy", "sell", "none"}:
            raise ValueError("entry signals must be 'buy', 'sell', or 'none'")
        entry = prices[index]
        target = targets[index]
        progress: float | None = None
        status = "none"
        reason = "no_entry_signal"
        if signal != "none":
            if entry is None or target is None:
                status = "unknown"
                reason = "entry_price_and_take_profit_required"
            else:
                entry_value, target_value = float(entry), float(target)
                distance = target_value - entry_value
                if (signal == "buy" and distance <= 0) or (
                    signal == "sell" and distance >= 0
                ):
                    raise ValueError(
                        "take profit must be above entry for buy and below for sell"
                    )
                current = float(candle["close"])
                progress = (current - entry_value) / distance
                status = "avoid" if progress > progress_threshold else "allow"
                reason = (
                    "late_entry_after_threshold"
                    if status == "avoid"
                    else "entry_within_threshold"
                )
        result.append(
            {
                "fomo_filter": status,
                "fomo_signal": signal,
                "fomo_entry_price": entry,
                "fomo_take_profit": target,
                "fomo_progress": progress,
                "fomo_progress_threshold": progress_threshold,
                "fomo_reason": reason,
            }
        )
    return result


def _read_entries(
    path: Path | None, size: int
) -> tuple[list[str], list[float | None], list[float | None]]:
    if path is None:
        return ["none"] * size, [None] * size, [None] * size
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        isinstance(payload, dict)
        and {"signal", "entry_price", "take_profit"} <= payload.keys()
    ):
        entries = [payload] * size
    else:
        entries = (
            payload.get("entries", payload) if isinstance(payload, dict) else payload
        )
    if not isinstance(entries, list):
        raise ValueError("entries JSON must contain a list or an 'entries' list")
    signals: list[str] = []
    prices: list[float | None] = []
    targets: list[float | None] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each entry must be an object")
        signals.append(str(entry.get("signal", "none")))
        prices.append(entry.get("entry_price"))
        targets.append(entry.get("take_profit"))
    if len(entries) != size:
        raise ValueError("entries must have the same length as candles")
    return signals, prices, targets


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    return (
        input_path.parent
        / f"{symbol}_fomo_filter_{timeframe}_{datetime.now().strftime('%Y%m%d_%H.%M.%S')}.json"
    )


def save_fomo_filter(
    input_path: Path, entries_path: Path | None = None, **settings: Any
) -> Path:
    candles = load_candles(input_path)
    signals, prices, targets = _read_entries(entries_path, len(candles))
    values = calculate_fomo_filter(candles, signals, prices, targets, **settings)
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_fomo_filter_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [
                {**candle, **value}
                for candle, value in zip(candles, values, strict=True)
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    for old_output in old_outputs:
        if old_output != output_path:
            old_output.unlink()
    return output_path


def latest_input_file(data_dir: Path, symbol: str, timeframe: str) -> Path:
    candidates = list(data_dir.glob(f"{symbol}_{timeframe}_*.json"))
    if not candidates:
        raise FileNotFoundError(f"No market-data file found for {symbol} {timeframe}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--entries-file", type=Path)
    parser.add_argument("--progress-threshold", type=float, default=PROGRESS_THRESHOLD)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    args = parser.parse_args()
    input_argument = args.input or DEFAULT_DATA_DIR
    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        print(
            f"Output saved: {save_fomo_filter(input_path, args.entries_file, progress_threshold=args.progress_threshold)}",
            flush=True,
        )
        if args.once:
            return
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - (time.monotonic() - started_at)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)

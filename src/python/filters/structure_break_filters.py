"""Configurable lower-timeframe ChoCh/BOS confirmation filter."""

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

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MIN_BREAK_DISTANCE = 0.0
HTF_MA_PERIOD = 50
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"
DEFAULT_HTF_TIMEFRAME = "H4"


def _validate_settings(
    pivot_left: int, pivot_right: int, min_break_distance: float
) -> None:
    if pivot_left < 1 or pivot_right < 1:
        raise ValueError("pivot_left and pivot_right must be at least 1")
    if min_break_distance < 0:
        raise ValueError("min_break_distance must be non-negative")


def _ema(values: list[float], period: int) -> float | None:
    if period < 1:
        raise ValueError("htf_ma_period must be at least 1")
    if len(values) < period:
        return None
    current = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def infer_htf_direction(candles: list[dict[str, Any]], ma_period: int) -> str:
    """Infer the higher-timeframe direction from the latest close and EMA."""
    closes = [float(candle["close"]) for candle in candles]
    ema = _ema(closes, ma_period)
    if ema is None:
        return "unknown"
    if closes[-1] > ema:
        return "buy"
    if closes[-1] < ema:
        return "sell"
    return "unknown"


def _pivot_high(
    candles: list[dict[str, Any]], index: int, left: int, right: int
) -> bool:
    high = float(candles[index]["high"])
    return all(
        high > float(candles[other]["high"])
        for other in range(index - left, index + right + 1)
        if other != index
    )


def _pivot_low(
    candles: list[dict[str, Any]], index: int, left: int, right: int
) -> bool:
    low = float(candles[index]["low"])
    return all(
        low < float(candles[other]["low"])
        for other in range(index - left, index + right + 1)
        if other != index
    )


def calculate_structure_break_filter(
    candles: list[dict[str, Any]],
    htf_direction: str,
    entry_signals: list[str] | None = None,
    pivot_left: int = PIVOT_LEFT,
    pivot_right: int = PIVOT_RIGHT,
    min_break_distance: float = MIN_BREAK_DISTANCE,
) -> list[dict[str, Any]]:
    """Allow entries only after a confirmed BOS aligned with the HTF direction."""
    _validate_settings(pivot_left, pivot_right, min_break_distance)
    direction = htf_direction.lower()
    if direction not in {"buy", "sell", "unknown"}:
        raise ValueError("htf_direction must be 'buy', 'sell', or 'unknown'")
    if entry_signals is not None and len(entry_signals) != len(candles):
        raise ValueError("entry_signals must have the same length as candles")
    signals = entry_signals or ["none"] * len(candles)
    result: list[dict[str, Any]] = []
    last_high: float | None = None
    last_low: float | None = None
    confirmed_bos: str | None = None
    broken_high: float | None = None
    broken_low: float | None = None

    for index, candle in enumerate(candles):
        if index >= pivot_left + pivot_right:
            pivot_index = index - pivot_right
            if _pivot_high(candles, pivot_index, pivot_left, pivot_right):
                last_high = float(candles[pivot_index]["high"])
            if _pivot_low(candles, pivot_index, pivot_left, pivot_right):
                last_low = float(candles[pivot_index]["low"])

        close = float(candle["close"])
        bullish_break = (
            last_high is not None
            and close > last_high + min_break_distance
            and last_high != broken_high
        )
        bearish_break = (
            last_low is not None
            and close < last_low - min_break_distance
            and last_low != broken_low
        )
        event = "none"
        if bullish_break:
            event = "BOS" if confirmed_bos in {None, "buy"} else "ChoCh"
            confirmed_bos = "buy"
            broken_high = last_high
        elif bearish_break:
            event = "BOS" if confirmed_bos in {None, "sell"} else "ChoCh"
            confirmed_bos = "sell"
            broken_low = last_low

        signal = signals[index].lower()
        if signal not in {"buy", "sell", "none"}:
            raise ValueError("entry signals must be 'buy', 'sell', or 'none'")
        aligned = signal == direction and confirmed_bos == direction
        status = (
            "allow"
            if aligned
            else "avoid"
            if signal != "none" and signal != direction
            else "wait"
            if signal != "none"
            and direction != "unknown"
            and confirmed_bos != direction
            else "avoid"
            if signal != "none"
            else "none"
        )
        result.append(
            {
                "structure_break_filter": status,
                "structure_break_signal": signal,
                "htf_direction": direction,
                "structure_event": event,
                "structure_direction": confirmed_bos or "unknown",
                "bos_confirmed": confirmed_bos == direction,
                "last_swing_high": last_high,
                "last_swing_low": last_low,
                "pivot_left": pivot_left,
                "pivot_right": pivot_right,
                "min_break_distance": min_break_distance,
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    stamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    return (
        input_path.parent / f"{symbol}_structure_break_filter_{timeframe}_{stamp}.json"
    )


def latest_input_file(data_dir: Path, symbol: str, timeframe: str) -> Path:
    candidates = list(data_dir.glob(f"{symbol}_{timeframe}_*.json"))
    if not candidates:
        raise FileNotFoundError(f"No market-data file found for {symbol} {timeframe}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def save_structure_break_filter(
    input_path: Path,
    htf_direction: str,
    signals_path: Path | None = None,
    **settings: Any,
) -> Path:
    candles = load_candles(input_path)
    signals: list[str] | None = None
    if signals_path is not None:
        payload = json.loads(signals_path.read_text(encoding="utf-8"))
        signals = (
            payload.get("signals", payload) if isinstance(payload, dict) else payload
        )
        if not isinstance(signals, list):
            raise ValueError("signals JSON must contain a list or a 'signals' list")
    values = calculate_structure_break_filter(
        candles, htf_direction, signals, **settings
    )
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_structure_break_filter_{timeframe}_*.json")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("--htf-input", type=Path)
    parser.add_argument("--htf-direction", choices=("buy", "sell", "unknown"))
    parser.add_argument("--signals-file", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--htf-timeframe", default=DEFAULT_HTF_TIMEFRAME)
    parser.add_argument("--htf-ma-period", type=int, default=HTF_MA_PERIOD)
    parser.add_argument("--pivot-left", type=int, default=PIVOT_LEFT)
    parser.add_argument("--pivot-right", type=int, default=PIVOT_RIGHT)
    parser.add_argument("--min-break-distance", type=float, default=MIN_BREAK_DISTANCE)
    args = parser.parse_args()
    input_argument = args.input or DEFAULT_DATA_DIR
    htf_input = args.htf_input
    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        if htf_input is None:
            htf_input = latest_input_file(
                DEFAULT_DATA_DIR, args.symbol, args.htf_timeframe
            )
        direction = args.htf_direction or infer_htf_direction(
            load_candles(htf_input), args.htf_ma_period
        )
        settings = {
            "pivot_left": args.pivot_left,
            "pivot_right": args.pivot_right,
            "min_break_distance": args.min_break_distance,
        }
        print(
            f"Output saved: {save_structure_break_filter(input_path, direction, args.signals_file, **settings)}",
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

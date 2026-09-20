"""Configurable DXY and intermarket-correlation risk filters."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indicators"))

from indicators.common import closes, load_candles

EMA_PERIOD = 20
EMA_SLOPE_LOOKBACK = 3
DXY_SLOPE_THRESHOLD = 0.0
CORRELATION_WINDOW = 50
CORRELATION_THRESHOLD = 0.80
MAX_CORRELATED_POSITIONS = 1
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"


def _ema(values: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("ema_period must be at least 1")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def _pearson(first: list[float], second: list[float]) -> float:
    if len(first) != len(second) or len(first) < 2:
        raise ValueError("correlation series must have equal length of at least 2")
    first_mean, second_mean = sum(first) / len(first), sum(second) / len(second)
    numerator = sum(
        (a - first_mean) * (b - second_mean) for a, b in zip(first, second, strict=True)
    )
    first_deviation = math.sqrt(sum((a - first_mean) ** 2 for a in first))
    second_deviation = math.sqrt(sum((b - second_mean) ** 2 for b in second))
    return (
        numerator / (first_deviation * second_deviation)
        if first_deviation and second_deviation
        else 0.0
    )


def dxy_filter(
    dxy_candles: list[dict[str, Any]],
    target_direction: str,
    ema_period: int = EMA_PERIOD,
    slope_lookback: int = EMA_SLOPE_LOOKBACK,
    slope_threshold: float = DXY_SLOPE_THRESHOLD,
) -> dict[str, Any]:
    """Return whether a target USD pair direction agrees with the DXY trend."""
    if target_direction not in {"buy", "sell"}:
        raise ValueError("target_direction must be 'buy' or 'sell'")
    if slope_lookback < 1:
        raise ValueError("slope_lookback must be at least 1")
    prices = closes(dxy_candles)
    ema_values = _ema(prices, ema_period)
    if not ema_values or ema_values[-1] is None or len(prices) <= slope_lookback:
        return {
            "dxy_filter": "unknown",
            "dxy_trend": "unknown",
            "dxy_ema": ema_values[-1] if ema_values else None,
        }
    current_ema = ema_values[-1]
    previous_ema = ema_values[-1 - slope_lookback]
    slope = current_ema - previous_ema if previous_ema is not None else None
    if slope is None:
        return {
            "dxy_filter": "unknown",
            "dxy_trend": "unknown",
            "dxy_ema": current_ema,
        }
    dxy_trend = (
        "rising"
        if slope > slope_threshold
        else "falling"
        if slope < -slope_threshold
        else "flat"
    )
    # For major USD quote pairs, a rising DXY opposes a buy and supports a sell.
    allowed = (target_direction == "buy" and dxy_trend == "falling") or (
        target_direction == "sell" and dxy_trend == "rising"
    )
    return {
        "dxy_filter": "allow" if allowed else "avoid",
        "dxy_trend": dxy_trend,
        "dxy_ema": ema_values[-1],
        "dxy_slope": slope,
        "dxy_ema_period": ema_period,
    }


def filter_correlated_positions(
    signals: list[dict[str, Any]],
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    correlation_window: int = CORRELATION_WINDOW,
    correlation_threshold: float = CORRELATION_THRESHOLD,
    max_correlated_positions: int = MAX_CORRELATED_POSITIONS,
) -> list[dict[str, Any]]:
    """Reject later same-direction signals whose absolute return correlation is excessive."""
    if correlation_window < 2 or not 0 <= correlation_threshold <= 1:
        raise ValueError(
            "correlation_window must be at least 2 and threshold must be 0..1"
        )
    if max_correlated_positions < 1:
        raise ValueError("max_correlated_positions must be at least 1")
    accepted: list[dict[str, Any]] = []
    for signal in signals:
        symbol, direction = signal.get("symbol"), signal.get("direction")
        if direction not in {"buy", "sell"} or symbol not in candles_by_symbol:
            accepted.append({**signal, "correlation_filter": "unknown"})
            continue
        current_prices = closes(candles_by_symbol[symbol])[-correlation_window:]
        current_returns = [
            current_prices[index] / current_prices[index - 1] - 1
            for index in range(1, len(current_prices))
        ]
        correlated_count = 0
        max_correlation = 0.0
        for previous in accepted:
            if (
                previous.get("direction") != direction
                or previous.get("symbol") not in candles_by_symbol
            ):
                continue
            previous_prices = closes(candles_by_symbol[previous["symbol"]])[
                -correlation_window:
            ]
            length = min(len(current_returns), len(previous_prices) - 1)
            if length < 2:
                continue
            correlation = _pearson(
                current_returns[-length:],
                [
                    previous_prices[index] / previous_prices[index - 1] - 1
                    for index in range(
                        len(previous_prices) - length, len(previous_prices)
                    )
                ],
            )
            max_correlation = max(max_correlation, abs(correlation))
            if abs(correlation) >= correlation_threshold:
                correlated_count += 1
        allowed = correlated_count < max_correlated_positions
        accepted.append(
            {
                **signal,
                "correlation_filter": "allow" if allowed else "avoid",
                "correlated_positions": correlated_count,
                "max_correlation": max_correlation,
            }
        )
    return accepted


def save_correlated_positions(path: Path, output_path: Path, **settings: Any) -> Path:
    """Filter a JSON list of proposed positions and atomically replace its output."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    signals = payload["signals"] if isinstance(payload, dict) else payload
    candles_by_symbol = {
        symbol: load_candles(Path(file_path))
        for symbol, file_path in settings.pop("market_data").items()
    }
    result = filter_correlated_positions(signals, candles_by_symbol, **settings)
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("signals", type=Path, nargs="?")
    parser.add_argument("--market-data", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--correlation-window", type=int, default=CORRELATION_WINDOW)
    parser.add_argument(
        "--correlation-threshold", type=float, default=CORRELATION_THRESHOLD
    )
    parser.add_argument(
        "--max-correlated-positions", type=int, default=MAX_CORRELATED_POSITIONS
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.signals is None or args.market_data is None or args.output is None:
        parser.print_help()
        print(
            "\nExample:\n"
            "py filters\\intermarket_macro_filters.py signals.json "
            "--market-data EURUSD.json GBPUSD.json --output filtered_signals.json --once",
            flush=True,
        )
        return
    market_data = {path.stem.split("_")[0]: str(path) for path in args.market_data}
    settings = {
        "market_data": market_data,
        "correlation_window": args.correlation_window,
        "correlation_threshold": args.correlation_threshold,
        "max_correlated_positions": args.max_correlated_positions,
    }
    while True:
        started_at = time.monotonic()
        print(
            f"Output saved: {save_correlated_positions(args.signals, args.output, **settings)}",
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

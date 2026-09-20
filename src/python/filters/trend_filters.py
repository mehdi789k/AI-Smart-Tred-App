"""Configurable moving-average and ADX trend filters."""

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

from indicators.common import highs, load_candles, lows, rolling_mean, true_ranges

MA_PERIOD = 200
MA_METHOD = "ema"
PRICE_FIELD = "close"
ADX_PERIOD = 14
ADX_STRONG_THRESHOLD = 25.0
ADX_WEAK_THRESHOLD = 20.0
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_ema(prices: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("ma_period must be at least 1")
    result: list[float | None] = [None] * len(prices)
    if len(prices) < period:
        return result
    current = sum(prices[:period]) / period
    result[period - 1] = current
    multiplier = 2 / (period + 1)
    for index in range(period, len(prices)):
        current = (prices[index] - current) * multiplier + current
        result[index] = current
    return result


def calculate_trend_filter(
    candles: list[dict[str, Any]],
    ma_period: int = MA_PERIOD,
    ma_method: str = MA_METHOD,
    price_field: str = PRICE_FIELD,
    adx_period: int = ADX_PERIOD,
    strong_threshold: float = ADX_STRONG_THRESHOLD,
    weak_threshold: float = ADX_WEAK_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return trend direction and actionable filter status for each candle."""
    if ma_period < 1 or adx_period < 1:
        raise ValueError("ma_period and adx_period must be at least 1")
    if ma_method not in {"sma", "ema"}:
        raise ValueError("ma_method must be 'sma' or 'ema'")
    if price_field not in {"open", "high", "low", "close"}:
        raise ValueError("price_field must be open, high, low, or close")
    if weak_threshold > strong_threshold:
        raise ValueError("weak_threshold must not exceed strong_threshold")

    prices = [float(candle[price_field]) for candle in candles]
    ma_values = (
        rolling_mean(prices, ma_period)
        if ma_method == "sma"
        else calculate_ema(prices, ma_period)
    )
    high_values, low_values = highs(candles), lows(candles)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for index in range(1, len(candles)):
        upward = high_values[index] - high_values[index - 1]
        downward = low_values[index - 1] - low_values[index]
        plus_dm.append(upward if upward > downward and upward > 0 else 0.0)
        minus_dm.append(downward if downward > upward and downward > 0 else 0.0)
    atr = rolling_mean(true_ranges(candles), adx_period)
    plus_average = rolling_mean(plus_dm, adx_period)
    minus_average = rolling_mean(minus_dm, adx_period)
    plus_di = [
        100 * plus / current_atr
        if plus is not None and current_atr is not None and current_atr != 0
        else None
        for plus, current_atr in zip(plus_average, atr, strict=True)
    ]
    minus_di = [
        100 * minus / current_atr
        if minus is not None and current_atr is not None and current_atr != 0
        else None
        for minus, current_atr in zip(minus_average, atr, strict=True)
    ]
    dx = [
        100 * abs(plus - minus) / (plus + minus)
        if plus is not None and minus is not None and plus + minus
        else None
        for plus, minus in zip(plus_di, minus_di, strict=True)
    ]
    adx_values = rolling_mean([value or 0.0 for value in dx], adx_period)

    result = []
    for price, ma, adx in zip(prices, ma_values, adx_values, strict=True):
        if ma is None or adx is None:
            status, direction, strength = "unknown", "unknown", "unknown"
        else:
            direction = "buy" if price > ma else "sell" if price < ma else "neutral"
            strength = (
                "strong"
                if adx >= strong_threshold
                else "developing"
                if adx >= weak_threshold
                else "weak"
            )
            status = (
                direction
                if adx >= strong_threshold and direction != "neutral"
                else "avoid"
            )
        result.append(
            {
                "ma": ma,
                "ma_period": ma_period,
                "ma_method": ma_method,
                "ma_price_field": price_field,
                "adx": adx,
                "adx_period": adx_period,
                "adx_strength": strength,
                "trend_direction": direction,
                "trend_filter": status,
                "adx_strong_threshold": strong_threshold,
                "adx_weak_threshold": weak_threshold,
            }
        )
    return result


def _output_path(input_path: Path) -> Path:
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    return input_path.parent / f"{symbol}_trend_filter_{timeframe}_{timestamp}.json"


def save_trend_filter(input_path: Path, **settings: Any) -> Path:
    candles = load_candles(input_path)
    filters = calculate_trend_filter(candles, **settings)
    output_path = _output_path(input_path)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_trend_filter_{timeframe}_*.json")
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            [
                {**candle, **filter_values}
                for candle, filter_values in zip(candles, filters, strict=True)
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
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--ma-period", type=int, default=MA_PERIOD)
    parser.add_argument("--ma-method", choices=("sma", "ema"), default=MA_METHOD)
    parser.add_argument(
        "--price-field", choices=("open", "high", "low", "close"), default=PRICE_FIELD
    )
    parser.add_argument("--adx-period", type=int, default=ADX_PERIOD)
    parser.add_argument(
        "--adx-strong-threshold", type=float, default=ADX_STRONG_THRESHOLD
    )
    parser.add_argument("--adx-weak-threshold", type=float, default=ADX_WEAK_THRESHOLD)
    args = parser.parse_args()
    settings = {
        "ma_period": args.ma_period,
        "ma_method": args.ma_method,
        "price_field": args.price_field,
        "adx_period": args.adx_period,
        "strong_threshold": args.adx_strong_threshold,
        "weak_threshold": args.adx_weak_threshold,
    }
    input_argument = args.input or DEFAULT_DATA_DIR
    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument, args.symbol, args.timeframe)
            if input_argument.is_dir()
            else input_argument
        )
        print(f"Output saved: {save_trend_filter(input_path, **settings)}", flush=True)
        if args.once:
            return
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - (time.monotonic() - started_at)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)

"""Stochastic oscillator with automatic market-data updates."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # Support both ``python indicators/stochastic.py`` and package imports.
    from common import highs, load_candles, lows, rolling_mean, true_ranges
    from moving_average import calculate_sma
except ImportError:  # pragma: no cover - exercised by package-based callers
    from .common import highs, load_candles, lows, rolling_mean, true_ranges

    def calculate_sma(prices: list[float], period: int) -> list[float | None]:
        """Local package-safe SMA fallback (moving_average is script-oriented)."""
        return rolling_mean(prices, period)


# Customize these values for the desired trading strategy.
STOCHASTIC_PERIOD = 14
SMOOTHING_PERIOD = 3
SIGNAL_PERIOD = 3
OVERBOUGHT_LEVEL = 80.0
OVERSOLD_LEVEL = 20.0
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25.0
REQUIRE_DI_DIRECTION = True
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_stochastic(
    candles: list[dict[str, Any]],
    period: int = STOCHASTIC_PERIOD,
    smoothing: int = SMOOTHING_PERIOD,
    signal_period: int = SIGNAL_PERIOD,
    overbought: float = OVERBOUGHT_LEVEL,
    oversold: float = OVERSOLD_LEVEL,
    adx_period: int = ADX_PERIOD,
    adx_threshold: float = ADX_TREND_THRESHOLD,
    require_di_direction: bool = REQUIRE_DI_DIRECTION,
) -> list[dict[str, float | str | None]]:
    """Calculate stochastic values and the bullish trending pullback rule.

    A buy is possible only after %K has visited oversold while a strong
    bullish trend is present, and then crosses above %D.  ADX and DI are
    calculated here to keep this module usable as a standalone script.
    """
    if period < 1 or smoothing < 1 or signal_period < 1:
        raise ValueError("stochastic periods must be at least 1")
    if not 0 <= oversold < overbought <= 100:
        raise ValueError(
            "stochastic levels must satisfy 0 <= oversold < overbought <= 100"
        )
    if adx_period < 1 or adx_threshold < 0:
        raise ValueError("adx_period must be positive and adx_threshold non-negative")

    high_values, low_values = highs(candles), lows(candles)
    raw: list[float | None] = []
    for index, candle in enumerate(candles):
        start = max(0, index - period + 1)
        window_high = max(high_values[start : index + 1])
        window_low = min(low_values[start : index + 1])
        raw.append(
            100 * (float(candle["close"]) - window_low) / (window_high - window_low)
            if index >= period - 1 and window_high != window_low
            else None
        )
    smoothed = calculate_sma(
        [value if value is not None else 0.0 for value in raw], smoothing
    )
    d_values = calculate_sma(
        [value if value is not None else 0.0 for value in smoothed], signal_period
    )

    # Wilder-style directional movement (the same convention as indicators/adx.py).
    plus_dm, minus_dm = [0.0], [0.0]
    for index in range(1, len(candles)):
        up = high_values[index] - high_values[index - 1]
        down = low_values[index - 1] - low_values[index]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr = rolling_mean(true_ranges(candles), adx_period)
    plus_avg = rolling_mean(plus_dm, adx_period)
    minus_avg = rolling_mean(minus_dm, adx_period)
    plus_di = [100 * p / a if a else None for p, a in zip(plus_avg, atr, strict=True)]
    minus_di = [100 * m / a if a else None for m, a in zip(minus_avg, atr, strict=True)]
    dx = [
        100 * abs(p - m) / (p + m)
        if p is not None and m is not None and p + m
        else None
        for p, m in zip(plus_di, minus_di, strict=True)
    ]
    adx_values = rolling_mean(
        [value if value is not None else 0.0 for value in dx], adx_period
    )

    result = []
    pullback_armed = False
    for index, (k_value, smooth_value, d_value) in enumerate(
        zip(raw, smoothed, d_values, strict=True)
    ):
        adx_value = adx_values[index]
        current_plus_di = plus_di[index]
        current_minus_di = minus_di[index]
        bullish_di = (
            current_plus_di is not None
            and current_minus_di is not None
            and current_plus_di > current_minus_di
        )
        trend_up = (
            adx_value is not None
            and adx_value >= adx_threshold
            and (bullish_di if require_di_direction else True)
        )
        previous_k = smoothed[index - 1] if index else None
        previous_d = d_values[index - 1] if index else None
        crossover = bool(
            trend_up
            and smooth_value is not None
            and d_value is not None
            and previous_k is not None
            and previous_d is not None
            and previous_k <= previous_d
            and smooth_value > d_value
        )
        if trend_up and smooth_value is not None and smooth_value <= oversold:
            pullback_armed = True
        elif not trend_up:
            pullback_armed = False
        trade_allowed = bool(trend_up and pullback_armed and crossover)
        if trade_allowed:
            pullback_state = "triggered"
            pullback_armed = False
        elif pullback_armed:
            pullback_state = "armed"
        elif trend_up:
            pullback_state = "waiting_for_oversold"
        else:
            pullback_state = "inactive"
        level = (
            "overbought"
            if smooth_value is not None and smooth_value >= overbought
            else "oversold"
            if smooth_value is not None and smooth_value <= oversold
            else "neutral"
            if smooth_value is not None
            else "unknown"
        )
        result.append(
            {
                "stochastic_k": k_value,
                "stochastic_k_smoothed": smooth_value,
                "stochastic_d": d_value,
                "stochastic_signal": level,
                "adx": adx_value,
                "plus_di": plus_di[index],
                "minus_di": minus_di[index],
                "trend": "strong_bullish" if trend_up else "not_strong_bullish",
                "trend_status": "strong_bullish" if trend_up else "not_strong_bullish",
                "trend_up": trend_up,
                "pullback_state": pullback_state,
                "bullish_crossover": crossover,
                "crossover": "bullish" if crossover else "none",
                "trade_allowed": trade_allowed,
            }
        )
    return result


def _symbol_and_timeframe(input_path: Path) -> tuple[str, str]:
    parts = input_path.stem.split("_")
    return (
        ("_".join(parts[:-3]), parts[-3]) if len(parts) >= 4 else ("market", "unknown")
    )


def save_stochastic(
    input_path: Path,
    period: int = STOCHASTIC_PERIOD,
    smoothing: int = SMOOTHING_PERIOD,
    signal_period: int = SIGNAL_PERIOD,
    overbought: float = OVERBOUGHT_LEVEL,
    oversold: float = OVERSOLD_LEVEL,
    adx_period: int = ADX_PERIOD,
    adx_threshold: float = ADX_TREND_THRESHOLD,
    require_di_direction: bool = REQUIRE_DI_DIRECTION,
) -> Path:
    candles = load_candles(input_path)
    indicators = calculate_stochastic(
        candles,
        period,
        smoothing,
        signal_period,
        overbought,
        oversold,
        adx_period,
        adx_threshold,
        require_di_direction,
    )
    symbol, timeframe = _symbol_and_timeframe(input_path)
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = (
        input_path.parent / f"{symbol}_stochastic_{timeframe}_{timestamp}.json"
    )
    old_outputs = list(
        input_path.parent.glob(f"{symbol}_stochastic_{timeframe}_*.json")
    )
    payload = [
        {
            **candle,
            **indicator,
            "stochastic_period": period,
            "stochastic_smoothing_period": smoothing,
            "stochastic_signal_period": signal_period,
            "stochastic_overbought": overbought,
            "stochastic_oversold": oversold,
            "stochastic_adx_period": adx_period,
            "stochastic_adx_threshold": adx_threshold,
            "stochastic_require_di_direction": require_di_direction,
        }
        for candle, indicator in zip(candles, indicators, strict=True)
    ]
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
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
    parser.add_argument(
        "input", nargs="?", type=Path, help="JSON file or market_data directory"
    )
    parser.add_argument("--once", action="store_true", help="run only one update")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--period", type=int, default=STOCHASTIC_PERIOD)
    parser.add_argument("--smoothing", type=int, default=SMOOTHING_PERIOD)
    parser.add_argument("--signal-period", type=int, default=SIGNAL_PERIOD)
    parser.add_argument("--overbought", type=float, default=OVERBOUGHT_LEVEL)
    parser.add_argument("--oversold", type=float, default=OVERSOLD_LEVEL)
    parser.add_argument("--adx-period", type=int, default=ADX_PERIOD)
    parser.add_argument("--adx-threshold", type=float, default=ADX_TREND_THRESHOLD)
    parser.add_argument(
        "--no-di-direction",
        action="store_true",
        help="allow ADX strength without requiring +DI > -DI",
    )
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
            f"Output saved: {save_stochastic(input_path, args.period, args.smoothing, args.signal_period, args.overbought, args.oversold, args.adx_period, args.adx_threshold, not args.no_di_direction)}",
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

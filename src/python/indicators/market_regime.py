"""Classify market regime using moving-average trend and ATR volatility."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import closes, load_candles, rolling_mean, true_ranges

TREND_PERIOD = 50
ATR_PERIOD = 14
VOLATILITY_LOOKBACK = 50
UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "market_data"
DEFAULT_SYMBOL = "XAUUSD_l"
DEFAULT_TIMEFRAME = "M5"


def calculate_market_regime(
    candles: list[dict[str, Any]],
    trend_period: int = TREND_PERIOD,
    atr_period: int = ATR_PERIOD,
    volatility_lookback: int = VOLATILITY_LOOKBACK,
) -> list[dict[str, Any]]:
    prices = closes(candles)
    trend = rolling_mean(prices, trend_period)
    atr = rolling_mean(true_ranges(candles), atr_period)
    atr_baseline = rolling_mean([value or 0.0 for value in atr], volatility_lookback)
    result = []
    for candle, price, average, current_atr, baseline in zip(
        candles, prices, trend, atr, atr_baseline, strict=True
    ):
        if average is None or current_atr is None or baseline is None:
            regime = "unknown"
        elif price > average and current_atr <= baseline:
            regime = "bullish_low_volatility"
        elif price > average:
            regime = "bullish_high_volatility"
        elif price < average and current_atr <= baseline:
            regime = "bearish_low_volatility"
        elif price < average:
            regime = "bearish_high_volatility"
        else:
            regime = "range"
        result.append(
            {**candle, "trend_ma": average, "atr": current_atr, "market_regime": regime}
        )
    return result


def save_market_regime(input_path: Path) -> Path:
    candles = load_candles(input_path)
    result = calculate_market_regime(candles)
    parts = input_path.stem.split("_")
    symbol = "_".join(parts[:-3]) if len(parts) >= 4 else "market"
    timeframe = parts[-3] if len(parts) >= 4 else "unknown"
    output_pattern = f"{symbol}_market_regime_{timeframe}_*.json"
    existing_outputs = sorted(
        input_path.parent.glob(output_pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H.%M.%S")
    output_path = input_path.parent / (
        f"{symbol}_market_regime_{timeframe}_{timestamp}.json"
    )
    temporary_path = output_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary_path.replace(output_path)
    for old_output in existing_outputs:
        if old_output != output_path:
            old_output.unlink()
    return output_path


def latest_input_file(
    data_dir: Path, symbol: str = DEFAULT_SYMBOL, timeframe: str = DEFAULT_TIMEFRAME
) -> Path:
    pattern = f"{symbol}_{timeframe}_*.json"
    candidates = list(data_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No market-data file found for {symbol} {timeframe}.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> None:
    arguments = [argument for argument in sys.argv[1:] if argument != "--once"]
    run_once = "--once" in sys.argv[1:]
    input_argument = Path(arguments[0]) if arguments else DEFAULT_DATA_DIR
    while True:
        started_at = time.monotonic()
        input_path = (
            latest_input_file(input_argument)
            if input_argument.is_dir()
            else input_argument
        )
        output_path = save_market_regime(input_path)
        print(f"Output saved: {output_path}", flush=True)
        if run_once:
            return
        elapsed = time.monotonic() - started_at
        time.sleep(max(0.0, UPDATE_INTERVAL_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAutomatic update stopped.", flush=True)

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from smc_filters import calculate_liquidity_sweeps


class SmcFilterTests(unittest.TestCase):
    def test_bullish_support_sweep_is_confirmed(self) -> None:
        candles = [
            {"open": 105, "high": 106, "low": 100, "close": 102},
            {"open": 102, "high": 105, "low": 101, "close": 104},
            {"open": 104, "high": 106, "low": 103, "close": 105},
            {"open": 101, "high": 108, "low": 99, "close": 104},
        ]
        result = calculate_liquidity_sweeps(candles, lookback=3, min_body_ratio=0.3)
        self.assertEqual(result[-1]["liquidity_sweep"], "buy")
        self.assertEqual(result[-1]["sweep_direction"], "bullish")

    def test_break_without_reclaim_is_blocked(self) -> None:
        candles = [
            {"open": 105, "high": 106, "low": 100, "close": 102},
            {"open": 102, "high": 105, "low": 101, "close": 104},
            {"open": 104, "high": 106, "low": 103, "close": 105},
            {"open": 101, "high": 103, "low": 98, "close": 99},
        ]
        self.assertEqual(
            calculate_liquidity_sweeps(candles, lookback=3)[-1]["liquidity_sweep"],
            "none",
        )

    def test_bearish_resistance_sweep_is_confirmed(self) -> None:
        candles = [
            {"open": 100, "high": 105, "low": 99, "close": 104},
            {"open": 104, "high": 106, "low": 103, "close": 105},
            {"open": 105, "high": 107, "low": 104, "close": 106},
            {"open": 108, "high": 110, "low": 104, "close": 106},
        ]
        result = calculate_liquidity_sweeps(candles, lookback=3, min_body_ratio=0.3)
        self.assertEqual(result[-1]["liquidity_sweep"], "sell")

    def test_invalid_settings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_liquidity_sweeps([], lookback=1)


if __name__ == "__main__":
    unittest.main()

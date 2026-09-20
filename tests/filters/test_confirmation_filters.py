import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from confirmation_filters import calculate_divergence, calculate_volume_confirmation


class ConfirmationFilterTests(unittest.TestCase):
    def test_volume_exhaustion_near_resistance_confirms_sell(self) -> None:
        candles = [
            {
                "open": 99 + index,
                "high": 101 + index,
                "low": 98 + index,
                "close": 100 + index,
                "tick_volume": 1000 - index * 100,
            }
            for index in range(5)
        ]
        result = calculate_volume_confirmation(candles, [104], volume_period=2)
        self.assertEqual(result[-1]["volume_confirmation"], "sell")

    def test_volume_without_levels_is_unknown(self) -> None:
        candles = [{"open": 1, "high": 2, "low": 0, "close": 1.5, "tick_volume": 100}]
        self.assertEqual(
            calculate_volume_confirmation(candles)[0]["volume_confirmation"], "unknown"
        )

    def test_divergence_output_contains_rsi_and_macd(self) -> None:
        candles = [
            {
                "open": 100 + (index % 3),
                "high": 102 + (index % 3),
                "low": 98 + (index % 3),
                "close": 100 + (index % 3),
            }
            for index in range(40)
        ]
        result = calculate_divergence(
            candles,
            rsi_period=5,
            macd_fast_period=3,
            macd_slow_period=6,
            macd_signal_period=2,
        )
        self.assertEqual(len(result), len(candles))
        self.assertIn("rsi", result[-1])
        self.assertIn("macd", result[-1])


if __name__ == "__main__":
    unittest.main()

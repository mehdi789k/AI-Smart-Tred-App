import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from fvg_filters import calculate_fvg_filter


class FvgFilterTests(unittest.TestCase):
    def test_unfilled_bullish_fvg_delays_buy(self) -> None:
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 100, "high": 102, "low": 100, "close": 101},
            {"open": 105, "high": 107, "low": 104, "close": 106},
            {"open": 106, "high": 109, "low": 105, "close": 108},
        ]
        result = calculate_fvg_filter(candles, ["none", "none", "none", "buy"])
        self.assertEqual(result[2]["fvg_type"], "bullish")
        self.assertEqual(result[3]["fvg_filter"], "wait")
        self.assertTrue(result[3]["fvg_unfilled"])

    def test_touching_gap_marks_it_filled(self) -> None:
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100},
            {"open": 100, "high": 102, "low": 100, "close": 101},
            {"open": 105, "high": 107, "low": 104, "close": 106},
            {"open": 104, "high": 106, "low": 101, "close": 103},
        ]
        result = calculate_fvg_filter(candles, ["none", "none", "none", "buy"])
        self.assertFalse(result[3]["fvg_unfilled"])
        self.assertEqual(result[3]["fvg_filter"], "allow")

    def test_bearish_fvg_delays_sell(self) -> None:
        candles = [
            {"open": 110, "high": 111, "low": 109, "close": 110},
            {"open": 110, "high": 110, "low": 108, "close": 109},
            {"open": 105, "high": 106, "low": 103, "close": 104},
            {"open": 104, "high": 105, "low": 101, "close": 102},
        ]
        result = calculate_fvg_filter(candles, ["none", "none", "none", "sell"])
        self.assertEqual(result[2]["fvg_type"], "bearish")
        self.assertEqual(result[3]["fvg_filter"], "wait")

    def test_invalid_signal_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_fvg_filter([], ["hold"])


if __name__ == "__main__":
    unittest.main()

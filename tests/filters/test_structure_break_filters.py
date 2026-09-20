import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from structure_break_filters import calculate_structure_break_filter


class StructureBreakFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candles = [
            {"open": 10, "high": 11, "low": 9, "close": 10},
            {"open": 10, "high": 12, "low": 10, "close": 11},
            {"open": 11, "high": 13, "low": 10.5, "close": 11.8},
            {"open": 10.5, "high": 10.8, "low": 9, "close": 10},
            {"open": 10, "high": 12.8, "low": 10, "close": 12.5},
            {"open": 12.5, "high": 13.2, "low": 12, "close": 13.5},
        ]

    def test_buy_waits_until_bullish_bos_then_allows(self) -> None:
        result = calculate_structure_break_filter(
            self.candles, "buy", ["none", "none", "none", "buy", "buy", "buy"]
        )
        self.assertEqual(result[3]["structure_break_filter"], "wait")
        self.assertEqual(result[5]["structure_event"], "BOS")
        self.assertEqual(result[5]["structure_break_filter"], "allow")

    def test_sell_is_blocked_against_bullish_htf(self) -> None:
        result = calculate_structure_break_filter(
            self.candles, "buy", ["none"] * 4 + ["sell", "sell"]
        )
        self.assertEqual(result[5]["structure_break_filter"], "avoid")

    def test_choch_updates_direction(self) -> None:
        candles = self.candles + [
            {"open": 13, "high": 13.1, "low": 8, "close": 8.8},
            {"open": 8.8, "high": 9.5, "low": 8.5, "close": 9},
        ]
        result = calculate_structure_break_filter(
            candles, "buy", ["none"] * 6 + ["sell", "sell"]
        )
        self.assertEqual(result[6]["structure_event"], "ChoCh")
        self.assertEqual(result[6]["structure_break_filter"], "avoid")

    def test_invalid_settings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_structure_break_filter(self.candles, "buy", pivot_left=0)


if __name__ == "__main__":
    unittest.main()

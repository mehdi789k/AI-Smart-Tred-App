import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

import intermarket_macro_filters
from intermarket_macro_filters import dxy_filter, filter_correlated_positions


def series(multiplier: float = 1.0) -> list[dict[str, float]]:
    return [{"close": 100 + index * multiplier} for index in range(80)]


class IntermarketMacroFilterTests(unittest.TestCase):
    def test_rising_dxy_blocks_buy_and_allows_sell(self) -> None:
        self.assertEqual(
            dxy_filter(series(), "buy", ema_period=5)["dxy_filter"], "avoid"
        )
        self.assertEqual(
            dxy_filter(series(), "sell", ema_period=5)["dxy_filter"], "allow"
        )

    def test_highly_correlated_same_direction_position_is_blocked(self) -> None:
        candles = {"EURUSD": series(), "GBPUSD": series(1.1)}
        result = filter_correlated_positions(
            [
                {"symbol": "EURUSD", "direction": "buy"},
                {"symbol": "GBPUSD", "direction": "buy"},
            ],
            candles,
            correlation_window=30,
            correlation_threshold=0.8,
        )
        self.assertEqual(result[0]["correlation_filter"], "allow")
        self.assertEqual(result[1]["correlation_filter"], "avoid")

    def test_opposite_direction_is_not_counted_as_duplicate_risk(self) -> None:
        result = filter_correlated_positions(
            [
                {"symbol": "EURUSD", "direction": "buy"},
                {"symbol": "GBPUSD", "direction": "sell"},
            ],
            {"EURUSD": series(), "GBPUSD": series(1.1)},
            correlation_window=30,
        )
        self.assertEqual(result[1]["correlation_filter"], "allow")

    def test_running_without_cli_arguments_prints_help(self) -> None:
        with (
            patch.object(sys, "argv", ["intermarket_macro_filters.py"]),
            patch("builtins.print") as output,
        ):
            intermarket_macro_filters.main()
        self.assertTrue(output.called)


if __name__ == "__main__":
    unittest.main()

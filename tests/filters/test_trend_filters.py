import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from trend_filters import calculate_ema, calculate_trend_filter, save_trend_filter


def candles(count: int = 80) -> list[dict[str, float]]:
    return [
        {
            "open": float(index),
            "high": float(index + 2),
            "low": float(index - 1),
            "close": float(index + 1),
        }
        for index in range(count)
    ]


class TrendFilterTests(unittest.TestCase):
    def test_ema_has_warmup_and_expected_values(self) -> None:
        self.assertEqual(calculate_ema([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_strong_uptrend_allows_buy_after_warmup(self) -> None:
        output = calculate_trend_filter(candles(), ma_period=5, adx_period=3)
        actionable = [item for item in output if item["trend_filter"] != "unknown"]
        self.assertTrue(actionable)
        self.assertTrue(all(item["trend_filter"] == "buy" for item in actionable))
        self.assertTrue(all(item["adx_strength"] == "strong" for item in actionable))

    def test_invalid_thresholds_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_trend_filter(candles(), strong_threshold=20, weak_threshold=25)

    def test_sma_and_custom_price_field_are_supported(self) -> None:
        output = calculate_trend_filter(
            candles(), ma_period=4, ma_method="sma", price_field="open", adx_period=3
        )
        self.assertEqual(output[-1]["ma_method"], "sma")
        self.assertEqual(output[-1]["ma_price_field"], "open")

    def test_repeated_updates_leave_only_latest_output(self) -> None:
        with TemporaryDirectory() as directory:
            input_path = Path(directory) / "TEST_M5_20260904_12.00.00.json"
            input_path.write_text(
                json.dumps({"candles": candles()}),
                encoding="utf-8",
            )
            first_output = save_trend_filter(input_path, ma_period=5, adx_period=3)
            time.sleep(1.05)
            second_output = save_trend_filter(input_path, ma_period=5, adx_period=3)
            outputs = list(Path(directory).glob("TEST_trend_filter_M5_*.json"))
            self.assertEqual(second_output, outputs[0])
            self.assertEqual(len(outputs), 1)
            self.assertNotEqual(first_output, second_output)


if __name__ == "__main__":
    unittest.main()

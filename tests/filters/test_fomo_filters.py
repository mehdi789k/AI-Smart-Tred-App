import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from fomo_filters import _read_entries, calculate_fomo_filter


class FomoFilterTests(unittest.TestCase):
    def test_buy_after_half_path_is_blocked(self) -> None:
        candles = [{"close": 106}, {"close": 151}]
        result = calculate_fomo_filter(candles, ["buy", "buy"], [100, 100], [200, 200])
        self.assertEqual(result[0]["fomo_filter"], "allow")
        self.assertEqual(result[1]["fomo_filter"], "avoid")
        self.assertGreater(result[1]["fomo_progress"], 0.5)

    def test_sell_uses_direction_aware_progress(self) -> None:
        result = calculate_fomo_filter([{"close": 40}], ["sell"], [100], [0])
        self.assertEqual(result[0]["fomo_filter"], "avoid")

    def test_missing_target_is_unknown(self) -> None:
        result = calculate_fomo_filter([{"close": 110}], ["buy"], [100], [None])
        self.assertEqual(result[0]["fomo_filter"], "unknown")

    def test_invalid_target_and_settings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            calculate_fomo_filter([{"close": 100}], ["buy"], [100], [90])
        with self.assertRaises(ValueError):
            calculate_fomo_filter([], progress_threshold=0)

    def test_single_entry_config_is_broadcast(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entries.json"
            path.write_text(
                json.dumps({"signal": "buy", "entry_price": 100, "take_profit": 200}),
                encoding="utf-8",
            )
            signals, prices, targets = _read_entries(path, 2)
        self.assertEqual(signals, ["buy", "buy"])
        self.assertEqual(prices, [100, 100])
        self.assertEqual(targets, [200, 200])


if __name__ == "__main__":
    unittest.main()

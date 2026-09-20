import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from structure_mtf_filters import (
    calculate_structure_mtf_filter,
    latest_input_file,
    load_zones,
)


def htf_candles() -> list[dict[str, float]]:
    return [
        {
            "time": float(index * 3600),
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
        }
        for index in range(8)
    ]


class StructureMtfFilterTests(unittest.TestCase):
    def test_bullish_pattern_in_demand_zone_is_allowed(self) -> None:
        ltf = [
            {"time": 18000.0, "open": 106, "high": 107, "low": 105, "close": 106},
            {"time": 18300.0, "open": 106.5, "high": 108, "low": 103, "close": 107.5},
        ]
        result = calculate_structure_mtf_filter(
            ltf,
            htf_candles(),
            [{"type": "demand", "lower": 107, "upper": 108}],
            ma_period=3,
        )
        self.assertEqual(result[-1]["mtf_filter"], "allow")
        self.assertEqual(result[-1]["htf_trend"], "bullish")

    def test_opposite_pattern_and_no_mans_land_are_blocked(self) -> None:
        ltf = [{"time": 18000.0, "open": 108, "high": 109, "low": 103, "close": 104}]
        result = calculate_structure_mtf_filter(
            ltf,
            htf_candles(),
            [{"type": "demand", "lower": 100, "upper": 101}],
            ma_period=3,
        )
        self.assertEqual(result[0]["mtf_filter"], "avoid")
        self.assertTrue(result[0]["no_mans_land"])

    def test_zone_file_validation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "zones.json"
            path.write_text(
                json.dumps({"zones": [{"type": "demand", "lower": 100, "upper": 101}]}),
                encoding="utf-8",
            )
            self.assertEqual(load_zones(path)[0]["type"], "demand")

    def test_latest_input_file_selects_requested_timeframe(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "TEST_M15_20260904_12.00.00.json").write_text(
                "[]", encoding="utf-8"
            )
            htf = root / "TEST_H4_20260904_12.00.00.json"
            htf.write_text("[]", encoding="utf-8")
            self.assertEqual(latest_input_file(root, "TEST", "H4"), htf)


if __name__ == "__main__":
    unittest.main()

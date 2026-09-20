import json
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "filters"))

from volatility_time_filters import (
    calculate_volatility_time_filter,
    save_volatility_time_filter,
)


def candles() -> list[dict[str, float | str]]:
    return [
        {
            "time_iso": f"2026-09-04T{15 + index // 12:02d}:{(index % 12) * 5:02d}:00",
            "high": index + 2.0,
            "low": index - 1.0,
            "close": index + 1.0,
        }
        for index in range(60)
    ]


class VolatilityTimeFilterTests(unittest.TestCase):
    def test_session_and_atr_filter_are_configurable(self) -> None:
        output = calculate_volatility_time_filter(
            candles(),
            atr_period=3,
            atr_lookback=10,
            session_start="15:30",
            session_end="19:30",
            timezone_offset_hours=0,
        )
        self.assertTrue(output[10]["in_trading_session"])
        self.assertIn(output[10]["volatility_time_filter"], {"allow", "avoid"})

    def test_high_impact_news_blocks_window(self) -> None:
        news = [datetime.fromisoformat("2026-09-04T16:00:00+00:00")]
        output = calculate_volatility_time_filter(
            candles(),
            news,
            atr_period=3,
            atr_lookback=10,
            news_window_minutes=30,
            timezone_offset_hours=0,
        )
        self.assertTrue(output[12]["high_impact_news_blocked"])
        self.assertEqual(output[12]["volatility_time_filter"], "avoid")

    def test_save_replaces_previous_output(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TEST_M5_20260904_12.00.00.json"
            path.write_text(json.dumps({"candles": candles()}), encoding="utf-8")
            save_volatility_time_filter(path, atr_period=3, atr_lookback=10)
            time.sleep(1.05)
            save_volatility_time_filter(path, atr_period=3, atr_lookback=10)
            self.assertEqual(
                len(
                    list(Path(directory).glob("TEST_volatility_time_filter_M5_*.json"))
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()

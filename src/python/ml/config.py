"""Configuration shared by training and live inference."""

import os
from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class MLConfig:
    """Explicit, conservative defaults; no value is inferred at runtime."""

    horizon: int = 10
    return_threshold: float = 0.005
    min_bars: int = 60
    model_path: str = ""
    expected_schema_version: str = "1.0"
    expected_symbol: str = ""
    expected_timeframe: str = ""
    feature_options: Tuple[str, ...] = field(default_factory=tuple)
    random_state: int = 42
    test_size: float = 0.15
    validation_size: float = 0.15
    source: str = "json"
    candle_source: str = ""
    database_url: str = ""
    symbol: str = "XAUUSD"
    timeframe: str = "M5"
    min_samples: int = 1000
    dataset_version: str = ""
    feature_schema_hash: str = ""
    walk_forward_splits: int = 5
    min_coverage_hours: float = 0.0
    gap_tolerance_minutes: int = 10
    max_unexpected_gaps: int = 0
    quality_report_path: str = ""
    fail_closed: bool = True

    def __post_init__(self) -> None:
        if self.horizon < 1 or self.min_bars < 2:
            raise ValueError("horizon and min_bars must be positive")
        if self.return_threshold < 0:
            raise ValueError("return_threshold must be non-negative")
        if self.source not in {"json", "database"}:
            raise ValueError("source must be 'json' or 'database'")
        if self.candle_source and not self.candle_source.strip():
            raise ValueError("candle_source must not be blank when provided")
        if self.min_samples < self.min_bars:
            raise ValueError("min_samples must be at least min_bars")
        if self.source == "database" and not self.database_url:
            raise ValueError("database_url is required when source='database'")
        if not self.symbol or not self.timeframe:
            raise ValueError("symbol and timeframe are required")
        if not 0 < self.test_size < 1 or not 0 <= self.validation_size < 1:
            raise ValueError("split sizes must be between zero and one")
        if self.test_size + self.validation_size >= 1:
            raise ValueError("test_size + validation_size must be less than one")
        if self.walk_forward_splits < 2:
            raise ValueError("walk_forward_splits must be at least two")
        if self.min_coverage_hours < 0 or self.gap_tolerance_minutes < 0:
            raise ValueError("coverage and gap tolerances must be non-negative")
        if self.max_unexpected_gaps < 0:
            raise ValueError("max_unexpected_gaps must be non-negative")

    @classmethod
    def from_env(cls, **overrides):
        """Build a safe config from ``ML_*`` environment variables.

        Explicit keyword arguments always win over environment values.
        """

        def value(name, default, cast):
            raw = os.getenv("ML_" + name.upper())
            return default if raw is None else cast(raw)

        fields = {
            "horizon": value("horizon", cls.horizon, int),
            "return_threshold": value("return_threshold", cls.return_threshold, float),
            "min_bars": value("min_bars", cls.min_bars, int),
            "min_samples": value("min_samples", cls.min_samples, int),
            "min_coverage_hours": value(
                "min_coverage_hours", cls.min_coverage_hours, float
            ),
            "gap_tolerance_minutes": value(
                "gap_tolerance_minutes", cls.gap_tolerance_minutes, int
            ),
            "max_unexpected_gaps": value(
                "max_unexpected_gaps", cls.max_unexpected_gaps, int
            ),
            "symbol": os.getenv("ML_SYMBOL", cls.symbol),
            "timeframe": os.getenv("ML_TIMEFRAME", cls.timeframe),
            "source": os.getenv("ML_SOURCE", cls.source),
            "candle_source": os.getenv("ML_CANDLE_SOURCE", cls.candle_source),
            "database_url": os.getenv(
                "ML_DATABASE_URL", os.getenv("DATABASE_URL", cls.database_url)
            ),
            "quality_report_path": os.getenv(
                "ML_QUALITY_REPORT_PATH", cls.quality_report_path
            ),
        }
        fields.update(overrides)
        return cls(**fields)

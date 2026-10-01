"""Minimal, leakage-safe training utilities compatible with train_model.py artifacts."""

from __future__ import annotations

import json
import os
import pickle
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from .config import MLConfig
from .utils import (
    build_training_features,
    feature_schema_hash,
    validate_ohlcv,
)


class DataSourceError(ValueError):
    """Raised when a configured training data source cannot be read safely."""


class DataQualityError(DataSourceError):
    """Raised when the training gate cannot prove data is safe to train on."""

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report


def assess_data_quality(
    frame: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    min_bars: int,
    min_coverage_hours: float = 0.0,
    gap_tolerance_minutes: int = 10,
    max_unexpected_gaps: int = 0,
    expected_gap_windows: Sequence[str] = (),
    source: str = "unknown",
    ingestion_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an audit report and fail closed on unsafe chronological OHLCV data."""
    report: dict[str, Any] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "source": source,
        "bars": int(len(frame)),
        "oldest": None,
        "newest": None,
        "status": "rejected",
        "expected_gaps": [],
        "unexpected_gaps": [],
        "duplicate_timestamps": 0,
        "timezone_utc": False,
        "ohlc_valid": False,
        "coverage_hours": 0.0,
        "ingestion_metadata": ingestion_metadata or {},
        "ingestion_metadata_coverage": {
            "present": bool(ingestion_metadata),
            "fields": sorted((ingestion_metadata or {}).keys()),
        },
        "expected_gap_windows": list(expected_gap_windows),
    }

    def reject(message: str) -> None:
        raise DataQualityError(message, report=report)

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        reject("training gate failed: no OHLCV bars")
    ts = frame["timestamp"] if "timestamp" in frame else frame.index
    naive = pd.Series(pd.to_datetime(ts, errors="coerce"))
    if getattr(naive.dt, "tz", None) is None:
        reject("training gate failed: timestamps must be timezone-aware UTC")
    parsed = naive.dt.tz_convert("UTC")
    if parsed.isna().any():
        reject("training gate failed: invalid or missing timestamps")
    # UTC conversion is deliberate; naive timestamps are not accepted.
    ordered = pd.Series(parsed).sort_values()
    report["oldest"], report["newest"] = (
        ordered.iloc[0].isoformat(),
        ordered.iloc[-1].isoformat(),
    )
    report["timezone_utc"] = True
    report["duplicate_timestamps"] = int(ordered.duplicated().sum())
    report["coverage_hours"] = float(
        (ordered.iloc[-1] - ordered.iloc[0]).total_seconds() / 3600
    )
    if report["duplicate_timestamps"]:
        reject("training gate failed: duplicate timestamps detected")
    if len(frame) < min_bars:
        reject(f"training gate failed: insufficient bars ({len(frame)} < {min_bars})")
    if not frame.index.is_monotonic_increasing and "timestamp" not in frame:
        reject("training gate failed: timestamps are not chronological")
    try:
        validate_ohlcv(frame, min_bars=min_bars)
        report["ohlc_valid"] = True
    except ValueError as exc:
        raise DataQualityError(
            f"training gate failed: OHLC validity: {exc}", report=report
        ) from exc
    expected_seconds = {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }.get(timeframe.upper())
    if expected_seconds:
        for prev, cur in zip(ordered.iloc[:-1], ordered.iloc[1:]):
            seconds = (cur - prev).total_seconds()
            missing = int(seconds // expected_seconds) - 1
            if missing > 0:
                gap = {
                    "from": prev.isoformat(),
                    "to": cur.isoformat(),
                    "missing_bars": missing,
                }
                # Weekends and short broker maintenance windows are expected.
                if (
                    prev.weekday() >= 4
                    or seconds <= gap_tolerance_minutes * 60
                    or _matches_expected_gap_window(prev, cur, expected_gap_windows)
                ):
                    report["expected_gaps"].append(gap)
                else:
                    report["unexpected_gaps"].append(gap)
    if report["coverage_hours"] < min_coverage_hours:
        reject(
            f"training gate failed: temporal coverage {report['coverage_hours']:.2f}h "
            f"is below {min_coverage_hours:.2f}h"
        )
    if len(report["unexpected_gaps"]) > max_unexpected_gaps:
        reject(
            f"training gate failed: {len(report['unexpected_gaps'])} unexpected gap(s) "
            f"(maximum allowed: {max_unexpected_gaps})"
        )
    report["status"] = "accepted"
    return report


def _matches_expected_gap_window(
    previous: pd.Timestamp,
    current: pd.Timestamp,
    windows: Sequence[str],
) -> bool:
    """Return whether a gap crosses a configured UTC session-break window."""
    previous_minutes = previous.hour * 60 + previous.minute
    current_minutes = current.hour * 60 + current.minute
    for window in windows:
        try:
            start_text, end_text = window.split("-", 1)
            start_hour, start_minute = (
                int(value) for value in start_text.split(":", 1)
            )
            end_hour, end_minute = (int(value) for value in end_text.split(":", 1))
        except (ValueError, TypeError):
            raise ValueError(f"invalid expected gap window: {window!r}") from None
        start = start_hour * 60 + start_minute
        end = end_hour * 60 + end_minute
        if start <= end:
            matches = start <= previous_minutes and current_minutes <= end
        else:
            matches = previous_minutes >= start and current_minutes <= end
        if matches:
            return True
    return False


def write_quality_report(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return target


def load_ohlcv_json(path: str | Path, min_bars: int = 60) -> pd.DataFrame:
    """Read and validate a JSON OHLCV export.

    The explicit JSON path is intentionally retained as an offline/fallback
    source; it is never selected implicitly when database mode is requested.
    """
    source = Path(path)
    if not source.is_file():
        raise DataSourceError(f"JSON data file not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataSourceError(f"cannot read JSON OHLCV: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("data", payload.get("candles", payload))
    try:
        return validate_ohlcv(payload, min_bars=min_bars)
    except (TypeError, ValueError) as exc:
        raise DataSourceError(str(exc)) from exc


def load_ohlcv_database(
    database_url: str | None = None,
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    min_bars: int = 60,
    connection_factory: Callable[[str], Any] | None = None,
    candle_source: str | None = None,
) -> pd.DataFrame:
    """Load validated candles from ``market_data`` using parameterized SQL.

    PostgreSQL is supported through psycopg2 (or a caller supplied factory).
    SQLite URLs are supported for deterministic tests and local development.
    The query deliberately selects only the OHLCV contract columns.
    """
    url = database_url or os.getenv("DATABASE_URL", "")
    if not url:
        raise DataSourceError("DATABASE_URL is required for database source")
    if not symbol or not timeframe:
        raise DataSourceError("symbol and timeframe are required")
    connection = None
    try:
        if connection_factory:
            connection = connection_factory(url)
        elif url.startswith("sqlite:///"):
            import sqlite3

            connection = sqlite3.connect(url[len("sqlite:///") :])
        else:
            try:
                import psycopg2
            except ImportError as exc:
                raise DataSourceError(
                    "psycopg2 is required for PostgreSQL DATABASE_URL"
                ) from exc
            connection = psycopg2.connect(url)
        # ``ohlcv_data`` is the canonical table in DATA_CONTRACT.md.  Older
        # local databases used ``market_data`` with a ``time`` column; keep
        # that schema as a read-only compatibility fallback.  Trying the
        # canonical query first prevents the trainer from silently ignoring
        # data written by the MT5 ingestion pipeline.
        source_clause = ""
        source_params: tuple[Any, ...] = ()
        if candle_source is not None:
            if not candle_source.strip():
                raise DataSourceError("candle_source must not be blank")
            source_clause = " AND source = %s"
            source_params = (candle_source.strip(),)
        queries = [
            "SELECT timestamp AS time, open, high, low, close, tick_volume "
            "FROM ohlcv_data WHERE symbol = %s AND timeframe = %s"
            + source_clause
            + " ORDER BY timestamp ASC"
        ]
        if candle_source is None:
            queries.append(
                "SELECT time, open, high, low, close, volume AS tick_volume "
                "FROM market_data WHERE symbol = %s AND timeframe = %s "
                "ORDER BY time ASC"
            )
        params: tuple[Any, ...] = (symbol, timeframe) + source_params
        last_error: Exception | None = None
        frame = None
        for query in queries:
            try:
                if url.startswith("sqlite:///"):
                    query = query.replace("%s", "?")
                query_params = (
                    params
                    if query.startswith("SELECT timestamp")
                    else ((symbol, timeframe))
                )
                frame = pd.read_sql_query(query, connection, params=query_params)
                break
            except Exception as exc:
                last_error = exc
        if frame is None:
            raise DataSourceError(
                f"database OHLCV query failed for canonical and legacy tables: "
                f"{last_error}"
            )
        if "time" in frame:
            frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
            if frame["time"].isna().any():
                raise DataSourceError("database contains invalid candle timestamps")
            frame = frame.drop_duplicates("time").sort_values("time")
        try:
            return validate_ohlcv(frame, min_bars=min_bars)
        except ValueError as exc:
            raise DataSourceError(str(exc)) from exc
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError(f"database OHLCV query failed: {exc}") from exc
    finally:
        if connection is not None and hasattr(connection, "close"):
            connection.close()


def load_training_data(
    source: str,
    *,
    json_path: str | Path | None = None,
    database_url: str | None = None,
    symbol: str = "XAUUSD",
    timeframe: str = "M5",
    min_samples: int = 1000,
    candle_source: str | None = None,
) -> pd.DataFrame:
    """Select exactly one explicit source and enforce a training floor."""
    if source == "json":
        if json_path is None:
            raise DataSourceError("json_path is required for source='json'")
        frame = load_ohlcv_json(json_path, min_bars=min_samples)
    elif source == "database":
        frame = load_ohlcv_database(
            database_url,
            symbol,
            timeframe,
            min_bars=min_samples,
            candle_source=candle_source,
        )
    else:
        raise DataSourceError("source must be 'database' or 'json'")
    if len(frame) < min_samples:
        raise DataSourceError(
            f"insufficient training samples: got {len(frame)}, need {min_samples}"
        )
    return frame


def make_classification_dataset(
    ohlcv: object, config: MLConfig, feature_names: Sequence[str] | None = None
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Create leakage-safe features and BUY/SELL/HOLD labels."""
    frame = validate_ohlcv(ohlcv, config.min_samples)
    names = list(
        feature_names
        or (
            "return_1",
            "return_5",
            "return_10",
            "volatility_10",
            "volatility_20",
            "high_low_range",
            "close_position",
            "sma_20",
            "sma_50",
            "rsi",
            "macd",
            "macd_signal",
            "atr",
            "atr_normalized",
        )
    )
    features = build_training_features(frame, names, config.min_bars)
    future_return = frame["close"].shift(-config.horizon) / frame["close"] - 1.0
    labels = pd.Series("HOLD", index=frame.index)
    labels[future_return > config.return_threshold] = "BUY"
    labels[future_return < -config.return_threshold] = "SELL"
    data = features.join(labels.rename("target")).dropna()
    return data[names], data["target"], names


def train_classifier(
    features: pd.DataFrame,
    labels: Sequence[Any],
    *,
    model_type: str = "xgboost",
    random_state: int = 42,
    **params: Any,
) -> Any:
    """Train a probability-producing classifier used by inference artifacts."""
    if len(features) != len(labels) or len(features) < 3:
        raise ValueError("features and labels must contain at least three aligned rows")
    if features.isna().any().any():
        raise ValueError("training features contain missing values")
    labels_array = np.asarray(labels)
    if len(np.unique(labels_array)) < 2:
        raise ValueError("training labels must contain at least two classes")
    kind = model_type.lower()
    if kind in {"random_forest", "randomforest", "rf"}:
        from sklearn.ensemble import RandomForestClassifier

        model = RandomForestClassifier(random_state=random_state, **params)
    elif kind in {"lightgbm", "lgbm"}:
        try:
            from lightgbm import LGBMClassifier
        except ImportError as exc:
            raise DataSourceError("lightgbm is not installed") from exc
        model = LGBMClassifier(random_state=random_state, verbosity=-1, **params)
    elif kind in {"xgboost", "xgb"}:
        try:
            from xgboost import XGBClassifier

            model = XGBClassifier(
                random_state=random_state,
                eval_metric="mlogloss",
                **params,
            )
        except ImportError:
            from sklearn.ensemble import HistGradientBoostingClassifier

            model = HistGradientBoostingClassifier(random_state=random_state, **params)
    else:
        raise ValueError(f"unsupported model_type: {model_type}")
    model.fit(features, labels_array)
    return model


def time_split(
    X: Any, y: Any, test_size: float = 0.15, validation_size: float = 0.15
) -> tuple:
    """Split ordered samples into train/validation/test without shuffling."""
    n = len(X)
    a = int(n * (1 - test_size - validation_size))
    b = int(n * (1 - test_size))
    if a < 1 or b <= a or b >= n:
        raise ValueError("not enough samples for time split")
    return X[:a], X[a:b], X[b:], y[:a], y[a:b], y[b:]


def walk_forward_splits(X: Any, n_splits: int = 5):
    """Yield expanding-window train/test indices."""
    if len(X) <= n_splits:
        raise ValueError("not enough samples for walk-forward validation")
    yield from TimeSeriesSplit(n_splits=n_splits).split(X)


def walk_forward_validate(
    features: pd.DataFrame,
    labels: Sequence[Any],
    *,
    model_factory: Callable[[], Any],
    n_splits: int = 5,
) -> list[dict[str, Any]]:
    """Fit a fresh model per chronological fold and return out-of-sample metrics."""
    if len(features) != len(labels):
        raise ValueError("features and labels must be aligned")
    if features.isna().any().any():
        raise ValueError("validation features contain missing values")
    results: list[dict[str, Any]] = []
    labels_array = np.asarray(labels)
    for fold, (train_idx, test_idx) in enumerate(
        walk_forward_splits(features, n_splits), 1
    ):
        model = model_factory()
        model.fit(features.iloc[train_idx], labels_array[train_idx])
        predictions = np.asarray(model.predict(features.iloc[test_idx]))
        if len(predictions) != len(test_idx):
            raise ValueError("model returned an invalid number of predictions")
        results.append(
            {
                "fold": fold,
                "train_start": int(train_idx[0]),
                "train_end": int(train_idx[-1]),
                "test_start": int(test_idx[0]),
                "test_end": int(test_idx[-1]),
                "accuracy": float(np.mean(predictions == labels_array[test_idx])),
                "n_train": int(len(train_idx)),
                "n_test": int(len(test_idx)),
            }
        )
    return results


def save_artifact(
    model: Any, feature_names: Sequence[str], path: str | Path, **metadata: Any
) -> Path:
    """Persist the exact model contract consumed by MLInferenceService."""
    if not feature_names or len(set(feature_names)) != len(feature_names):
        raise ValueError("feature_names must be non-empty and unique")
    if not callable(getattr(model, "predict_proba", None)):
        raise ValueError("model must implement predict_proba")
    names = [str(name) for name in feature_names]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = dict(metadata)
    metadata.setdefault("feature_schema_hash", feature_schema_hash(names))
    metadata.setdefault("metadata_version", "1")
    with target.open("wb") as fh:
        pickle.dump(
            {"model": model, "feature_names": names, **metadata},
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    return target

"""Strict feature construction used by both offline and live code."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

REQUIRED_OHLCV = ("open", "high", "low", "close")


def feature_schema_hash(
    features: pd.DataFrame | Mapping[str, Any] | Sequence[str],
) -> str:
    """Return a deterministic hash of feature name, dtype and column order.

    The representation is deliberately small and JSON based so it is stable
    across processes and contains no values (or credentials) from the data.
    """
    if isinstance(features, pd.DataFrame):
        schema = [
            (str(name), str(dtype))
            for name, dtype in zip(features.columns, features.dtypes)
        ]
    elif isinstance(features, Mapping):
        schema = [(str(name), str(value)) for name, value in features.items()]
    else:
        schema = [(str(name), "") for name in features]
    payload = json.dumps(schema, ensure_ascii=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def dataset_version(features: pd.DataFrame, labels: Sequence[Any] | None = None) -> str:
    """Create a content-addressed, deterministic version for a dataset."""
    frame = features.copy()
    frame = frame.reindex(sorted(frame.columns), axis=1)
    payload = {
        "schema": [(str(c), str(frame[c].dtype)) for c in frame.columns],
        "values": frame.to_json(orient="split", date_format="iso", double_precision=15),
        "labels": None if labels is None else [str(x) for x in labels],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_checksum(path: str | "Path") -> str:
    """Checksum the exact serialized artifact bytes on disk."""
    return sha256_bytes(Path(path).read_bytes())


def validate_ohlcv(data: object, min_bars: int = 60) -> pd.DataFrame:
    """Validate and normalize real OHLCV data without filling missing values."""
    if isinstance(data, pd.DataFrame):
        frame = data.copy()
    elif isinstance(data, (list, tuple)):
        frame = pd.DataFrame(data)
    elif isinstance(data, dict):
        frame = pd.DataFrame(data)
    else:
        raise ValueError("OHLCV must be a pandas DataFrame or row records")
    frame.columns = [str(c).lower() for c in frame.columns]
    missing = [c for c in REQUIRED_OHLCV if c not in frame.columns]
    if missing:
        raise ValueError(f"OHLCV schema missing required columns: {missing}")
    if len(frame) < min_bars:
        raise ValueError(f"insufficient OHLCV bars: got {len(frame)}, need {min_bars}")
    for col in REQUIRED_OHLCV:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if frame[list(REQUIRED_OHLCV)].isna().any().any():
        raise ValueError("OHLCV contains missing or non-numeric prices")
    if (frame["high"] < frame[["open", "close"]].max(axis=1)).any() or (
        frame["low"] > frame[["open", "close"]].min(axis=1)
    ).any():
        raise ValueError("OHLCV contains impossible candles")
    if (frame["close"] <= 0).any():
        raise ValueError("close prices must be positive")
    return frame.reset_index(drop=True)


def build_training_features(
    data: object, feature_names: Sequence[str], min_bars: int = 60
) -> pd.DataFrame:
    """Build the exact indicator names used by ``scripts/train_model.py``."""
    df = validate_ohlcv(data, min_bars)
    close, high, low = df["close"], df["high"], df["low"]
    r1 = close.pct_change()
    values = {
        "open": df["open"],
        "high": high,
        "low": low,
        "close": close,
        "tick_volume": pd.to_numeric(df["tick_volume"], errors="coerce")
        if "tick_volume" in df
        else None,
        "return_1": r1,
        "return_5": close.pct_change(5),
        "return_10": close.pct_change(10),
        "volatility_10": r1.rolling(10).std(),
        "volatility_20": r1.rolling(20).std(),
        "high_low_range": (high - low) / close,
        "close_position": (close - low) / (high - low + 1e-10),
        "sma_20": close.rolling(20).mean(),
        "sma_50": close.rolling(50).mean(),
    }
    values.update(
        {
            "price_vs_sma20": (close - values["sma_20"]) / values["sma_20"],
            "price_vs_sma50": (close - values["sma_50"]) / values["sma_50"],
            "sma20_vs_sma50": (values["sma_20"] - values["sma_50"]) / values["sma_50"],
        }
    )
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / (loss + 1e-10))
    values.update(
        {
            "rsi": rsi,
            "rsi_ma5": rsi.rolling(5).mean(),
            "rsi_deviation": rsi - rsi.rolling(5).mean(),
        }
    )
    e1, e2 = (
        close.ewm(span=12, adjust=False).mean(),
        close.ewm(span=26, adjust=False).mean(),
    )
    macd = e1 - e2
    sig = macd.ewm(span=9, adjust=False).mean()
    values.update({"macd": macd, "macd_signal": sig, "macd_hist": macd - sig})
    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    values.update(
        {
            "bb_middle": mid,
            "bb_std": std,
            "bb_upper": upper,
            "bb_lower": lower,
            "bb_width": (upper - lower) / mid,
            "bb_position": (close - lower) / (upper - lower + 1e-10),
        }
    )
    tr = pd.concat(
        [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr = tr.rolling(14).mean()
    values.update({"atr": atr, "atr_normalized": atr / close})
    if "tick_volume" in df:
        vol = pd.to_numeric(df["tick_volume"], errors="coerce")
        ma = vol.rolling(10).mean()
        values.update(
            {
                "volume_ma10": ma,
                "volume_ratio": vol / (ma + 1e-10),
                "volume_spike": (vol > ma * 2).astype(int),
            }
        )
    # Remaining names emitted by the training script.
    plus = (
        high.diff()
        .where((high.diff() > low.diff()) & (high.diff() > 0), 0)
        .rolling(14)
        .mean()
        / (atr + 1e-10)
        * 100
    )
    minus = (
        low.diff()
        .where((low.diff() > high.diff()) & (low.diff() > 0), 0)
        .rolling(14)
        .mean()
        / (atr + 1e-10)
        * 100
    )
    dx = 100 * (plus - minus).abs() / (plus + minus + 1e-10)
    values.update(
        {
            "adx": dx.rolling(14).mean(),
            "plus_di": plus,
            "minus_di": minus,
            "di_diff": plus - minus,
        }
    )
    lowest, highest = low.rolling(14).min(), high.rolling(14).max()
    k = 100 * (close - lowest) / (highest - lowest + 1e-10)
    values.update({"stoch_k": k, "stoch_d": k.rolling(3).mean()})
    centered_high = high.rolling(5, center=True).max()
    centered_low = low.rolling(5, center=True).min()
    trailing_high = high.rolling(5).max()
    trailing_low = low.rolling(5).min()
    swing_high = centered_high.fillna(trailing_high)
    swing_low = centered_low.fillna(trailing_low)
    values.update(
        {
            "swing_high": swing_high.eq(high).astype(float),
            "swing_low": swing_low.eq(low).astype(float),
            "dist_from_swing_high": high.rolling(5).max() - close,
            "dist_from_swing_low": close - low.rolling(5).min(),
        }
    )
    body = (close - df["open"]).abs() / close
    values.update(
        {
            "body_size": body,
            "body_ratio": (close - df["open"]).abs() / (high - low + 1e-10),
            "upper_shadow": (high - pd.concat([df["open"], close], axis=1).max(axis=1))
            / close,
            "lower_shadow": (pd.concat([df["open"], close], axis=1).min(axis=1) - low)
            / close,
            "candle_direction": np.sign(close - df["open"]),
            "is_doji": ((close - df["open"]).abs() / (high - low + 1e-10) < 0.1).astype(
                int
            ),
            "prev_body": body.shift(1),
            "engulfing": (
                (body > body.shift(1) * 1.5)
                & (np.sign(close - df["open"]) != np.sign(close - df["open"]).shift(1))
            ).astype(int),
        }
    )
    result = pd.DataFrame(
        {name: values[name] for name in feature_names if name in values}
    )
    unknown = [name for name in feature_names if name not in result.columns]
    if unknown:
        raise ValueError(f"cannot reproduce artifact features: {unknown}")
    result = result.replace([np.inf, -np.inf], np.nan)
    if result.empty or result.iloc[-1].isna().any():
        raise ValueError("latest OHLCV row cannot produce a complete feature vector")
    return result

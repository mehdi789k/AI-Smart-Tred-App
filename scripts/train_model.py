#!/usr/bin/env python3
"""
Training Pipeline for ML Trading Models

This script implements a complete offline training pipeline for machine learning
models used in algorithmic trading. It includes:

1. Feature Engineering: Technical indicators, SMC levels, candle patterns
2. Smart Labeling: Triple Barrier Method for sophisticated labeling
3. Model Training: RandomForest, XGBoost with hyperparameter tuning
4. Cross-Validation: 5-fold CV to prevent overfitting
5. Model Evaluation: Comprehensive metrics and A/B testing
6. Model Persistence: Save best models for live trading

Usage:
    python scripts/train_model.py --symbol XAUUSD_l --timeframe M5 --years 2
    python scripts/train_model.py --config scripts/ml_training_config.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.python.logging_config import get_logger
from src.python.data.historical_loader import HistoricalDataLoader
from src.python.data.database import AsyncDatabase
from src.python.data.models import OHLCVBar
from src.python.data.config import DataConfig
from src.python.ml import (
    dataset_version, save_artifact, assess_data_quality, write_quality_report,
    sha256_file,
)
from sqlalchemy import select
from src.python.indicators.common import load_candles

logger = get_logger("ml.training")


@dataclass
class TrainingConfig:
    """Configuration for model training."""
    
    # Data parameters
    symbols: List[str] = field(default_factory=lambda: ["XAUUSD_l", "EURUSD", "GBPUSD"])
    timeframes: List[str] = field(default_factory=lambda: ["M15", "H1", "H4"])
    years_of_data: int = 2
    min_samples: int = 10000
    
    # Feature engineering
    use_rsi: bool = True
    use_macd: bool = True
    use_bollinger: bool = True
    use_atr: bool = True
    use_adx: bool = True
    use_stochastic: bool = True
    use_ichimoku: bool = False
    use_volume: bool = True
    use_smc_features: bool = True
    use_candle_patterns: bool = True
    
    # Triple Barrier Method parameters
    profit_target_multiplier: float = 2.0  # TP = multiplier * ATR
    stop_loss_multiplier: float = 1.0      # SL = multiplier * ATR
    time_barrier_bars: int = 20            # Max bars to wait
    
    # Model parameters
    model_type: str = "randomforest"  # 'randomforest', 'xgboost', 'ensemble'
    n_estimators_rf: int = 200
    max_depth_rf: int = 15
    n_estimators_xgb: int = 300
    max_depth_xgb: int = 8
    learning_rate_xgb: float = 0.05
    
    # Training parameters
    test_size: float = 0.15
    val_size: float = 0.15
    cv_folds: int = 5
    random_state: int = 42
    class_weight_strategy: str = "balanced"
    hold_warning_threshold: float = 0.70
    class_imbalance_warning_threshold: float = 0.10
    
    # Hyperparameter tuning
    use_hyperparameter_tuning: bool = False
    n_trials: int = 50
    
    # Output
    output_dir: str = "models"
    save_feature_importance: bool = True
    min_coverage_hours: float = 720.0
    gap_tolerance_minutes: int = 10
    max_unexpected_gaps: int = 0
    expected_gap_windows: list[str] = field(
        default_factory=lambda: ["23:55-01:05"]
    )
    quality_report_dir: str = "reports/data_quality"

    def __post_init__(self) -> None:
        if self.profit_target_multiplier <= 0 or self.stop_loss_multiplier <= 0:
            raise ValueError("Triple Barrier multipliers must be positive")
        if self.time_barrier_bars < 1:
            raise ValueError("time_barrier_bars must be positive")
        if self.class_weight_strategy not in {"balanced", "balanced_subsample", "none"}:
            raise ValueError(
                "class_weight_strategy must be 'balanced', "
                "'balanced_subsample', or 'none'"
            )
        if not 0 < self.hold_warning_threshold <= 1:
            raise ValueError("hold_warning_threshold must be between zero and one")
        if not 0 < self.class_imbalance_warning_threshold <= 1:
            raise ValueError(
                "class_imbalance_warning_threshold must be between zero and one"
            )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrainingConfig":
        """Create config from flat or grouped JSON configuration."""
        defaults = cls()
        values = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        triple_barrier = data.get("triple_barrier", {})
        if isinstance(triple_barrier, dict):
            for key in (
                "profit_target_multiplier",
                "stop_loss_multiplier",
                "time_barrier_bars",
            ):
                if key in triple_barrier:
                    values[key] = triple_barrier[key]
        models = data.get("models", {})
        if isinstance(models, dict):
            values["model_type"] = models.get(
                "model_type", values.get("model_type", defaults.model_type)
            )
            random_forest = models.get("random_forest", {})
            if isinstance(random_forest, dict):
                values["n_estimators_rf"] = random_forest.get(
                    "n_estimators", values.get("n_estimators_rf", defaults.n_estimators_rf)
                )
                values["max_depth_rf"] = random_forest.get(
                    "max_depth", values.get("max_depth_rf", defaults.max_depth_rf)
                )
            xgboost = models.get("xgboost", {})
            if isinstance(xgboost, dict):
                values["n_estimators_xgb"] = xgboost.get(
                    "n_estimators", values.get("n_estimators_xgb", defaults.n_estimators_xgb)
                )
                values["max_depth_xgb"] = xgboost.get(
                    "max_depth", values.get("max_depth_xgb", defaults.max_depth_xgb)
                )
                values["learning_rate_xgb"] = xgboost.get(
                    "learning_rate", values.get("learning_rate_xgb", defaults.learning_rate_xgb)
                )
        training = data.get("training", {})
        if isinstance(training, dict):
            values["test_size"] = training.get("test_size", values.get("test_size", defaults.test_size))
            values["val_size"] = training.get("val_size", values.get("val_size", defaults.val_size))
            values["cv_folds"] = training.get("cv_folds", values.get("cv_folds", defaults.cv_folds))
            values["random_state"] = training.get(
                "random_state", values.get("random_state", defaults.random_state)
            )
            values["use_hyperparameter_tuning"] = training.get(
                "use_hyperparameter_tuning",
                values.get("use_hyperparameter_tuning", defaults.use_hyperparameter_tuning),
            )
            values["n_trials"] = training.get("n_trials", values.get("n_trials", defaults.n_trials))
        feature_engineering = data.get("feature_engineering", {})
        if isinstance(feature_engineering, dict):
            values.update(
                {
                    key: feature_engineering[key]
                    for key in cls.__dataclass_fields__
                    if key.startswith("use_") and key in feature_engineering
                }
            )
        output = data.get("output", {})
        if isinstance(output, dict):
            values["output_dir"] = output.get("output_dir", values.get("output_dir", defaults.output_dir))
            values["save_feature_importance"] = output.get(
                "save_feature_importance",
                values.get("save_feature_importance", defaults.save_feature_importance),
            )
            values["quality_report_dir"] = output.get(
                "quality_report_dir", values.get("quality_report_dir", defaults.quality_report_dir)
            )
        quality = data.get("data_quality", {})
        if isinstance(quality, dict):
            values["min_coverage_hours"] = quality.get(
                "min_coverage_hours", values.get("min_coverage_hours", defaults.min_coverage_hours)
            )
            values["gap_tolerance_minutes"] = quality.get(
                "gap_tolerance_minutes", values.get("gap_tolerance_minutes", defaults.gap_tolerance_minutes)
            )
            values["max_unexpected_gaps"] = quality.get(
                "max_unexpected_gaps",
                values.get("max_unexpected_gaps", defaults.max_unexpected_gaps),
            )
            values["expected_gap_windows"] = quality.get(
                "expected_gap_windows",
                values.get("expected_gap_windows", defaults.expected_gap_windows),
            )
        return cls(**values)
    
    @classmethod
    def from_json(cls, json_path: str) -> "TrainingConfig":
        """Load config from JSON file."""
        with open(json_path, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def save(self, json_path: str) -> None:
        """Save config to JSON file."""
        with open(json_path, 'w') as f:
            json.dump(self.__dict__, f, indent=2)


class FeatureEngineer:
    """Generate features for ML models."""
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self.logger = get_logger("ml.features")
        self.feature_names: List[str] = []
    
    def calculate_all_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all configured features."""
        self.logger.info("Calculating features...")
        
        # Basic OHLCV features
        df = self._add_basic_features(df)
        
        # Technical indicators
        if self.config.use_rsi:
            df = self._add_rsi(df)
        
        if self.config.use_macd:
            df = self._add_macd(df)
        
        if self.config.use_bollinger:
            df = self._add_bollinger_bands(df)
        
        if self.config.use_atr:
            df = self._add_atr(df)
        
        if self.config.use_adx:
            df = self._add_adx(df)
        
        if self.config.use_stochastic:
            df = self._add_stochastic(df)
        
        if self.config.use_volume:
            df = self._add_volume_features(df)
        
        if self.config.use_smc_features:
            df = self._add_smc_features(df)
        
        if self.config.use_candle_patterns:
            df = self._add_candle_patterns(df)
        
        # Only feature columns determine training-row validity. Raw metadata
        # fields such as ``time_iso`` must not discard every numeric row.
        feature_columns = [name for name in self.feature_names if name in df.columns]
        df[feature_columns] = df[feature_columns].replace([np.inf, -np.inf], np.nan)
        before_drop = len(df)
        df = df.dropna(subset=feature_columns)
        if df.empty:
            raise ValueError(
                "Feature engineering produced no valid rows "
                f"(input_rows={before_drop}, features={len(feature_columns)})."
            )
        
        self.logger.info(f"Generated {len(self.feature_names)} features")
        return df
    
    def _add_basic_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add basic price-derived features."""
        df = df.copy()
        
        # Returns
        df['return_1'] = df['close'].pct_change(1)
        df['return_5'] = df['close'].pct_change(5)
        df['return_10'] = df['close'].pct_change(10)
        
        # Volatility
        df['volatility_10'] = df['return_1'].rolling(10).std()
        df['volatility_20'] = df['return_1'].rolling(20).std()
        
        # Price position
        df['high_low_range'] = (df['high'] - df['low']) / df['close']
        df['close_position'] = (df['close'] - df['low']) / (df['high'] - df['low'] + 1e-10)
        
        # Trend indicators
        df['sma_20'] = df['close'].rolling(20).mean()
        df['sma_50'] = df['close'].rolling(50).mean()
        df['price_vs_sma20'] = (df['close'] - df['sma_20']) / df['sma_20']
        df['price_vs_sma50'] = (df['close'] - df['sma_50']) / df['sma_50']
        df['sma20_vs_sma50'] = (df['sma_20'] - df['sma_50']) / df['sma_50']
        
        basic_features = [
            'return_1', 'return_5', 'return_10',
            'volatility_10', 'volatility_20',
            'high_low_range', 'close_position',
            'sma_20', 'sma_50', 'price_vs_sma20', 'price_vs_sma50', 'sma20_vs_sma50'
        ]
        self.feature_names.extend(basic_features)
        
        return df
    
    def _add_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Add RSI indicator."""
        df = df.copy()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # RSI derivatives
        df['rsi_ma5'] = df['rsi'].rolling(5).mean()
        df['rsi_deviation'] = df['rsi'] - df['rsi_ma5']
        
        self.feature_names.extend(['rsi', 'rsi_ma5', 'rsi_deviation'])
        return df
    
    def _add_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add MACD indicator."""
        df = df.copy()
        
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = exp1 - exp2
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        
        self.feature_names.extend(['macd', 'macd_signal', 'macd_hist'])
        return df
    
    def _add_bollinger_bands(self, df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """Add Bollinger Bands."""
        df = df.copy()
        
        df['bb_middle'] = df['close'].rolling(period).mean()
        df['bb_std'] = df['close'].rolling(period).std()
        df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * 2)
        df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * 2)
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'] + 1e-10)
        
        self.feature_names.extend(['bb_middle', 'bb_std', 'bb_upper', 'bb_lower', 'bb_width', 'bb_position'])
        return df
    
    def _add_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Add Average True Range."""
        df = df.copy()
        
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(period).mean()
        df['atr_normalized'] = df['atr'] / df['close']
        
        self.feature_names.extend(['atr', 'atr_normalized'])
        return df
    
    def _add_adx(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Add ADX (Average Directional Index)."""
        df = df.copy()
        
        # Calculate +DM and -DM
        high_diff = df['high'].diff()
        low_diff = df['low'].diff()
        
        plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
        
        # Calculate TR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = np.maximum(high_low, np.maximum(high_close, low_close))
        
        # Smooth
        atr = pd.Series(tr, index=df.index).rolling(period).mean()
        plus_di = (
            100
            * pd.Series(plus_dm, index=df.index).rolling(period).mean()
            / (atr + 1e-10)
        )
        minus_di = (
            100
            * pd.Series(minus_dm, index=df.index).rolling(period).mean()
            / (atr + 1e-10)
        )
        
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        df['adx'] = dx.rolling(period).mean()
        df['plus_di'] = plus_di
        df['minus_di'] = minus_di
        df['di_diff'] = plus_di - minus_di
        
        self.feature_names.extend(['adx', 'plus_di', 'minus_di', 'di_diff'])
        return df
    
    def _add_stochastic(self, df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
        """Add Stochastic Oscillator."""
        df = df.copy()
        
        lowest_low = df['low'].rolling(k_period).min()
        highest_high = df['high'].rolling(k_period).max()
        
        df['stoch_k'] = 100 * (df['close'] - lowest_low) / (highest_high - lowest_low + 1e-10)
        df['stoch_d'] = df['stoch_k'].rolling(d_period).mean()
        
        self.feature_names.extend(['stoch_k', 'stoch_d'])
        return df
    
    def _add_volume_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add volume-based features."""
        df = df.copy()
        
        if 'tick_volume' in df.columns:
            df['volume_ma10'] = df['tick_volume'].rolling(10).mean()
            df['volume_ratio'] = df['tick_volume'] / (df['volume_ma10'] + 1e-10)
            df['volume_spike'] = (df['tick_volume'] > df['volume_ma10'] * 2).astype(int)
            
            self.feature_names.extend(['volume_ma10', 'volume_ratio', 'volume_spike'])
        
        return df
    
    def _add_smc_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add Smart Money Concepts features (simplified)."""
        df = df.copy()
        
        # Identify swing highs and lows
        window = 5
        df['swing_high'] = df['high'].rolling(window, center=True).apply(
            lambda x: 1 if x.iloc[window//2] == x.max() else 0
        )
        df['swing_low'] = df['low'].rolling(window, center=True).apply(
            lambda x: 1 if x.iloc[window//2] == x.min() else 0
        )
        
        # Distance from recent swing high/low
        df['dist_from_swing_high'] = df['high'].rolling(window).max() - df['close']
        df['dist_from_swing_low'] = df['close'] - df['low'].rolling(window).min()
        
        self.feature_names.extend(['swing_high', 'swing_low', 'dist_from_swing_high', 'dist_from_swing_low'])
        return df
    
    def _add_candle_patterns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add candlestick pattern features."""
        df = df.copy()
        
        # Body size
        df['body_size'] = np.abs(df['close'] - df['open']) / df['close']
        df['body_ratio'] = df['body_size'] / (df['high'] - df['low'] + 1e-10)
        
        # Upper and lower shadows
        df['upper_shadow'] = (df['high'] - np.maximum(df['open'], df['close'])) / df['close']
        df['lower_shadow'] = (np.minimum(df['open'], df['close']) - df['low']) / df['close']
        
        # Candle direction
        df['candle_direction'] = np.sign(df['close'] - df['open'])
        
        # Doji detection (small body)
        df['is_doji'] = (df['body_ratio'] < 0.1).astype(int)
        
        # Engulfing pattern (simplified)
        df['prev_body'] = df['body_size'].shift(1)
        df['engulfing'] = ((df['body_size'] > df['prev_body'] * 1.5) & 
                          (df['candle_direction'] != df['candle_direction'].shift(1))).astype(int)
        
        self.feature_names.extend([
            'body_size', 'body_ratio', 'upper_shadow', 'lower_shadow',
            'candle_direction', 'is_doji', 'engulfing', 'prev_body'
        ])
        return df


class TripleBarrierLabeler:
    """Implement Triple Barrier Method for labeling."""
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self.logger = get_logger("ml.labeling")
    
    def label(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply Triple Barrier Method to generate labels.
        
        Barriers:
        1. Upper barrier: profit_target = ATR * multiplier
        2. Lower barrier: stop_loss = ATR * multiplier
        3. Time barrier: max holding period
        
        Labels:
        - 1 (BUY): Price hits upper barrier first
        - -1 (SELL): Price hits lower barrier first
        - 0 (HOLD): Time barrier hit or no clear signal
        """
        self.logger.info("Applying Triple Barrier Method...")
        required = {"close", "high", "low", "atr"}
        missing = required.difference(df.columns)
        if missing:
            raise ValueError(f"Triple Barrier requires columns: {sorted(missing)}")
        if df.empty:
            raise ValueError("Triple Barrier requires at least one candle")

        df = df.copy()
        labels = []
        reasons = []
        
        profit_target_mult = self.config.profit_target_multiplier
        stop_loss_mult = self.config.stop_loss_multiplier
        time_barrier = self.config.time_barrier_bars
        
        for i in range(len(df)):
            if i + time_barrier >= len(df):
                labels.append(0)
                reasons.append('time_cutoff')
                continue
            
            entry_price = df.iloc[i]['close']
            atr = df.iloc[i]['atr']
            
            if pd.isna(atr) or atr == 0:
                labels.append(0)
                reasons.append('invalid_atr')
                continue
            
            upper_barrier = entry_price * (1 + profit_target_mult * atr / entry_price)
            lower_barrier = entry_price * (1 - stop_loss_mult * atr / entry_price)
            
            # Scan forward to see which barrier is hit first
            label = 0
            reason = 'time_barrier'
            
            for j in range(i + 1, min(i + time_barrier + 1, len(df))):
                high = df.iloc[j]['high']
                low = df.iloc[j]['low']
                
                if high >= upper_barrier and low <= lower_barrier:
                    # OHLC cannot reveal intrabar order; choose the nearer barrier
                    # to keep the decision deterministic and conservative.
                    if abs(high - upper_barrier) < abs(low - lower_barrier):
                        label = 1
                        reason = 'profit_target'
                    else:
                        label = -1
                        reason = 'stop_loss'
                    break
                elif high >= upper_barrier:
                    label = 1
                    reason = 'profit_target'
                    break
                elif low <= lower_barrier:
                    label = -1
                    reason = 'stop_loss'
                    break
            
            labels.append(label)
            reasons.append(reason)
        
        df['label'] = labels
        df['label_reason'] = reasons
        
        # Convert to 0, 1, 2 for classification (SELL=0, HOLD=1, BUY=2)
        df['label_class'] = df['label'].map({-1: 0, 0: 1, 1: 2})
        
        if not labels:
            raise ValueError(
                "Triple Barrier labeling produced no rows. "
                "Increase the available candle history."
            )

        # Statistics
        total = len(labels)
        buy_count = sum(1 for l in labels if l == 1)
        sell_count = sum(1 for l in labels if l == -1)
        hold_count = sum(1 for l in labels if l == 0)
        
        self.logger.info(
            f"Label distribution: BUY={buy_count} ({buy_count/total*100:.1f}%), "
            f"SELL={sell_count} ({sell_count/total*100:.1f}%), "
            f"HOLD={hold_count} ({hold_count/total*100:.1f}%)"
        )
        
        return df


def analyze_label_distribution(
    df: pd.DataFrame,
    hold_warning_threshold: float = 0.70,
    class_imbalance_warning_threshold: float = 0.10,
) -> Dict[str, Any]:
    """Create auditable label diagnostics and flag under-represented classes."""
    required = {"label", "label_reason"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"label diagnostics require columns: {sorted(missing)}")
    if not 0 < hold_warning_threshold <= 1:
        raise ValueError("hold_warning_threshold must be between zero and one")
    if not 0 < class_imbalance_warning_threshold <= 1:
        raise ValueError(
            "class_imbalance_warning_threshold must be between zero and one"
        )

    labels = df["label"].dropna()
    counts = labels.value_counts().reindex([-1, 0, 1], fill_value=0).astype(int)
    total = int(counts.sum())
    if total == 0:
        raise ValueError("cannot analyze an empty label set")

    timestamps = df.index
    if "datetime" in df.columns:
        timestamps = pd.to_datetime(df["datetime"], errors="coerce", utc=True)
    else:
        timestamps = pd.to_datetime(timestamps, errors="coerce", utc=True)
    if pd.Series(timestamps).isna().any():
        raise ValueError("label diagnostics require valid timestamps")

    monthly: Dict[str, Dict[str, Any]] = {}
    timestamp_series = pd.Series(timestamps, index=df.index)
    if getattr(timestamp_series.dt, "tz", None) is not None:
        timestamp_series = timestamp_series.dt.tz_localize(None)
    month_keys = timestamp_series.dt.to_period("M").astype(str)
    for month, group in df.assign(_month=month_keys).groupby("_month", sort=True):
        month_counts = (
            group["label"].value_counts().reindex([-1, 0, 1], fill_value=0).astype(int)
        )
        month_total = int(month_counts.sum())
        monthly[str(month)] = {
            "samples": month_total,
            "SELL": int(month_counts[-1]),
            "HOLD": int(month_counts[0]),
            "BUY": int(month_counts[1]),
            "HOLD_pct": float(month_counts[0] / month_total),
        }

    hold_pct = float(counts[0] / total)
    percentages = {
        "SELL": float(counts[-1] / total),
        "HOLD": hold_pct,
        "BUY": float(counts[1] / total),
    }
    underrepresented = [
        label
        for label, percentage in percentages.items()
        if percentage < class_imbalance_warning_threshold
    ]
    reasons = (
        df.loc[df["label"] == 0, "label_reason"]
        .value_counts()
        .astype(int)
        .to_dict()
    )
    return {
        "samples": total,
        "counts": {"SELL": int(counts[-1]), "HOLD": int(counts[0]), "BUY": int(counts[1])},
        "percentages": {
            **percentages,
        },
        "class_imbalance": {
            "warning": bool(underrepresented),
            "threshold": class_imbalance_warning_threshold,
            "underrepresented_classes": underrepresented,
        },
        "hold": {
            "warning": hold_pct >= hold_warning_threshold,
            "reason_counts": {str(key): int(value) for key, value in reasons.items()},
        },
        "monthly": monthly,
    }


def calculate_class_weights(
    labels: np.ndarray, strategy: str = "balanced"
) -> Dict[int, float]:
    """Calculate deterministic class weights from training labels only."""
    classes = np.unique(labels)
    if strategy == "none":
        return {int(label): 1.0 for label in classes}
    if strategy not in {"balanced", "balanced_subsample"}:
        raise ValueError(f"unsupported class weight strategy: {strategy}")
    from sklearn.utils.class_weight import compute_class_weight

    values = compute_class_weight(class_weight="balanced", classes=classes, y=labels)
    return {int(label): float(weight) for label, weight in zip(classes, values)}


class ModelTrainer:
    """Train and evaluate ML models."""
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self.logger = get_logger("ml.trainer")
        self.models = {}
        self.metrics = {}
        self.class_weights: Dict[int, float] = {}
    
    def prepare_data(
        self, 
        df: pd.DataFrame, 
        feature_cols: List[str]
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Split data into train/validation/test sets."""
        
        features = df[feature_cols].values
        labels = df['label_class'].values
        
        n_samples = len(features)
        test_size = int(n_samples * self.config.test_size)
        val_size = int(n_samples * self.config.val_size)
        
        # Time-series split (no shuffling to preserve temporal order)
        train_end = n_samples - test_size - val_size
        val_end = n_samples - test_size
        
        X_train = features[:train_end]
        y_train = labels[:train_end]
        
        X_val = features[train_end:val_end]
        y_val = labels[train_end:val_end]
        
        X_test = features[val_end:]
        y_test = labels[val_end:]
        
        self.logger.info(
            f"Data split: Train={len(X_train)}, Val={len(X_val)}, Test={len(X_test)}"
        )
        
        return X_train, y_train, X_val, y_val, X_test, y_test
    
    def train_random_forest(
        self, 
        X_train: np.ndarray, 
        y_train: np.ndarray,
        feature_names: List[str]
    ):
        """Train Random Forest model."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score
        
        self.logger.info("Training Random Forest...")
        self.class_weights = calculate_class_weights(
            y_train, self.config.class_weight_strategy
        )
        class_weight = (
            None
            if self.config.class_weight_strategy == "none"
            else (
                "balanced_subsample"
                if self.config.class_weight_strategy == "balanced_subsample"
                else self.class_weights
            )
        )

        model = RandomForestClassifier(
            n_estimators=self.config.n_estimators_rf,
            max_depth=self.config.max_depth_rf,
            random_state=self.config.random_state,
            n_jobs=-1,
            class_weight=class_weight
        )
        
        # Cross-validation
        if self.config.cv_folds > 1:
            cv_scores = cross_val_score(
                model, X_train, y_train, 
                cv=self.config.cv_folds, 
                scoring='accuracy'
            )
            self.logger.info(f"RF CV Accuracy: {cv_scores.mean():.4f} (+/- {cv_scores.std()*2:.4f})")
        
        # Hyperparameter tuning
        if self.config.use_hyperparameter_tuning:
            model = self._tune_random_forest(X_train, y_train, class_weight)
        
        model.fit(X_train, y_train)
        
        self.models['random_forest'] = model
        self.logger.info("Random Forest training completed")
        
        return model
    
    def _tune_random_forest(self, X: np.ndarray, y: np.ndarray, class_weight):
        """Hyperparameter tuning for Random Forest."""
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.model_selection import GridSearchCV
            
            param_grid = {
                'n_estimators': [100, 200, 300],
                'max_depth': [10, 15, 20, None],
                'min_samples_split': [2, 5, 10],
                'min_samples_leaf': [1, 2, 4]
            }
            
            grid_search = GridSearchCV(
                RandomForestClassifier(
                    random_state=self.config.random_state,
                    n_jobs=-1,
                    class_weight=class_weight,
                ),
                param_grid,
                cv=3,
                scoring='accuracy',
                n_jobs=-1,
                verbose=1
            )
            
            grid_search.fit(X, y)
            self.logger.info(f"Best RF params: {grid_search.best_params_}")
            
            return grid_search.best_estimator_
            
        except Exception as e:
            self.logger.warning(f"Hyperparameter tuning failed: {e}")
            return RandomForestClassifier(
                n_estimators=self.config.n_estimators_rf,
                max_depth=self.config.max_depth_rf,
                random_state=self.config.random_state,
                n_jobs=-1,
                class_weight=class_weight,
            )
    
    def train_xgboost(
        self, 
        X_train: np.ndarray, 
        y_train: np.ndarray,
        feature_names: List[str]
    ):
        """Train XGBoost model."""
        try:
            import xgboost as xgb
        except ImportError:
            self.logger.warning("XGBoost not installed, skipping")
            return None
        
        self.logger.info("Training XGBoost...")
        if not self.class_weights:
            self.class_weights = calculate_class_weights(
                y_train, self.config.class_weight_strategy
            )
        
        model = xgb.XGBClassifier(
            n_estimators=self.config.n_estimators_xgb,
            max_depth=self.config.max_depth_xgb,
            learning_rate=self.config.learning_rate_xgb,
            objective='multi:softprob',
            num_class=3,
            random_state=self.config.random_state,
            n_jobs=-1,
            eval_metric='mlogloss'
        )
        
        # Cross-validation
        if self.config.cv_folds > 1:
            from sklearn.model_selection import cross_val_score
            cv_scores = cross_val_score(
                model, X_train, y_train, 
                cv=self.config.cv_folds, 
                scoring='accuracy'
            )
            self.logger.info(f"XGB CV Accuracy: {cv_scores.mean():.4f} (+/- {cv_scores.std()*2:.4f})")
        
        sample_weight = np.asarray(
            [self.class_weights[int(label)] for label in y_train], dtype=float
        )
        model.fit(X_train, y_train, sample_weight=sample_weight)
        
        self.models['xgboost'] = model
        self.logger.info("XGBoost training completed")
        
        return model
    
    def evaluate_model(
        self, 
        model, 
        X_test: np.ndarray, 
        y_test: np.ndarray,
        model_name: str
    ) -> Dict[str, Any]:
        """Evaluate model performance."""
        from sklearn.metrics import (
            accuracy_score, precision_score, recall_score, 
            f1_score, confusion_matrix, classification_report
        )
        
        y_pred = model.predict(X_test)
        if not isinstance(y_pred, np.ndarray) or y_pred.dtype.kind not in {"i", "u"}:
            predict_proba = getattr(model, "predict_proba", None)
            if predict_proba is None:
                raise TypeError(f"{model_name} did not return numeric class labels")
            y_pred = np.argmax(np.asarray(predict_proba(X_test)), axis=1)
        y_pred = np.asarray(y_pred, dtype=int)
        
        metrics = {
            'accuracy': accuracy_score(y_test, y_pred),
            'precision_macro': precision_score(y_test, y_pred, average='macro', zero_division=0),
            'recall_macro': recall_score(y_test, y_pred, average='macro', zero_division=0),
            'f1_macro': f1_score(y_test, y_pred, average='macro', zero_division=0),
        }
        
        self.logger.info(f"\n{model_name} Performance:")
        self.logger.info(f"Accuracy: {metrics['accuracy']:.4f}")
        self.logger.info(f"Precision (macro): {metrics['precision_macro']:.4f}")
        self.logger.info(f"Recall (macro): {metrics['recall_macro']:.4f}")
        self.logger.info(f"F1 Score (macro): {metrics['f1_macro']:.4f}")
        
        # Confusion matrix
        labels = [0, 1, 2]
        cm = confusion_matrix(y_test, y_pred, labels=labels)
        self.logger.info(f"Confusion Matrix:\n{cm}")
        metrics["confusion_matrix"] = cm.tolist()
        metrics["confusion_matrix_labels"] = ["SELL", "HOLD", "BUY"]
        metrics["classification_report"] = classification_report(
            y_test,
            y_pred,
            labels=labels,
            target_names=metrics["confusion_matrix_labels"],
            output_dict=True,
            zero_division=0,
        )
        
        self.metrics[model_name] = metrics
        
        return metrics
    
    def save_model(
        self,
        model,
        model_name: str,
        output_dir: str,
        feature_names: List[str],
        symbol: str = "",
        timeframe: str = "",
        dataset_version_value: str = "",
        data_manifest: Dict[str, Any] | None = None,
    ):
        """Save a governed artifact consumed by MLInferenceService."""
        
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{model_name}_{timestamp}.pkl"
        filepath = output_path / filename
        
        model_data = {
            'config': self.config.__dict__,
            'metrics': self.metrics.get(model_name, {}),
            'timestamp': timestamp,
            'schema_version': '1.0',
            'symbol': symbol,
            'timeframe': timeframe,
        }
        # Keep the class contract available for composite estimators such as
        # the soft-voting ensemble.  MLInferenceService uses this metadata
        # when the wrapper itself does not expose ``classes_``.
        classes = getattr(model, "classes_", None)
        if classes is not None:
            model_data["classes_"] = list(np.asarray(classes))
        
        if dataset_version_value:
            model_data["dataset_version"] = dataset_version_value
        if data_manifest is not None:
            model_data["data_manifest"] = data_manifest
        save_artifact(model, feature_names, filepath, **model_data)
        
        self.logger.info(f"Model saved to {filepath}")
        
        # Also save as latest
        latest_path = output_path / f"{model_name}_latest.pkl"
        save_artifact(model, feature_names, latest_path, **model_data)
        
        # Save feature importance
        if self.config.save_feature_importance and hasattr(model, 'feature_importances_'):
            importance_df = pd.DataFrame({
                'feature': feature_names,
                'importance': model.feature_importances_
            }).sort_values('importance', ascending=False)
            
            importance_path = output_path / f"{model_name}_feature_importance_{timestamp}.csv"
            importance_df.to_csv(importance_path, index=False)
            self.logger.info(f"Feature importance saved to {importance_path}")
        
        return filepath


def load_market_data(
    symbol: str, 
    timeframe: str, 
    years: int,
    data_dir: Path
) -> pd.DataFrame:
    """Load historical market data."""
    timeframe = {"WN": "MN1"}.get(timeframe.upper(), timeframe.upper())
    logger.info(f"Loading data for {symbol} {timeframe} ({years} years)...")
    
    # Prefer the rolling canonical file. Legacy timestamped snapshots may be
    # stale or partial, so select the file with the largest valid candle set.
    canonical_file = data_dir / f"{symbol}_{timeframe}.json"
    files = [canonical_file] if canonical_file.is_file() else []
    files.extend(
        file
        for file in data_dir.glob(f"{symbol}_{timeframe}_*.json")
        if file != canonical_file
    )
    # WN was used by the first collector release for monthly candles. Keep
    # those snapshots readable while all newly collected data uses MN1.
    if timeframe == "MN1":
        legacy_canonical = data_dir / f"{symbol}_WN.json"
        if legacy_canonical.is_file():
            files.append(legacy_canonical)
        files.extend(
            file
            for file in data_dir.glob(f"{symbol}_WN_*.json")
            if file != legacy_canonical
        )
    
    if not files:
        raise FileNotFoundError(f"No data found for {symbol} {timeframe}")
    
    candidates: list[tuple[Path, list[dict]]] = []
    for file in files:
        loaded = load_candles(file)
        if loaded:
            candidates.append((file, loaded))
    if not candidates:
        raise ValueError(f"No candles loaded for {symbol} {timeframe}")

    latest_file, candles = max(
        candidates,
        key=lambda item: (len(item[1]), item[0] == canonical_file, item[0].stat().st_mtime),
    )
    logger.info(
        "Using data file: %s (%d candles)",
        latest_file,
        len(candles),
    )
    
    # Convert to DataFrame
    df = pd.DataFrame(candles)
    
    # Dashboard-generated snapshots may contain the native epoch ``time``
    # field without the derived ISO field used by the legacy collector.
    if "time_iso" in df.columns:
        df["datetime"] = pd.to_datetime(df["time_iso"], utc=True)
    elif "time" in df.columns:
        df["datetime"] = pd.to_datetime(df["time"], unit="s", utc=True)
    else:
        raise ValueError(
            f"Market data for {symbol} {timeframe} has no timestamp field "
            "('time_iso' or 'time')"
        )
    df = df.set_index('datetime')
    
    logger.info(f"Loaded {len(df)} candles from {df.index.min()} to {df.index.max()}")
    
    return df


async def _load_database_candles(
    database_url: str,
    symbol: str,
    timeframe: str,
    min_samples: int,
    candle_source: str | None = None,
) -> pd.DataFrame:
    """Read validated OHLCV rows from the production database."""

    database = AsyncDatabase(database_url)
    try:
        async with database.session_factory() as session:
            query = select(OHLCVBar).where(
                OHLCVBar.symbol == symbol, OHLCVBar.timeframe == timeframe
            )
            if candle_source is not None:
                if not candle_source.strip():
                    raise ValueError("--candle-source must not be blank")
                query = query.where(OHLCVBar.source == candle_source.strip())
            result = await session.execute(query.order_by(OHLCVBar.timestamp.asc()))
            rows = [bar.to_payload() for bar in result.scalars().all()]
    finally:
        await database.dispose()
    if len(rows) < min_samples:
        raise ValueError(
            f"Database contains {len(rows)} {symbol}/{timeframe} candles; "
            f"at least {min_samples} are required"
        )
    frame = pd.DataFrame(rows)
    frame["datetime"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.set_index("datetime")


def main():
    """Main training pipeline."""
    parser = argparse.ArgumentParser(description="Train ML models for trading")
    parser.add_argument("--symbol", type=str, default="XAUUSD", help="Symbol to train on")
    parser.add_argument("--timeframe", type=str, default="M5", help="Timeframe")
    parser.add_argument("--years", type=int, default=2, help="Years of historical data")
    parser.add_argument(
        "--model-type",
        type=str,
        default="ensemble",
        choices=["randomforest", "xgboost", "ensemble"],
        help="Model family to train",
    )
    parser.add_argument(
        "--stage-b",
        action="store_true",
        help="Run the reproducible baseline: database source, Random Forest, no tuning",
    )
    parser.add_argument(
        "--time-barrier-bars",
        type=int,
        default=None,
        help="Triple Barrier time horizon in candles",
    )
    parser.add_argument("--config", type=str, help="Path to JSON config file")
    parser.add_argument("--data-dir", type=str, default="market_data", help="Directory with market data")
    parser.add_argument(
        "--source", choices=["json", "database"], default="json",
        help="Training data source; database reads validated OHLCV from PostgreSQL",
    )
    parser.add_argument(
        "--candle-source",
        default=None,
        help="Optional exact OHLCV provenance filter, e.g. mt5_official",
    )
    parser.add_argument(
        "--database-url", type=str, default=os.getenv("DATABASE_URL"),
        help="SQLAlchemy database URL, or DATABASE_URL",
    )
    parser.add_argument("--output-dir", type=str, default="models", help="Directory to save models")
    parser.add_argument(
        "--min-samples",
        type=int,
        default=int(os.getenv("ML_MIN_SAMPLES", "10000")),
        help="Minimum number of candles required by the training gate",
    )
    parser.add_argument("--min-coverage-hours", type=float,
                        default=float(os.getenv("ML_MIN_COVERAGE_HOURS", "720")),
                        help="Minimum chronological coverage required by the training gate")
    parser.add_argument("--gap-tolerance-minutes", type=int,
                        default=int(os.getenv("ML_GAP_TOLERANCE_MINUTES", "10")),
                        help="Short gap tolerance; unsafe gaps always fail closed")
    parser.add_argument("--max-unexpected-gaps", type=int,
                        default=int(os.getenv("ML_MAX_UNEXPECTED_GAPS", "0")),
                        help="Maximum unexpected gaps permitted by the training gate")
    parser.add_argument(
        "--expected-gap-window",
        action="append",
        default=None,
        help="Expected UTC session break in HH:MM-HH:MM format; repeatable",
    )
    parser.add_argument("--quality-report-dir", type=str,
                        default=os.getenv("ML_QUALITY_REPORT_DIR", "reports/data_quality"))
    parser.add_argument(
        "--class-imbalance-warning-threshold",
        type=float,
        default=float(os.getenv("ML_CLASS_IMBALANCE_WARNING_THRESHOLD", "0.10")),
        help="Warn when any label class share is below this fraction",
    )
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    
    args = parser.parse_args()

    if args.stage_b:
        args.source = "database"
        args.model_type = "randomforest"
    
    # Set logging level
    logging.getLogger("ml").setLevel(getattr(logging, args.log_level))
    
    # Load configuration
    if args.config:
        config = TrainingConfig.from_json(args.config)
    else:
        config = TrainingConfig()
        config.symbols = [args.symbol]
        config.timeframes = [args.timeframe]
        config.years_of_data = args.years
        config.output_dir = args.output_dir
        config.model_type = args.model_type
        config.min_samples = args.min_samples
        config.min_coverage_hours = args.min_coverage_hours
        config.gap_tolerance_minutes = args.gap_tolerance_minutes
        config.max_unexpected_gaps = args.max_unexpected_gaps
        if args.expected_gap_window is not None:
            config.expected_gap_windows = args.expected_gap_window
        config.quality_report_dir = args.quality_report_dir
        config.class_imbalance_warning_threshold = (
            args.class_imbalance_warning_threshold
        )
        if args.stage_b:
            config.min_samples = max(config.min_samples, 30000)
            config.use_hyperparameter_tuning = False
            config.cv_folds = 0
    if args.stage_b:
        config.model_type = "randomforest"
        config.min_samples = max(config.min_samples, 30000)
        config.use_hyperparameter_tuning = False
        config.cv_folds = 0
    if args.time_barrier_bars is not None:
        if args.time_barrier_bars < 1:
            raise ValueError("--time-barrier-bars must be positive")
        config.time_barrier_bars = args.time_barrier_bars
    if (
        config.min_samples < 1
        or not 0 < config.class_imbalance_warning_threshold <= 1
        or config.min_coverage_hours < 0
        or config.gap_tolerance_minutes < 0
        or config.max_unexpected_gaps < 0
    ):
        raise ValueError("invalid training gate thresholds")
    
    logger.info("=" * 60)
    logger.info("ML Model Training Pipeline")
    logger.info("=" * 60)
    logger.info(f"Configuration: {json.dumps(config.__dict__, indent=2)}")
    
    data_dir = Path(args.data_dir)
    trained_count = 0
    
    for symbol in config.symbols:
        for timeframe in config.timeframes:
            logger.info(f"\n{'='*60}")
            logger.info(f"Training for {symbol} {timeframe}")
            logger.info(f"{'='*60}")
            
            try:
                # Step 1: Load data
                if args.source == "database":
                    database_url = args.database_url or DataConfig.from_env().database_url
                    if not database_url:
                        raise ValueError("--database-url or DATABASE_URL is required for database source")
                    df = asyncio.run(
                        _load_database_candles(
                            database_url,
                            symbol,
                            timeframe,
                            config.min_samples,
                            args.candle_source,
                        )
                    )
                else:
                    df = load_market_data(symbol, timeframe, config.years_of_data, data_dir)
                # Training is fail-closed: do not create features or artifacts
                # until the complete chronological OHLCV contract is proven.
                report_path = Path(config.quality_report_dir) / f"{symbol}_{timeframe}.json"
                source_values = (
                    sorted(
                        {
                            str(value)
                            for value in df.get("source", pd.Series(dtype=object)).dropna()
                            if str(value).strip()
                        }
                    )
                    if args.source == "database"
                    else ["json"]
                )
                metadata_values = (
                    df.get("ingestion_metadata", pd.Series(dtype=object)).tolist()
                    if args.source == "database"
                    else []
                )
                metadata_fields = sorted(
                    {
                        str(key)
                        for value in metadata_values
                        if isinstance(value, dict)
                        for key in value
                    }
                )
                metadata_present = sum(
                    isinstance(value, dict) and bool(value) for value in metadata_values
                )
                ingestion_metadata = {
                    "data_dir": str(data_dir) if args.source == "json" else None,
                    "database_configured": bool(args.database_url)
                    if args.source == "database"
                    else None,
                    "sources": source_values,
                    "metadata_fields": metadata_fields,
                    "metadata_rows": len(metadata_values),
                    "metadata_present_rows": metadata_present,
                }
                try:
                    quality_report = assess_data_quality(
                        df, symbol=symbol, timeframe=timeframe,
                        min_bars=config.min_samples,
                        min_coverage_hours=config.min_coverage_hours,
                        gap_tolerance_minutes=config.gap_tolerance_minutes,
                        max_unexpected_gaps=config.max_unexpected_gaps,
                        expected_gap_windows=config.expected_gap_windows,
                        source=args.source,
                        ingestion_metadata=ingestion_metadata,
                    )
                except Exception as quality_error:
                    # Keep an auditable rejection record, but never continue to
                    # feature engineering or artifact creation.
                    report = getattr(quality_error, "report", None) or {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "source": args.source,
                        "bars": int(len(df)),
                    }
                    report.update(
                        status="rejected",
                        error=str(quality_error),
                        ingestion_metadata=ingestion_metadata,
                    )
                    write_quality_report(report, report_path)
                    raise
                quality_report_path = write_quality_report(quality_report, report_path)
                logger.info("Data quality report written to %s", report_path)
                if len(df) < config.min_samples:
                    raise ValueError(
                        f"training gate failed: insufficient data ({len(df)} samples)"
                    )
                
                # Step 2: Feature engineering
                feature_engineer = FeatureEngineer(config)
                df_features = feature_engineer.calculate_all_features(df)
                feature_names = feature_engineer.feature_names
                
                logger.info(f"Features generated: {len(feature_names)}")
                
                # Step 3: Labeling with Triple Barrier Method
                labeler = TripleBarrierLabeler(config)
                df_labeled = labeler.label(df_features)
                label_diagnostics = analyze_label_distribution(
                    df_labeled,
                    config.hold_warning_threshold,
                    config.class_imbalance_warning_threshold,
                )
                if label_diagnostics["hold"]["warning"]:
                    logger.warning(
                        "HOLD share %.1f%% exceeds configured threshold %.1f%%",
                        label_diagnostics["percentages"]["HOLD"] * 100,
                        config.hold_warning_threshold * 100,
                    )
                if label_diagnostics["class_imbalance"]["warning"]:
                    logger.warning(
                        "Class imbalance detected: %s below %.1f%%",
                        ", ".join(
                            label_diagnostics["class_imbalance"][
                                "underrepresented_classes"
                            ]
                        ),
                        config.class_imbalance_warning_threshold * 100,
                    )

                training_dataset_version = dataset_version(
                    pd.DataFrame(df_labeled[feature_names]),
                    df_labeled["label"].to_numpy(),
                )
                data_manifest = {
                    "manifest_version": "1.0",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "source": args.source,
                    "requested_years": config.years_of_data,
                    "required_min_samples": config.min_samples,
                    "required_min_coverage_hours": config.min_coverage_hours,
                    "coverage_hours": quality_report["coverage_hours"],
                    "coverage_ratio": (
                        quality_report["coverage_hours"]
                        / config.min_coverage_hours
                        if config.min_coverage_hours > 0
                        else None
                    ),
                    "quality_report_path": str(quality_report_path),
                    "quality_report_sha256": sha256_file(quality_report_path),
                    "quality": quality_report,
                    "label_diagnostics": label_diagnostics,
                    "dataset_version": training_dataset_version,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                }
                
                # Step 4: Prepare data
                trainer = ModelTrainer(config)
                X_train, y_train, X_val, y_val, X_test, y_test = trainer.prepare_data(
                    df_labeled, feature_names
                )
                
                # Step 5: Train models
                if config.model_type in ['randomforest', 'ensemble']:
                    rf_model = trainer.train_random_forest(X_train, y_train, feature_names)
                    trainer.evaluate_model(rf_model, X_test, y_test, 'random_forest')
                    trainer.save_model(
                        rf_model,
                        'random_forest',
                        config.output_dir,
                        feature_names,
                        symbol,
                        timeframe,
                        training_dataset_version,
                        data_manifest,
                    )
                
                if config.model_type in ['xgboost', 'ensemble']:
                    xgb_model = trainer.train_xgboost(X_train, y_train, feature_names)
                    if xgb_model:
                        trainer.evaluate_model(xgb_model, X_test, y_test, 'xgboost')
                        trainer.save_model(
                            xgb_model,
                            'xgboost',
                            config.output_dir,
                            feature_names,
                            symbol,
                            timeframe,
                            training_dataset_version,
                            data_manifest,
                        )

                # ``ensemble`` previously trained its component models but
                # never persisted the ensemble selected by the dashboard.
                # Persist the same soft-voting model using the already-fitted
                # components so no second training pass (or look-ahead) is
                # introduced.
                if config.model_type == 'ensemble':
                    from src.python.ml import ProbabilityEnsemble

                    components = [
                        model for model in (rf_model, xgb_model)
                        if model is not None
                    ]
                    if len(components) < 2:
                        raise RuntimeError(
                            "ensemble training requires both component models"
                        )
                    ensemble_model = ProbabilityEnsemble(components)
                    trainer.evaluate_model(
                        ensemble_model, X_test, y_test, 'ensemble'
                    )
                    trainer.save_model(
                        ensemble_model,
                        'ensemble',
                        config.output_dir,
                        feature_names,
                        symbol,
                        timeframe,
                        training_dataset_version,
                        data_manifest,
                    )
                
                # Step 6: Save summary
                summary = {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'training_date': datetime.now().isoformat(),
                    'n_samples': len(df),
                    'data_manifest': data_manifest,
                    'n_features': len(feature_names),
                    'features': feature_names,
                    'label_diagnostics': label_diagnostics,
                    'class_weight_strategy': config.class_weight_strategy,
                    'class_weights': {
                        str(key): value for key, value in trainer.class_weights.items()
                    },
                    'metrics': trainer.metrics
                }
                
                summary_path = Path(config.output_dir) / f"training_summary_{symbol}_{timeframe}.json"
                with open(summary_path, 'w') as f:
                    json.dump(summary, f, indent=2)
                
                logger.info(f"Training summary saved to {summary_path}")
                trained_count += 1
                
            except Exception as e:
                logger.error(f"Error training {symbol} {timeframe}: {e}", exc_info=True)
                continue
    
    logger.info("\n" + "=" * 60)
    logger.info("Training pipeline completed!")
    logger.info("=" * 60)
    if trained_count == 0:
        logger.error(
            "No model was trained. Ensure the market watcher has produced at least "
            f"{config.min_samples} candles for the requested symbol/timeframe."
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()

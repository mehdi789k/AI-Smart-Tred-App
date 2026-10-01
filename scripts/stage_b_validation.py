#!/usr/bin/env python3
"""Walk-forward and baseline validation for the Stage B research model."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    balanced_accuracy_score,
    classification_report,
    f1_score,
)

from scripts.train_model import (
    FeatureEngineer,
    TrainingConfig,
    TripleBarrierLabeler,
    _load_database_candles,
)


LABELS = [0, 1, 2]
LABEL_NAMES = ["SELL", "HOLD", "BUY"]


def expanding_splits(
    sample_count: int, n_splits: int, test_size: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return expanding, ordered train/test windows."""
    if sample_count < (n_splits + 1) * test_size:
        raise ValueError("not enough samples for requested walk-forward windows")
    first_test_start = sample_count - n_splits * test_size
    splits = []
    for fold in range(n_splits):
        test_start = first_test_start + fold * test_size
        test_end = test_start + test_size
        train = np.arange(0, test_start)
        test = np.arange(test_start, test_end)
        splits.append((train, test))
    return splits


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    """Calculate class-aware metrics with stable label ordering."""
    report = classification_report(
        y_true,
        y_pred,
        labels=LABELS,
        target_names=LABEL_NAMES,
        output_dict=True,
        zero_division=0,
    )
    return {
        "macro_f1": float(f1_score(y_true, y_pred, labels=LABELS, average="macro")),
        "balanced_accuracy": float(
            balanced_accuracy_score(y_true, y_pred)
        ),
        "class_report": report,
    }


def _oos_trade_metrics(
    frame: pd.DataFrame,
    test_idx: np.ndarray,
    prediction: np.ndarray,
    *,
    initial_capital: float,
    spread: float,
    slippage: float,
    commission: float,
) -> dict[str, Any]:
    """Simulate one-bar-ahead OOS trades with explicit round-trip costs."""
    pnl = []
    signals = []
    for row_idx, label in zip(test_idx, prediction):
        next_idx = row_idx + 1
        if label == 1 or next_idx >= len(frame):
            continue
        direction = 1.0 if label == 2 else -1.0
        entry = float(frame["open"].iloc[next_idx])
        exit_price = float(frame["close"].iloc[next_idx])
        gross = direction * (exit_price - entry)
        net = gross - spread - (2.0 * slippage) - commission
        pnl.append(net)
        signals.append(label)
    equity = initial_capital + np.cumsum(pnl)
    curve = np.concatenate(([initial_capital], equity))
    peak = np.maximum.accumulate(curve)
    drawdown = np.min((curve - peak) / peak) if len(curve) else 0.0
    winners = [value for value in pnl if value > 0]
    losers = [value for value in pnl if value <= 0]
    gross_loss = abs(sum(losers))
    return {
        "trades": len(pnl),
        "buy_signals": signals.count(2),
        "sell_signals": signals.count(0),
        "total_return": float(sum(pnl)),
        "expectancy": float(np.mean(pnl)) if pnl else 0.0,
        "profit_factor": float(sum(winners) / gross_loss) if gross_loss else 0.0,
        "gross_profit": float(sum(winners)),
        "gross_loss": float(gross_loss),
        "max_drawdown": float(abs(drawdown)),
        "win_rate": float(len(winners) / len(pnl)) if pnl else 0.0,
    }


def validate_frame(
    frame: pd.DataFrame,
    *,
    n_splits: int = 5,
    test_size: int = 3000,
    random_state: int = 42,
    time_barrier_bars: int = 20,
    spread: float = 0.30,
    slippage: float = 0.05,
    commission: float = 0.07,
) -> dict[str, Any]:
    """Run purged walk-forward validation and a majority-class baseline."""
    if not frame.index.is_monotonic_increasing:
        raise ValueError("validation data must be chronological")
    config = TrainingConfig(
        symbols=["XAUUSD"],
        timeframes=["M5"],
        min_samples=30000,
        model_type="randomforest",
        use_hyperparameter_tuning=False,
        cv_folds=0,
        random_state=random_state,
        time_barrier_bars=time_barrier_bars,
    )
    engineer = FeatureEngineer(config)
    engineered = engineer.calculate_all_features(frame)
    labeled = TripleBarrierLabeler(config).label(engineered)
    names = engineer.feature_names
    dataset = labeled[names + ["label_class", "open", "close"]].dropna()
    X = dataset[names].to_numpy()
    y = dataset["label_class"].astype(int).to_numpy()
    splits = expanding_splits(len(dataset), n_splits, test_size)
    folds = []
    purge = config.time_barrier_bars
    total_trade_metrics = []
    labeling_sensitivity = []
    for horizon in (10, 20, 40):
        sensitivity_config = TrainingConfig(
            symbols=["XAUUSD"],
            timeframes=["M5"],
            min_samples=30000,
            time_barrier_bars=horizon,
            use_hyperparameter_tuning=False,
            cv_folds=0,
        )
        sensitivity_labeled = TripleBarrierLabeler(sensitivity_config).label(engineered)
        counts = sensitivity_labeled["label_class"].value_counts()
        labeling_sensitivity.append(
            {
                "time_barrier_bars": horizon,
                "SELL": int(counts.get(0, 0)),
                "HOLD": int(counts.get(1, 0)),
                "BUY": int(counts.get(2, 0)),
            }
        )

    for fold_number, (train_idx, test_idx) in enumerate(splits, start=1):
        if len(train_idx) <= purge:
            raise ValueError("training window is shorter than purge horizon")
        fit_idx = train_idx[:-purge]
        model = RandomForestClassifier(
            n_estimators=config.n_estimators_rf,
            max_depth=config.max_depth_rf,
            class_weight="balanced",
            random_state=random_state,
            n_jobs=-1,
        )
        model.fit(X[fit_idx], y[fit_idx])
        prediction = model.predict(X[test_idx])
        majority = np.full(len(test_idx), int(pd.Series(y[fit_idx]).mode().iloc[0]))
        folds.append(
            {
                "fold": fold_number,
                "train_samples": int(len(fit_idx)),
                "test_samples": int(len(test_idx)),
                "test_start": str(dataset.index[test_idx[0]]),
                "test_end": str(dataset.index[test_idx[-1]]),
                "model": _metrics(y[test_idx], prediction),
                "majority_baseline": _metrics(y[test_idx], majority),
                "class_distribution": {
                    name: int((y[test_idx] == label).sum())
                    for label, name in zip(LABELS, LABEL_NAMES)
                },
            }
        )
        trade_metrics = _oos_trade_metrics(
            dataset,
            test_idx,
            prediction,
            initial_capital=10_000.0,
            spread=spread,
            slippage=slippage,
            commission=commission,
        )
        folds[-1]["oos_backtest"] = trade_metrics
        total_trade_metrics.append(trade_metrics)

    model_scores = [fold["model"]["macro_f1"] for fold in folds]
    baseline_scores = [fold["majority_baseline"]["macro_f1"] for fold in folds]
    return {
        "schema_version": "1.0",
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "samples": int(len(dataset)),
        "features": int(len(names)),
        "purge_bars": purge,
        "n_splits": n_splits,
        "test_size": test_size,
        "model": {
            "macro_f1_mean": float(np.mean(model_scores)),
            "macro_f1_std": float(np.std(model_scores)),
        },
        "majority_baseline": {
            "macro_f1_mean": float(np.mean(baseline_scores)),
            "macro_f1_std": float(np.std(baseline_scores)),
        },
        "beats_baseline": bool(np.mean(model_scores) > np.mean(baseline_scores)),
        "folds": folds,
        "leakage_audit": {
            "chronological_index": True,
            "purged_label_horizon": purge,
            "future_named_features": [
                name for name in names
                if any(token in name.lower() for token in ("future", "target", "label"))
            ],
        },
        "oos_backtest": {
            "cost_model": {
                "spread_price_units": spread,
                "slippage_price_units_per_side": slippage,
                "commission_price_units_per_trade": commission,
                "entry": "next_bar_open",
                "exit": "next_bar_close",
                "position_size": "one_price_unit",
            },
            "total_trades": int(sum(item["trades"] for item in total_trade_metrics)),
            "total_return": float(sum(item["total_return"] for item in total_trade_metrics)),
            "expectancy": float(
                np.mean([item["expectancy"] for item in total_trade_metrics])
            ),
            "profit_factor": float(
                sum(item["gross_profit"] for item in total_trade_metrics)
                / max(sum(item["gross_loss"] for item in total_trade_metrics), 1e-12)
            ),
            "max_drawdown": float(max(item["max_drawdown"] for item in total_trade_metrics)),
            "buy_signals": int(sum(item["buy_signals"] for item in total_trade_metrics)),
            "sell_signals": int(sum(item["sell_signals"] for item in total_trade_metrics)),
        },
        "labeling_sensitivity": labeling_sensitivity,
    }


def main() -> None:
    """Load canonical database candles and write the validation report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--output", default="models/stage_b_walk_forward_XAUUSD_M5.json")
    parser.add_argument("--splits", type=int, default=5)
    parser.add_argument("--test-size", type=int, default=3000)
    parser.add_argument("--time-barrier-bars", type=int, default=20)
    parser.add_argument("--spread", type=float, default=0.30)
    parser.add_argument("--slippage", type=float, default=0.05)
    parser.add_argument("--commission", type=float, default=0.07)
    args = parser.parse_args()
    if not args.database_url:
        raise ValueError("--database-url or DATABASE_URL is required")
    frame = asyncio.run(
        _load_database_candles(args.database_url, "XAUUSD", "M5", 30000)
    )
    report = validate_frame(
        frame,
        n_splits=args.splits,
        test_size=args.test_size,
        time_barrier_bars=args.time_barrier_bars,
        spread=args.spread,
        slippage=args.slippage,
        commission=args.commission,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

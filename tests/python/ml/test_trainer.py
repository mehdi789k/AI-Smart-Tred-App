import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.train_model import (
    ModelTrainer,
    TrainingConfig,
    TripleBarrierLabeler,
    analyze_label_distribution,
    calculate_class_weights,
    load_market_data,
)
from src.python.ml.config import MLConfig
from src.python.ml.ensemble import ProbabilityEnsemble
from src.python.ml.governance import build_dataset_manifest, sha256_file, write_manifest
from src.python.ml.predictor import MLInferenceService
from src.python.ml.registry import ModelRegistry
from src.python.ml.trainer import (
    DataQualityError,
    DataSourceError,
    assess_data_quality,
    load_ohlcv_database,
    load_training_data,
    save_artifact,
    time_split,
    walk_forward_splits,
)


class Model:
    classes_ = np.array([0, 1, 2])

    def predict(self, X):
        return np.array([0, 1, 2, 0, 1, 2])[: len(X)]

    def predict_proba(self, X):
        return np.tile([1 / 3, 1 / 3, 1 / 3], (len(X), 1))


def test_load_market_data_accepts_connector_candles_without_time_iso(tmp_path):
    (tmp_path / "DSHUSD_l_M5.json").write_text(
        json.dumps(
            {
                "candles": [
                    {
                        "time": 1_700_000_000,
                        "open": 1,
                        "high": 2,
                        "low": 0,
                        "close": 1.5,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    frame = load_market_data("DSHUSD_l", "M5", 2, tmp_path)

    assert len(frame) == 1
    assert frame.index[0] == pd.Timestamp("2023-11-14 22:13:20", tz="UTC")


def test_time_split_and_walk_forward():
    X = np.arange(100)
    y = np.arange(100)
    parts = time_split(X, y)
    assert [len(x) for x in parts[:3]] == [70, 15, 15]
    splits = list(walk_forward_splits(X, 3))
    assert len(splits) == 3
    assert max(splits[0][0]) < min(splits[0][1])


def test_save_artifact(tmp_path):
    path = save_artifact(Model(), ["close"], tmp_path / "a.pkl", model_version="1")
    assert path.exists()
    service = MLInferenceService(path)
    assert service.metadata["model_version"] == "1"


def test_governance_manifest_and_registry_metadata(tmp_path):
    frame = pd.DataFrame({"close": [100.0, 101.0], "volume": [10, 11]})
    manifest = build_dataset_manifest(
        frame,
        dataset_name="test",
        source="fixture",
        labels=["HOLD", "BUY"],
    )
    manifest_path = write_manifest(manifest, tmp_path / "manifest.json")
    assert manifest_path.exists()
    assert len(manifest["dataset_version"]) == 64
    assert len(manifest["feature_schema_hash"]) == 64

    artifact = save_artifact(
        Model(),
        ["close"],
        tmp_path / "model.pkl",
        dataset_version=manifest["dataset_version"],
        model_version="2026.01",
        walk_forward_validation={"folds": 2},
    )
    registry = ModelRegistry(tmp_path / "registry")
    registry.register("test", artifact, "2026.01")
    metadata = registry.metadata("test", "2026.01")
    assert metadata["dataset_version"] == manifest["dataset_version"]
    assert metadata["walk_forward_validation"] == {"folds": 2}
    assert metadata["artifact_checksum"] == sha256_file(artifact)


def test_golden_dataset_manifest_matches_fixture():
    fixture_dir = Path(__file__).resolve().parents[2] / "fixtures"
    manifest = json.loads(
        (fixture_dir / "golden_dataset.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["sha256"] == sha256_file(fixture_dir / "golden_dataset.csv")


def test_probability_ensemble_is_artifact_compatible():
    first = Model()
    second = Model()
    ensemble = ProbabilityEnsemble([first, second])
    result = ensemble.predict_proba(np.zeros((2, 1)))
    assert result.shape == (2, 3)
    assert np.allclose(result.sum(axis=1), 1.0)
    assert ensemble.predict(np.zeros((2, 1))).tolist() == [0, 0]


def test_saved_soft_voting_ensemble_is_loadable_by_inference(tmp_path):
    from sklearn.linear_model import LogisticRegression

    x = np.arange(18, dtype=float).reshape(-1, 1)
    y = np.tile([0, 1, 2], 6)
    first = LogisticRegression(max_iter=300).fit(x, y)
    second = LogisticRegression(max_iter=300, C=0.5).fit(x, y)
    ensemble = ProbabilityEnsemble([first, second])
    # This is the metadata emitted by scripts/train_model.py for the
    # composite wrapper, which has no sklearn classes_ property by default.
    ensemble.classes_ = np.array([0, 1, 2])

    path = save_artifact(
        ensemble,
        ["close"],
        tmp_path / "ensemble_latest.pkl",
        classes_=ensemble.classes_.tolist(),
        schema_version="1.0",
    )
    service = MLInferenceService(path)
    close = np.arange(100, 160, dtype=float)
    result = service.predict(
        pd.DataFrame(
            {
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
            }
        )
    )
    assert result.direction in {"BUY", "SELL", "HOLD"}
    assert set(result.probabilities) == {"BUY", "SELL", "HOLD"}


def test_database_source_reads_and_validates_sqlite(tmp_path):
    db = tmp_path / "candles.db"
    con = sqlite3.connect(db)
    con.execute(
        "create table market_data (time text, symbol text, timeframe text, "
        "open real, high real, low real, close real, volume integer)"
    )
    rows = [
        (
            f"2026-01-01 00:{i:02d}:00",
            "XAUUSD",
            "M5",
            100 + i,
            101 + i,
            99 + i,
            100.5 + i,
            10,
        )
        for i in range(5)
    ]
    con.executemany("insert into market_data values (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    frame = load_ohlcv_database(f"sqlite:///{db}", min_bars=5)
    assert len(frame) == 5
    assert "tick_volume" in frame.columns


def test_database_source_prefers_canonical_ohlcv_data_table(tmp_path):
    db = tmp_path / "canonical.db"
    con = sqlite3.connect(db)
    con.execute(
        "create table ohlcv_data (symbol text, timeframe text, timestamp text, "
        "open real, high real, low real, close real, tick_volume real)"
    )
    rows = [
        (
            "XAUUSD",
            "M5",
            f"2026-01-01 00:{i:02d}:00",
            100 + i,
            101 + i,
            99 + i,
            100.5 + i,
            10,
        )
        for i in range(5)
    ]
    con.executemany("insert into ohlcv_data values (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    frame = load_ohlcv_database(f"sqlite:///{db}", min_bars=5)
    assert len(frame) == 5
    assert frame["close"].iloc[-1] == 104.5


def test_database_source_can_filter_mt5_provenance_without_fallback(tmp_path):
    db = tmp_path / "source-filter.db"
    con = sqlite3.connect(db)
    con.execute(
        "create table ohlcv_data (symbol text, timeframe text, timestamp text, "
        "open real, high real, low real, close real, tick_volume real, source text)"
    )
    rows = [
        (
            "XAUUSD",
            "M5",
            f"2026-01-01 00:{i:02d}:00",
            100 + i,
            101 + i,
            99 + i,
            100.5 + i,
            10,
            "mt5_official",
        )
        for i in range(5)
    ]
    rows.append(
        (
            "XAUUSD",
            "M5",
            "2026-01-01 00:05:00",
            200,
            201,
            199,
            200.5,
            10,
            "other_provider",
        )
    )
    con.executemany("insert into ohlcv_data values (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    frame = load_ohlcv_database(
        f"sqlite:///{db}",
        symbol="XAUUSD",
        timeframe="M5",
        min_bars=5,
        candle_source="mt5_official",
    )
    assert len(frame) == 5
    assert frame["close"].iloc[-1] == 104.5


def test_database_rejects_insufficient_samples(tmp_path):
    db = tmp_path / "empty.db"
    con = sqlite3.connect(db)
    con.execute(
        "create table market_data (time text, symbol text, timeframe text, "
        "open real, high real, low real, close real, volume integer)"
    )
    con.commit()
    con.close()
    with pytest.raises(DataSourceError, match="insufficient|OHLCV"):
        load_training_data("database", database_url=f"sqlite:///{db}", min_samples=3)


def test_config_requires_database_url():
    with pytest.raises(ValueError, match="database_url"):
        MLConfig(source="database", min_samples=60)


def quality_frame(n=12):
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = np.arange(n, dtype=float) + 100
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close},
        index=index,
    )


def test_training_gate_emits_auditable_report():
    report = assess_data_quality(
        quality_frame(),
        symbol="XAUUSD",
        timeframe="M5",
        min_bars=10,
        source="fixture",
        ingestion_metadata={"batch": "test"},
    )
    assert report["symbol"] == "XAUUSD"
    assert report["bars"] == 12
    assert report["timezone_utc"] is True
    assert report["oldest"].endswith("+00:00")
    assert report["ingestion_metadata"]["batch"] == "test"


def test_training_gate_rejects_naive_timestamps_and_gaps():
    frame = quality_frame()
    frame.index = frame.index.tz_localize(None)
    with pytest.raises(DataQualityError, match="timezone-aware"):
        assess_data_quality(frame, symbol="XAUUSD", timeframe="M5", min_bars=10)

    frame = quality_frame()
    frame = frame.drop(frame.index[[5, 6]])
    with pytest.raises(DataQualityError, match="unexpected gap"):
        assess_data_quality(frame, symbol="XAUUSD", timeframe="M5", min_bars=10)

    with pytest.raises(DataQualityError) as error:
        assess_data_quality(
            frame,
            symbol="XAUUSD",
            timeframe="M5",
            min_bars=10,
            max_unexpected_gaps=0,
        )
    assert error.value.report["unexpected_gaps"]


def test_training_gate_rejects_duplicate_and_invalid_ohlc():
    frame = quality_frame()
    duplicate = pd.concat([frame, frame.iloc[[0]]])
    with pytest.raises(DataQualityError, match="duplicate"):
        assess_data_quality(duplicate, symbol="XAUUSD", timeframe="M5", min_bars=10)
    frame.loc[frame.index[0], "high"] = 0
    with pytest.raises(DataQualityError, match="OHLC validity"):
        assess_data_quality(frame, symbol="XAUUSD", timeframe="M5", min_bars=10)


def test_training_gate_accepts_configured_cross_midnight_session_break():
    frame = quality_frame(30)
    frame.index = pd.date_range("2026-01-01 23:55", periods=30, freq="5min", tz="UTC")
    frame = frame.drop(
        pd.date_range("2026-01-02 00:00", "2026-01-02 00:55", freq="5min", tz="UTC")
    )
    report = assess_data_quality(
        frame,
        symbol="XAUUSD",
        timeframe="M5",
        min_bars=10,
        expected_gap_windows=["23:55-01:05"],
    )
    assert report["status"] == "accepted"
    assert report["unexpected_gaps"] == []


def test_stage_b_baseline_defaults_and_confusion_matrix():
    config = TrainingConfig()
    assert config.model_type == "randomforest"
    assert config.use_hyperparameter_tuning is False
    assert "23:55-01:05" in config.expected_gap_windows

    trainer = ModelTrainer(config)
    model = Model()
    metrics = trainer.evaluate_model(
        model,
        np.zeros((6, 1)),
        np.array([0, 1, 2, 0, 1, 2]),
        "baseline",
    )
    assert metrics["confusion_matrix"] == [
        [2, 0, 0],
        [0, 2, 0],
        [0, 0, 2],
    ]
    assert metrics["confusion_matrix_labels"] == ["SELL", "HOLD", "BUY"]
    assert set(metrics["classification_report"]) >= {"SELL", "HOLD", "BUY", "accuracy"}


def test_triple_barrier_labels_first_barrier_and_hold_reasons():
    frame = pd.DataFrame(
        {
            "close": [100.0, 100.0, 100.0, 100.0],
            "high": [100.0, 103.0, 100.5, 100.5],
            "low": [100.0, 99.5, 100.0, 100.0],
            "atr": [1.0, 1.0, 1.0, 1.0],
        },
        index=pd.date_range("2026-01-01", periods=4, freq="h"),
    )
    config = TrainingConfig(
        profit_target_multiplier=2.0,
        stop_loss_multiplier=1.0,
        time_barrier_bars=2,
    )
    labeled = TripleBarrierLabeler(config).label(frame)
    assert labeled.loc[labeled.index[0], "label"] == 1
    assert labeled.loc[labeled.index[1], "label"] == 0
    assert labeled.loc[labeled.index[1], "label_reason"] == "time_barrier"
    assert labeled.loc[labeled.index[2], "label_reason"] == "time_cutoff"


def test_label_diagnostics_report_monthly_hold_share():
    frame = pd.DataFrame(
        {
            "label": [0, 0, 1, -1],
            "label_reason": [
                "time_barrier",
                "invalid_atr",
                "profit_target",
                "stop_loss",
            ],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-15", "2026-02-01", "2026-02-15"]),
    )
    report = analyze_label_distribution(frame, hold_warning_threshold=0.5)
    assert report["counts"] == {"SELL": 1, "HOLD": 2, "BUY": 1}
    assert report["hold"]["warning"] is True
    assert report["hold"]["reason_counts"] == {
        "time_barrier": 1,
        "invalid_atr": 1,
    }
    assert report["monthly"]["2026-01"]["HOLD"] == 2
    assert report["monthly"]["2026-02"]["BUY"] == 1


def test_label_diagnostics_warns_for_underrepresented_class():
    frame = pd.DataFrame(
        {"label": [0] * 9 + [1], "label_reason": ["time_barrier"] * 10}
    )
    report = analyze_label_distribution(frame, class_imbalance_warning_threshold=0.2)
    assert report["class_imbalance"]["warning"] is True
    assert "BUY" in report["class_imbalance"]["underrepresented_classes"]


def test_class_weights_use_only_observed_training_classes():
    weights = calculate_class_weights(np.array([0, 0, 0, 1, 2]), "balanced")
    assert set(weights) == {0, 1, 2}
    assert weights[0] < weights[1]
    assert weights[0] < weights[2]
    assert calculate_class_weights(np.array([0, 1, 1]), "none") == {
        0: 1.0,
        1: 1.0,
    }


def test_training_config_loads_grouped_stage_c_json():
    config_path = "scripts/ml_training_config.json"
    with open(config_path, encoding="utf-8") as config_file:
        config = TrainingConfig.from_dict(json.load(config_file))

    assert config.profit_target_multiplier == 2.0
    assert config.stop_loss_multiplier == 1.0
    assert config.time_barrier_bars == 20
    assert config.model_type == "ensemble"
    assert config.test_size == 0.15
    assert config.val_size == 0.15
    assert config.class_weight_strategy == "balanced"

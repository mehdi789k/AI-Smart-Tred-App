from __future__ import annotations

import numpy as np
import pandas as pd


def make_df(n=600, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", periods=n)
    trend = np.linspace(0, 0.6, n)
    noise = rng.normal(0, 0.02, n).cumsum()
    close = 100 * np.exp(trend + noise)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = low + (high - low) * rng.random(n)
    vol = rng.integers(500_000, 3_000_000, n).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=dates)


def test_discover_strategies_finds_plugin_files():
    from strategies.base import discover_strategies

    specs = discover_strategies()
    assert "MA-Crossover" in specs
    # همه استراتژی‌ها باید کلاس معتبر و SPEC داشته باشند
    for key, entry in specs.items():
        assert entry["class"] is not None
        assert "fields" in entry


def test_run_backtest_smoke():
    from engine import run_backtest
    from strategies.base import RISK_FIELDS, discover_strategies

    specs = discover_strategies()
    spec = specs["MA-Crossover"]
    params = {f["key"]: f["default"] for f in spec["fields"]}
    params.update({f["key"]: f["default"] for f in RISK_FIELDS})

    res = run_backtest(make_df(), "MA-Crossover", params)
    m = res["metrics"]
    assert len(res["equity"]) == 600
    assert m["total_trades"] >= 0
    assert np.isfinite(m["final_value"])


def test_validate_source_rejects_dangerous_imports():
    from strategies.base import validate_source

    bad = (
        "import socket\n"
        "from strategies.base import RiskManagedStrategy, BASE_PARAMS\n"
        "class S(RiskManagedStrategy):\n"
        "    params = BASE_PARAMS\n"
        "SPEC = {'key': 'S', 'title': 't', 'desc': 'd', 'fields': []}\n"
        "def get_strategy_class():\n"
        "    return S\n"
    )
    errors = validate_source(bad)
    assert any("socket" in e for e in errors)


def test_mt5_export_loader_reads_repo_market_data():
    """پل ادغام: داشبورد باید CSVهای خروجی ابزار استخراج MT5 را بخواند."""
    from data_source import list_mt5_exports, load_mt5_csv, mt5_available_symbols

    symbols = mt5_available_symbols()
    assert "XAUUSD_l" in symbols
    files = list_mt5_exports("XAUUSD_l", "D1")
    assert files, "CSV استخراج‌شده MT5 برای XAUUSD_l/D1 یافت نشد"
    df = load_mt5_csv(files[-1])
    assert {"Open", "High", "Low", "Close"} <= set(df.columns)
    assert len(df) > 10
    assert df.index.is_monotonic_increasing

# -*- coding: utf-8 -*-
"""تست خودکار موتور بک‌تست با داده مصنوعی (بدون نیاز به اینترنت)."""
import numpy as np
import pandas as pd

from engine import run_backtest, optimize_strategy
from strategies.base import discover_strategies
STRATEGY_SPECS = discover_strategies()


def make_df(n=600, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", periods=n)
    # روند + نویز
    trend = np.linspace(0, 0.6, n)
    noise = rng.normal(0, 0.02, n).cumsum()
    close = 100 * np.exp(trend + noise)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = low + (high - low) * rng.random(n)
    vol = rng.integers(500_000, 3_000_000, n).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=dates)


def defaults_for(spec_key):
    spec = STRATEGY_SPECS[spec_key]
    from strategies.base import RISK_FIELDS
    p = {f["key"]: f["default"] for f in spec["fields"]}
    p.update({f["key"]: f["default"] for f in RISK_FIELDS})
    return p


if __name__ == "__main__":
    df = make_df()
    print(f"rows={len(df)} first_close={df.Close.iloc[0]:.2f} last_close={df.Close.iloc[-1]:.2f}")
    ok = True
    for key in STRATEGY_SPECS:
        try:
            res = run_backtest(df, key, defaults_for(key))
            m = res["metrics"]
            print(f"[OK] {key:22s} trades={m['total_trades']:3d} "
                  f"ret={m['total_return_pct']:+7.2f}% "
                  f"sharpe={m['sharpe']} dd={m['max_drawdown_pct']:.1f}% "
                  f"win%={m['win_rate_pct']:.0f} equity_len={len(res['equity'])}")
        except Exception as e:
            ok = False
            print(f"[FAIL] {key}: {type(e).__name__}: {e}")

    # تست بهینه‌ساز
    grid = {"fast_period": [5, 10], "slow_period": [30, 50]}
    base = defaults_for("MA-Crossover")
    out = optimize_strategy(df, "MA-Crossover", base, grid)
    print("\nOptimizer result:\n", out.to_string(index=False))
    print("\nALL PASS" if ok else "\nSOME FAILED")

# Stage D: walk-forward backtesting

`src.python.backtest` provides a side-effect-free, event-driven simulator.
Market data can be a dataframe, a sequence of bar mappings, or a mapping keyed
by `(symbol, timeframe)` (the repository's OHLCV contract uses
`symbol/timeframe/timestamp/open/high/low/close/volume`).

Strategies subclass `Strategy` and return `OrderIntent` values from
`on_bar(bar, indicators, ml_prediction)`.  A strategy can implement `on_tick`
for tick consumers and `train(data)` for walk-forward training.  Existing
`BaseStrategy.generate_signal` strategies remain supported by the compatibility
adapter in `BacktestEngine`.

`BacktestEngine.walk_forward(factory, data, train_window, test_window, step)`
fits each strategy only on the in-sample window and evaluates the immediately
following out-of-sample window.  `GridOptimizer` can select parameters on each
training window.  The returned `WalkForwardResult.out_of_sample` aggregates
only test folds.

Execution models bid/ask spread (absolute price or rate), slippage,
per-notional commission, long and short positions, stop-loss/take-profit
intrabar exits, signal reversals, and volume/fill-ratio partial fills.
Invalid configuration and malformed bars fail early rather than silently
producing a misleading report.  `generate_html_report` and `trades_to_csv`
return report content; callers decide whether and where to persist it.

## ML promotion gate

Backtest output is not sufficient by itself for model promotion. The model
registry requires a reproducibility record containing `dataset_version`,
`feature_schema_hash`, `artifact_checksum`, and completed out-of-sample
walk-forward evidence with at least two folds.

Promotion also requires the combined evaluation record produced by
`build_evaluation_record`, with finite values for:

- risk-adjusted return (minimum `0.0`);
- maximum drawdown (maximum `0.20`, expressed as a fraction);
- Sharpe ratio (minimum `0.5`);
- stability (minimum `0.70`).

The decision records the policy version and a rollback model version. A failed
criterion rejects promotion; it never falls back to raw return or an implicit
approval. Mutable registries, caches, and generated artifacts belong outside
source control. Only intentional fixtures or checksummed release artifacts
should be versioned.

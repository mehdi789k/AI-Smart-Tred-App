"""Serialization helpers for backtest and walk-forward results.

The functions return content rather than writing files, which keeps reporting
usable from the dashboard, a CLI, and tests without introducing filesystem
side effects.
"""

from __future__ import annotations

import csv
import html
import io

from .engine import BacktestResult, WalkForwardResult


def trades_to_csv(result: BacktestResult) -> str:
    output = io.StringIO()
    fields = [
        "entry_time",
        "exit_time",
        "symbol",
        "direction",
        "entry_price",
        "exit_price",
        "quantity",
        "pnl",
        "pnl_percent",
        "exit_reason",
        "commission",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for trade in result.trades:
        row = {field: getattr(trade, field) for field in fields}
        row["entry_time"] = trade.entry_time.isoformat()
        row["exit_time"] = trade.exit_time.isoformat()
        writer.writerow(row)
    return output.getvalue()


def generate_html_report(
    result: BacktestResult | WalkForwardResult, title: str = "Backtest report"
) -> str:
    if isinstance(result, WalkForwardResult):
        summary = result.out_of_sample
        folds = result.folds
    else:
        summary = result
        folds = []
    metrics = summary.to_dict()
    metric_rows = "".join(
        f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in metrics.items()
        if key not in {"trades", "equity_curve"}
    )
    fold_rows = "".join(
        f"<tr><td>{index + 1}</td><td>{fold.total_trades}</td><td>{fold.total_pnl:.6f}</td>"
        f"<td>{fold.metrics.sharpe_ratio if fold.metrics else 0:.4f}</td></tr>"
        for index, fold in enumerate(folds)
    )
    folds_html = (
        "<h2>Walk-forward folds</h2><table><tr><th>Fold</th><th>Trades</th>"
        "<th>P&amp;L</th><th>Sharpe</th></tr>" + fold_rows + "</table>"
        if folds
        else ""
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui;margin:2rem}table{border-collapse:collapse}"
        "th,td{border:1px solid #ccc;padding:.35rem .6rem;text-align:right}"
        "th{text-align:left;background:#f3f3f3}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><table>{metric_rows}</table>{folds_html}"
        "</body></html>"
    )


def generate_report(
    result: BacktestResult | WalkForwardResult, format: str = "html"
) -> str:
    """Return an HTML or CSV report; reject ambiguous formats defensively."""

    normalized = format.lower().strip()
    if normalized in {"html", "htm"}:
        return generate_html_report(result)
    if normalized == "csv":
        if not isinstance(result, BacktestResult):
            result = result.out_of_sample
        return trades_to_csv(result)
    raise ValueError("format must be html or csv")

"""Verify live MT5 readiness without invoking an order endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from python.dashboard.mt5_connector import MT5Connection  # noqa: E402
from python.execution.live_readiness import validate_live_readiness  # noqa: E402
from python.risk.circuit_breaker import CircuitBreaker  # noqa: E402


def fetch_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    """Fetch a non-trading API status document."""
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned an invalid JSON object")
    return payload


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def validate_live_runtime(
    *,
    base_url: str,
    timeout: float = 5.0,
    allow_direct_dashboard: bool = False,
    connector: Any | None = None,
) -> dict[str, Any]:
    """Validate the local MT5 owner, risk breaker, and API health."""
    if not _env_bool("MT5_AUTO_TRADING_ENABLED") or _env_bool("MT5_DEMO_ENABLED"):
        raise RuntimeError("live environment flags are not enabled")
    if not os.getenv("MT5_LOGIN") or not os.getenv("MT5_SERVER"):
        raise RuntimeError("MT5_LOGIN and MT5_SERVER are required")
    symbols = frozenset(
        item.strip().upper()
        for item in os.getenv("MT5_LIVE_SYMBOLS", "").split(",")
        if item.strip()
    )
    if connector is None:
        connector = MT5Connection()
        if not connector.connect(
            login=int(os.environ["MT5_LOGIN"]),
            password=os.getenv("MT5_PASSWORD"),
            server=os.environ["MT5_SERVER"],
        ):
            raise RuntimeError("MT5 connection failed")

    report = validate_live_readiness(
        connector,
        expected_login=int(os.environ["MT5_LOGIN"]),
        expected_server=os.environ["MT5_SERVER"],
        allowed_symbols=symbols,
        max_tick_age_seconds=float(os.getenv("MT5_MAX_TICK_AGE_SECONDS", "30")),
    )
    if not report.ready:
        raise RuntimeError("live readiness failed: " + ", ".join(report.reasons))

    account = connector.get_account_summary()
    capital = float(account.get("equity") or account.get("balance") or 0)
    if capital <= 0:
        raise RuntimeError("MT5 account equity must be positive")
    history = connector.get_history(days=1)
    if hasattr(history, "to_dict"):
        history = history.to_dict(orient="records")
    trades = []
    for trade in history or []:
        row = dict(trade)
        if "pnl" not in row and "profit" in row:
            row["pnl"] = row["profit"]
        trades.append(row)
    breaker = CircuitBreaker(
        capital,
        max_daily_loss_percent=float(os.getenv("MT5_MAX_DAILY_LOSS_PERCENT", "2")),
    )
    breaker_result = breaker.evaluate(trades)
    if not breaker_result["trading_allowed"]:
        raise RuntimeError(f"circuit breaker tripped: {breaker_result['stop_reason']}")

    health = fetch_json(base_url, "/health", timeout)
    if health.get("data", {}).get("status") != "ok":
        raise RuntimeError("API liveness is not healthy")
    api_readiness = "direct_dashboard_owner"
    if not allow_direct_dashboard:
        readiness = fetch_json(base_url, "/ready", timeout)
        if readiness.get("data", {}).get("trading", {}).get("allowed") is not True:
            raise RuntimeError("API live readiness is not allowed")
        api_readiness = readiness["data"]["dependencies"]

    return {
        "ready": True,
        "account": {
            "login": account.get("login"),
            "server": account.get("server"),
            "currency": account.get("currency"),
        },
        "symbols": report.symbols,
        "circuit_breaker": "armed",
        "api_readiness": api_readiness,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--allow-direct-dashboard", action="store_true")
    args = parser.parse_args()
    result = validate_live_runtime(
        base_url=args.base_url,
        timeout=args.timeout,
        allow_direct_dashboard=args.allow_direct_dashboard,
    )
    print(json.dumps(result, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

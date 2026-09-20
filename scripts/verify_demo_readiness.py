"""Validate the non-trading Demo/MT5 operational gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from python.execution.shadow import ShadowOrderLedger  # noqa: E402


def fetch_json(base_url: str, path: str, timeout: float) -> dict:
    """Fetch one API health document without invoking an order endpoint."""
    request = Request(f"{base_url.rstrip('/')}{path}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"{path} returned HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"{path} is unavailable") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned an invalid JSON object")
    return payload


def validate_demo_readiness(
    health: dict,
    readiness: dict,
    *,
    demo_symbols: list[str] | None = None,
    max_daily_loss: float = 10.0,
) -> None:
    """Fail closed unless the API is alive and trading gates are explicitly safe."""
    if health.get("data", {}).get("status") != "ok":
        raise RuntimeError("API liveness is not healthy")
    data = readiness.get("data", {})
    if data.get("status") != "ready":
        raise RuntimeError("API readiness is not healthy")
    if data.get("trading", {}).get("allowed") is not True:
        raise RuntimeError("trading gate is not allowed for Demo validation")
    dependencies = data.get("dependencies", {})
    if dependencies.get("mt5") != "ready":
        raise RuntimeError("MT5 is not connected for Demo validation")
    if dependencies.get("circuit_breaker") not in {"armed", "not_configured"}:
        raise RuntimeError("circuit breaker is not safe for Demo validation")
    if demo_symbols is not None and len({symbol.upper() for symbol in demo_symbols}) != 1:
        raise RuntimeError("controlled Demo requires a single allowed symbol")
    if max_daily_loss <= 0 or max_daily_loss > 10.0:
        raise RuntimeError("controlled Demo daily loss limit is unsafe")


def validate_shadow_readiness(
    ledger_path: str | Path,
    *,
    audit_path: str | Path | None = None,
    max_drawdown: float = 0.0,
) -> None:
    """Validate Shadow completeness before any controlled Demo activation."""
    report = ShadowOrderLedger(ledger_path).validate_for_demo(
        audit_path=audit_path,
        max_drawdown=max_drawdown,
    )
    if not report["ready"]:
        raise RuntimeError(
            "Shadow readiness failed: " + ", ".join(report["reasons"])
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--demo-symbol", action="append", dest="demo_symbols")
    parser.add_argument("--max-daily-loss", type=float, default=10.0)
    parser.add_argument("--shadow-ledger")
    parser.add_argument("--audit-log")
    parser.add_argument("--max-shadow-drawdown", type=float, default=0.0)
    args = parser.parse_args()
    health = fetch_json(args.base_url, "/health", args.timeout)
    readiness = fetch_json(args.base_url, "/ready", args.timeout)
    validate_demo_readiness(
        health,
        readiness,
        demo_symbols=args.demo_symbols,
        max_daily_loss=args.max_daily_loss,
    )
    if args.shadow_ledger:
        validate_shadow_readiness(
            args.shadow_ledger,
            audit_path=args.audit_log,
            max_drawdown=args.max_shadow_drawdown,
        )
    print("Demo readiness passed; no order endpoint was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

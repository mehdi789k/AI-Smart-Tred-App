"""Dashboard-controlled scheduler for safe, periodic ML retraining."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    """Run the existing retraining command at a bounded polling interval."""
    parser = argparse.ArgumentParser(description="Run periodic ML retraining checks")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--schedule", choices=("daily", "weekly", "monthly"), required=True)
    parser.add_argument("--interval-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.interval_seconds < 60:
        raise SystemExit("--interval-seconds must be at least 60")

    project_root = Path(__file__).resolve().parents[1]
    retrain_script = project_root / "scripts" / "retrain_model.py"
    while True:
        result = subprocess.run(
            [
                sys.executable,
                str(retrain_script),
                "--symbol",
                args.symbol,
                "--timeframe",
                args.timeframe,
                "--schedule",
                args.schedule,
                "--models-dir",
                str(project_root / "models"),
                "--data-dir",
                str(project_root / "market_data"),
            ],
            cwd=str(project_root),
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(result.returncode)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()

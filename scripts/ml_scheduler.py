"""Dashboard-controlled scheduler for safe, periodic ML retraining."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def _acquire_scheduler_lock(lock_path: Path) -> int | None:
    """Acquire the scheduler singleton lock and return an existing owner PID."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError:
        try:
            owner_pid = int(lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            owner_pid = 0
        if owner_pid > 0:
            try:
                os.kill(owner_pid, 0)
            except (OSError, SystemError):
                lock_path.unlink(missing_ok=True)
                return _acquire_scheduler_lock(lock_path)
            return owner_pid
        lock_path.unlink(missing_ok=True)
        return _acquire_scheduler_lock(lock_path)

    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
    return None


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
    lock_path = project_root / "data" / "ml_scheduler.lock"
    existing_pid = _acquire_scheduler_lock(lock_path)
    if existing_pid is not None:
        return
    try:
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
    finally:
        try:
            if int(lock_path.read_text(encoding="ascii").strip()) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()

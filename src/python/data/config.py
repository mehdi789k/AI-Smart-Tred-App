from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover - optional in non-MT5 environments.
    mt5 = None

DEFAULT_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]

TIMEFRAME_MAP: dict[str, int] = {
    "M1": getattr(mt5, "TIMEFRAME_M1", 1) if mt5 is not None else 1,
    "M5": getattr(mt5, "TIMEFRAME_M5", 5) if mt5 is not None else 5,
    "M15": getattr(mt5, "TIMEFRAME_M15", 15) if mt5 is not None else 15,
    "M30": getattr(mt5, "TIMEFRAME_M30", 30) if mt5 is not None else 30,
    "H1": getattr(mt5, "TIMEFRAME_H1", 16385) if mt5 is not None else 16385,
    "H4": getattr(mt5, "TIMEFRAME_H4", 16388) if mt5 is not None else 16388,
    "D1": getattr(mt5, "TIMEFRAME_D1", 16390) if mt5 is not None else 16390,
    "W1": getattr(mt5, "TIMEFRAME_W1", 16392) if mt5 is not None else 16392,
    "MN1": getattr(mt5, "TIMEFRAME_MN1", 16393) if mt5 is not None else 16393,
}


def _read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def load_local_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for env_path in candidates:
        for key, value in _read_env_file(env_path).items():
            os.environ.setdefault(key, value)


def resolve_timeframes(values: Iterable[str] | None = None) -> list[str]:
    if values is None:
        selected = DEFAULT_TIMEFRAMES
    else:
        selected = [str(value).strip().upper() for value in values if str(value).strip()]

    missing = [item for item in selected if item not in TIMEFRAME_MAP]
    if missing:
        raise ValueError(f"Unsupported MT5 timeframes: {', '.join(missing)}")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in selected:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


load_local_env()

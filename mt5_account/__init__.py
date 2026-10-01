"""Backward-compatible imports for the canonical MT5 account package."""

from pathlib import Path
import sys

_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "python"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

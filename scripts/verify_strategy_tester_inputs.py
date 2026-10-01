"""Validate fail-closed inputs before an MT5 Strategy Tester run."""

from __future__ import annotations

from pathlib import Path


def validate_inputs(ea_source: Path, set_file: Path) -> None:
    """Reject a tester run if the EA or set file permits live orders."""
    if not ea_source.is_file():
        raise RuntimeError(f"EA source not found: {ea_source}")
    if not set_file.is_file():
        raise RuntimeError(f"Strategy Tester set file not found: {set_file}")
    source = ea_source.read_text(encoding="utf-8")
    settings = set_file.read_text(encoding="utf-8")
    if "InpAllowLiveTrading=false" not in settings:
        raise RuntimeError("Strategy Tester set file must disable live trading")
    if "InpAllowLiveTrading" not in source:
        raise RuntimeError("EA source does not expose the live-trading safety input")
    if "InpEnableZmq=false" not in settings:
        raise RuntimeError("Strategy Tester set file must disable ZeroMQ")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ea_source", type=Path)
    parser.add_argument("set_file", type=Path)
    arguments = parser.parse_args()
    validate_inputs(arguments.ea_source, arguments.set_file)
    print("strategy_tester_inputs_safe=true")
    print("live_trading=false")
    print("zmq=false")

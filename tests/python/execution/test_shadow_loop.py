from pathlib import Path

from src.python.execution import AutoTrader, ShadowTradingLoop, TradingConfig


def test_shadow_loop_reads_each_configured_live_data_pair(tmp_path: Path):
    calls = []
    trader = AutoTrader(
        TradingConfig(
            execution_mode="shadow",
            symbols=["EURUSD", "XAUUSD"],
            timeframes=["M5", "H1"],
            symbol_timeframes={"EURUSD": "M5", "XAUUSD": "H1"},
            shadow_ledger_path=str(tmp_path / "shadow.jsonl"),
        )
    )

    def provider(symbols, timeframe):
        calls.append((symbols, timeframe))
        symbol = symbols[0]
        return {symbol: {"symbol": symbol, "candles": []}}, {symbol: 100.0}

    loop = ShadowTradingLoop(trader, provider)
    assert loop.run_once() is None
    loop.start()
    cycle = loop.run_once()

    assert cycle is not None
    assert calls == [(["EURUSD"], "M5"), (["XAUUSD"], "H1")]
    assert loop.last_status == "running"
    loop.stop()
    assert loop.last_status == "stopped"

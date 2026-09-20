"""Read-only verification of the currently connected MT5 terminal."""

from __future__ import annotations

import argparse
import os

import MetaTrader5 as mt5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal-path", default=os.getenv("MT5_TERMINAL_PATH") or None)
    parser.add_argument("--expected-server", default=os.getenv("MT5_SERVER") or "")
    args = parser.parse_args()

    initialized = mt5.initialize(path=args.terminal_path) if args.terminal_path else mt5.initialize()
    if not initialized:
        raise SystemExit("MT5 initialize failed")
    try:
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        if terminal is None or not getattr(terminal, "connected", False):
            raise SystemExit("MT5 terminal is not connected")
        if account is None:
            raise SystemExit("MT5 account information is unavailable")
        server = str(getattr(account, "server", ""))
        if args.expected_server and server != args.expected_server:
            raise SystemExit("connected MT5 server does not match MT5_SERVER")
        if "demo" not in server.lower():
            raise SystemExit("connected account server is not identified as Demo")
        if bool(getattr(account, "trade_allowed", False)):
            print("warning=terminal_allows_trading; this probe sent no orders")
        print("mt5_connected=true")
        print("demo_server=true")
        print("account_identity_present=true")
        print("orders_sent=0")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())

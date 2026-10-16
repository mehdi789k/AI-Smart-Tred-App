from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


def _read_env_file(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def _load_local_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path.cwd().parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for env_path in candidates:
        for key, value in _read_env_file(env_path).items():
            os.environ.setdefault(key, value)

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover - optional dependency in non-MT5 environments.
    mt5 = None

DEFAULT_DEMO_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
DEFAULT_FILE_NAME = "market_watch.json"

_load_local_env()


@dataclass
class MarketWatchRow:
    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    spread: float | None = None
    time: str | None = None

    @classmethod
    def from_mt5(cls, symbol: str, tick, info) -> "MarketWatchRow":
        spread = None
        if info is not None:
            spread = getattr(info, "spread", None)
            if spread is not None and float(spread) <= 0:
                spread = None

        time_value = None
        if tick is not None:
            raw_time = getattr(tick, "time", None)
            if raw_time:
                time_value = datetime.fromtimestamp(raw_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")

        def positive_quote(value) -> float | None:
            if value is None:
                return None
            quote = float(value)
            return quote if quote > 0 else None

        return cls(
            symbol=symbol,
            bid=positive_quote(getattr(tick, "bid", None)) if tick is not None else None,
            ask=positive_quote(getattr(tick, "ask", None)) if tick is not None else None,
            last=positive_quote(getattr(tick, "last", None)) if tick is not None else None,
            spread=float(spread) if spread is not None else None,
            time=time_value,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "spread": self.spread,
            "time": self.time,
        }


def parse_symbol_list(raw: str | None) -> list[str]:
    if not raw:
        return []

    items = []
    for part in raw.split(","):
        symbol = part.strip()
        if symbol:
            items.append(symbol)
    return items


def load_symbol_file(path: str | Path) -> list[str]:
    candidate = Path(path).expanduser()
    if not candidate.exists():
        raise FileNotFoundError(f"Watchlist file not found: {candidate}")

    if candidate.suffix.lower() == ".json":
        with candidate.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if isinstance(payload, dict):
            for key in ("symbols", "watchlist", "selected_symbols"):
                if key in payload:
                    values = payload[key]
                    if isinstance(values, list):
                        return [str(item["symbol"] if isinstance(item, dict) else item) for item in values]
            if "symbol" in payload:
                return [str(payload["symbol"])]

        if isinstance(payload, list):
            return [str(item["symbol"] if isinstance(item, dict) else item) for item in payload]

        raise ValueError(f"Unsupported JSON format in {candidate}")

    if candidate.suffix.lower() == ".csv":
        with candidate.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                for key in ("symbol", "Symbol"):
                    if key in row and row[key]:
                        return [str(row[key]).strip()]
            raise ValueError(f"CSV file {candidate} does not contain a symbol column")

    if candidate.suffix.lower() in {".txt", ".dat"}:
        with candidate.open("r", encoding="utf-8") as handle:
            symbols = [line.strip() for line in handle if line.strip()]
        return symbols

    raise ValueError(f"Unsupported watchlist format for {candidate}")


def default_watchlist_file() -> Path:
    env_value = os.getenv("MT5_MARKET_WATCH_FILE")
    if env_value:
        return Path(env_value).expanduser()
    return Path.cwd() / DEFAULT_FILE_NAME


def fetch_live_watchlist(symbols: Iterable[str] | None = None) -> list[MarketWatchRow]:
    if mt5 is None:
        raise RuntimeError(
            "MetaTrader5 package is not installed. Install MetaTrader5 to pull live values from MT5."
        )

    path = os.getenv("MT5_PATH")
    login = os.getenv("MT5_LOGIN")
    password = os.getenv("MT5_PASSWORD")
    server = os.getenv("MT5_SERVER")

    credentials = (login, password, server)
    if any(credentials) and not all(credentials):
        raise RuntimeError(
            "MT5_LOGIN, MT5_PASSWORD, and MT5_SERVER must either all be set or all be omitted. "
            "If omitted, the already-open, logged-in MT5 terminal will be used."
        )

    initialize_options: dict[str, object] = {"timeout": 15000}
    if path:
        terminal_path = Path(path).expanduser()
        if terminal_path.is_dir():
            terminal_path = next(
                (
                    terminal_path / executable
                    for executable in ("terminal64.exe", "terminal.exe")
                    if (terminal_path / executable).is_file()
                ),
                terminal_path,
            )
        if not terminal_path.is_file():
            raise RuntimeError(
                f"MT5_PATH does not point to a terminal executable or installation folder: {terminal_path}"
            )
        initialize_options["path"] = str(terminal_path)
    if all(credentials):
        try:
            initialize_options.update(
                login=int(login),
                password=password,
                server=server,
            )
        except ValueError as exc:
            raise RuntimeError("MT5_LOGIN must be a numeric account number.") from exc

    initialized = mt5.initialize(**initialize_options)
    if not initialized:
        error = mt5.last_error()
        raise RuntimeError(f"MetaTrader5 failed to initialize: {error}")

    try:
        selected_symbols = list(symbols) if symbols is not None else mt5.symbols_get()
        if selected_symbols is None:
            error = mt5.last_error()
            raise RuntimeError(f"Unable to read symbols from the MT5 Market Watch: {error}")

        reading_market_watch = symbols is None
        rows: list[MarketWatchRow] = []
        for item in selected_symbols:
            symbol_name = str(getattr(item, "name", item)).strip()
            if not symbol_name:
                continue

            info = mt5.symbol_info(symbol_name)
            if reading_market_watch and (info is None or not getattr(info, "visible", False)):
                continue
            tick = mt5.symbol_info_tick(symbol_name)
            rows.append(MarketWatchRow.from_mt5(symbol_name, tick, info))

        if reading_market_watch and not rows:
            raise RuntimeError(
                "No symbols are visible in the MT5 Market Watch. Add symbols to Market Watch and try again."
            )
        return rows
    finally:
        mt5.shutdown()


def render_rows(rows: Sequence[MarketWatchRow]) -> str:
    if not rows:
        return "No symbols available for the current Market Watch."

    headers = ["Symbol", "Bid", "Ask", "Last", "Spread", "Time"]
    widths = [len(header) for header in headers]
    cells: list[list[str]] = []

    for row in rows:
        values = [
            row.symbol,
            "-" if row.bid is None else f"{row.bid:,.5f}",
            "-" if row.ask is None else f"{row.ask:,.5f}",
            "-" if row.last is None else f"{row.last:,.5f}",
            "-" if row.spread is None else str(int(row.spread)),
            row.time or "-",
        ]
        cells.append(values)
        for index, value in enumerate(values):
            widths[index] = max(widths[index], len(value))

    lines = [
        " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "-+-".join("-" * widths[index] for index in range(len(headers))),
    ]

    for values in cells:
        lines.append(" | ".join(value.ljust(widths[index]) for index, value in enumerate(values)))

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display the user-selected market watch symbols from MetaTrader 5 in Python."
    )
    parser.add_argument("--symbols", help="Comma-separated symbols to display, for example EURUSD,GBPUSD")
    parser.add_argument("--file", help="Path to a JSON/CSV/TXT file containing the selected watchlist symbols")
    parser.add_argument("--path", help="Optional MetaTrader 5 installation path. Overrides MT5_PATH environment variable.")
    parser.add_argument("--login", type=int, help="Optional MT5 account login. Overrides MT5_LOGIN environment variable.")
    parser.add_argument("--password", help="Optional MT5 account password. Overrides MT5_PASSWORD environment variable.")
    parser.add_argument("--server", help="Optional MT5 broker server name. Overrides MT5_SERVER environment variable.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Render a sample watchlist without connecting to MetaTrader 5.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the watchlist as JSON instead of a table.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.path:
        os.environ["MT5_PATH"] = args.path
    if args.login is not None:
        os.environ["MT5_LOGIN"] = str(args.login)
    if args.password:
        os.environ["MT5_PASSWORD"] = args.password
    if args.server:
        os.environ["MT5_SERVER"] = args.server

    try:
        if args.demo:
            symbols = DEFAULT_DEMO_SYMBOLS
            rows = [
                MarketWatchRow(symbol=symbol, bid=1.0 + index * 0.01, ask=1.0 + index * 0.01 + 0.0003, last=None, spread=3, time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z"))
                for index, symbol in enumerate(symbols)
            ]
        else:
            if args.symbols:
                symbols = parse_symbol_list(args.symbols)
            elif args.file:
                symbols = load_symbol_file(args.file)
            else:
                watchlist_file = default_watchlist_file()
                if watchlist_file.exists():
                    symbols = load_symbol_file(watchlist_file)
                else:
                    symbols = None

            if symbols == []:
                raise SystemExit(
                    "The supplied market watch list is empty. Add symbols to it or remove --file to read the open MT5 Market Watch."
                )
            rows = fetch_live_watchlist(symbols)

        if args.json:
            print(json.dumps([row.to_dict() for row in rows], indent=2))
        else:
            print(render_rows(rows))
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

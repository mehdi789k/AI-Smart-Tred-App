"""Fetch, merge, persist, and display MetaTrader 5 account history."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import MetaTrader5 as mt5

DATA_DIR = Path(__file__).resolve().parent / "account_history"
POLL_INTERVAL_SECONDS = 1.0
CHUNK_DAYS = 7
QUERY_TIME_MARGIN = timedelta(hours=12)
_history_cache: dict[str, Any] | None = None
_history_file: Path | None = None


def mt5_is_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq terminal64.exe"],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "terminal64.exe" in result.stdout.lower()


def internet_is_available() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return True
    except OSError:
        return False


def initialize_mt5() -> None:
    if mt5.initialize():
        return
    raise RuntimeError(f"اتصال به MT5 ناموفق بود: {mt5.last_error()}")


def wait_for_connection() -> None:
    while True:
        if mt5_is_running() and internet_is_available():
            try:
                initialize_mt5()
                terminal = mt5.terminal_info()
                account = mt5.account_info()
                if terminal is not None and terminal.connected and account is not None:
                    return
            except RuntimeError:
                pass
        mt5.shutdown()
        print("در انتظار اتصال MT5 و اینترنت...", flush=True)
        time.sleep(POLL_INTERVAL_SECONDS)


def _json_value(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _record_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "_asdict"):
        values = record._asdict()
    elif hasattr(record, "__dict__"):
        values = vars(record)
    else:
        raise TypeError(f"رکورد MT5 قابل تبدیل نیست: {type(record).__name__}")
    result = {name: _json_value(value) for name, value in values.items()}
    for field in ("time", "time_setup", "time_done", "time_expiration"):
        value = result.get(field)
        if isinstance(value, (int, float)) and value > 0:
            result[f"{field}_iso"] = datetime.fromtimestamp(
                value, timezone.utc
            ).isoformat(sep=" ", timespec="seconds")
    return result


def _record_key(record: dict[str, Any], prefix: str) -> str:
    ticket = record.get("ticket")
    if ticket is not None:
        return f"{prefix}:{ticket}"
    identity = (
        record.get("time") or record.get("time_setup") or record.get("time_done") or ""
    )
    return f"{prefix}:{identity}:{record.get('symbol', '')}:{record.get('type', '')}"


def _load_history(file_path: Path) -> dict[str, Any]:
    if not file_path.is_file():
        return {}
    try:
        with file_path.open("r", encoding="utf-8") as file:
            history = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"فایل تاریخچه قابل خواندن نیست: {file_path}") from error
    if not isinstance(history, dict):
        raise RuntimeError(f"ساختار فایل تاریخچه نامعتبر است: {file_path}")
    for name in ("deals", "orders"):
        if not isinstance(history.get(name, []), list):
            raise RuntimeError(f"بخش {name} باید فهرست باشد: {file_path}")
    return history


def _merge_history(
    existing: dict[str, Any], deals: Any, orders: Any
) -> tuple[dict[str, Any], bool]:
    collections = {
        "deals": {
            _record_key(record, "deal"): record
            for record in existing.get("deals", [])
            if isinstance(record, dict)
        },
        "orders": {
            _record_key(record, "order"): record
            for record in existing.get("orders", [])
            if isinstance(record, dict)
        },
    }
    changed = False
    for name, values in (("deals", deals), ("orders", orders)):
        for value in values:
            record = _record_dict(value)
            key = _record_key(record, name[:-1])
            if collections[name].get(key) != record:
                changed = True
            collections[name][key] = record
    return (
        {
            "deals": sorted(
                collections["deals"].values(),
                key=lambda item: (item.get("time", 0), item.get("ticket", 0)),
            ),
            "orders": sorted(
                collections["orders"].values(),
                key=lambda item: (
                    item.get("time_done", item.get("time_setup", item.get("time", 0))),
                    item.get("ticket", 0),
                ),
            ),
        },
        changed,
    )


def save_history(
    account: Any,
    deals: Any,
    orders: Any,
    days: int,
    existing: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ValueError("تعداد روزها باید عدد صحیح بزرگ‌تر از صفر باشد.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_path = DATA_DIR / f"account_{account.login}_history.json"
    current = _load_history(file_path) if existing is None else existing
    records, changed = _merge_history(current, deals, orders)
    payload = {
        "account": account.login,
        "server": account.server,
        "history_days": days,
        "updated_at": datetime.now(timezone.utc).isoformat(sep=" ", timespec="seconds"),
        **records,
    }
    temporary_path = file_path.with_suffix(".json.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temporary_path.replace(file_path)
    except OSError as error:
        raise RuntimeError(f"ذخیره تاریخچه ناموفق بود: {file_path}") from error
    return file_path, payload, changed


def _fetch_range(
    getter: Callable[[datetime, datetime], Any],
    date_from: datetime,
    date_to: datetime,
) -> list[Any]:
    result = getter(date_from, date_to)
    if result is None:
        raise RuntimeError(f"دریافت تاریخچه ناموفق بود: {mt5.last_error()}")
    return list(result)


def fetch_history_in_chunks(
    date_from: datetime, date_to: datetime
) -> tuple[list[Any], list[Any]]:
    if date_from >= date_to:
        raise ValueError("تاریخ شروع باید قبل از تاریخ پایان باشد.")
    deals: list[Any] = []
    orders: list[Any] = []
    chunk_start = date_from
    while chunk_start < date_to:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), date_to)
        deals.extend(_fetch_range(mt5.history_deals_get, chunk_start, chunk_end))
        orders.extend(_fetch_range(mt5.history_orders_get, chunk_start, chunk_end))
        chunk_start = chunk_end
    return deals, orders


def summarize_history(history: dict[str, Any]) -> dict[str, float | int]:
    deals = history.get("deals", [])
    orders = history.get("orders", [])
    totals: dict[str, float | int] = {
        "deal_count": len(deals),
        "order_count": len(orders),
        "profit": 0.0,
        "commission": 0.0,
        "swap": 0.0,
        "fee": 0.0,
    }
    for deal in deals:
        if isinstance(deal, dict):
            for field in ("profit", "commission", "swap", "fee"):
                value = deal.get(field)
                if isinstance(value, (int, float)):
                    totals[field] += float(value)
    totals["net_result"] = sum(
        float(totals[field]) for field in ("profit", "commission", "swap", "fee")
    )
    return totals


def print_history_records(history: dict[str, Any]) -> None:
    deals = history.get("deals", [])
    orders = history.get("orders", [])
    print("\n=== معاملات ===")
    if not deals:
        print("هیچ معامله‌ای در بازه انتخاب‌شده پیدا نشد.")
    for index, deal in enumerate(deals, 1):
        print(
            f"{index}. ticket={deal.get('ticket', '-')}, "
            f"symbol={deal.get('symbol', '-')}, volume={deal.get('volume', '-')}, "
            f"price={deal.get('price', '-')}, profit={deal.get('profit', 0)}, "
            f"time={deal.get('time_iso', deal.get('time', '-'))}"
        )
    print("\n=== سفارش‌ها ===")
    if not orders:
        print("هیچ سفارشی در بازه انتخاب‌شده پیدا نشد.")
    for index, order in enumerate(orders, 1):
        print(
            f"{index}. ticket={order.get('ticket', '-')}, "
            f"symbol={order.get('symbol', '-')}, type={order.get('type', '-')}, "
            f"volume={order.get('volume_initial', order.get('volume', '-'))}, "
            f"time={order.get('time_setup_iso', order.get('time_setup', '-'))}"
        )


def print_history(days: int) -> Path:
    global _history_cache, _history_file
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        raise ValueError("تعداد روزها باید عدد صحیح بزرگ‌تر از صفر باشد.")
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"دریافت اطلاعات حساب ناموفق بود: {mt5.last_error()}")
    file_path = DATA_DIR / f"account_{account.login}_history.json"
    if _history_cache is None or _history_file != file_path:
        _history_file = file_path
        _history_cache = _load_history(file_path)
    requested_to = datetime.now(timezone.utc)
    requested_from = requested_to - timedelta(days=days)
    date_from = requested_from - QUERY_TIME_MARGIN
    date_to = requested_to + QUERY_TIME_MARGIN
    deals, orders = fetch_history_in_chunks(date_from, date_to)
    file_path, _history_cache, changed = save_history(
        account, deals, orders, days, existing=_history_cache
    )
    summary = summarize_history(_history_cache)
    print(
        f"\nحساب: {account.login} / {account.server}\n"
        f"بازه درخواستی: {requested_from:%Y-%m-%d %H:%M:%S} تا "
        f"{requested_to:%Y-%m-%d %H:%M:%S} UTC\n"
        f"دریافت این چرخه: {len(deals)} معامله، {len(orders)} سفارش | "
        f"تغییر: {'بله' if changed else 'خیر'}\n"
        f"ذخیره شد: {file_path}\n"
        f"جمع: {summary['deal_count']} معامله، {summary['order_count']} سفارش | "
        f"نتیجه خالص: {summary['net_result']:.2f}"
    )
    print_history_records(_history_cache)
    return file_path


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--once", action="store_true", help="فقط یک بار دریافت و ذخیره کن"
    )
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days باید بزرگ‌تر از صفر باشد.")
    try:
        wait_for_connection()
        if args.once:
            print_history(args.days)
            return
        while True:
            started = time.monotonic()
            if mt5.terminal_info() is None or mt5.account_info() is None:
                mt5.shutdown()
                wait_for_connection()
            print_history(args.days)
            time.sleep(max(0.0, POLL_INTERVAL_SECONDS - (time.monotonic() - started)))
    except KeyboardInterrupt:
        print("\nدریافت تاریخچه متوقف شد.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

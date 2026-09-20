"""Print MetaTrader 5 account, open-position, and limit-order information."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import MetaTrader5 as mt5


def print_record_details(record: object, title: str) -> None:
    """Print every field returned by an MT5 namedtuple record."""
    fields = record._asdict() if hasattr(record, "_asdict") else vars(record)
    print(f"\n--- {title} ---")
    for name, value in fields.items():
        print(f"{name}: {value}")


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


def initialize_mt5() -> bool:
    return mt5.initialize()


def print_account_info() -> None:
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"دریافت اطلاعات حساب ناموفق بود: {mt5.last_error()}")

    print("\n=== اطلاعات حساب ===")
    print(f"حساب: {account.login}")
    print(f"سرور: {account.server}")
    print(f"موجودی (Balance): {account.balance:.2f} {account.currency}")
    print(f"دارایی (Equity): {account.equity:.2f} {account.currency}")
    print(f"مارجین استفاده‌شده (Margin): {account.margin:.2f} {account.currency}")
    print(f"مارجین آزاد (Free Margin): {account.margin_free:.2f} {account.currency}")
    print(f"سطح مارجین (Margin Level): {account.margin_level:.2f}%")


def print_positions() -> None:
    positions = mt5.positions_get()
    if positions is None:
        raise RuntimeError(f"دریافت پوزیشن‌های باز ناموفق بود: {mt5.last_error()}")

    print("\n=== پوزیشن‌های باز ===")
    if not positions:
        print("موردی وجود ندارد.")
        return

    for index, position in enumerate(positions, start=1):
        print_record_details(position, f"پوزیشن {index}")


def print_limit_orders() -> None:
    orders = mt5.orders_get()
    if orders is None:
        raise RuntimeError(f"دریافت سفارش‌ها ناموفق بود: {mt5.last_error()}")

    order_types = {
        mt5.ORDER_TYPE_BUY: "BUY",
        mt5.ORDER_TYPE_SELL: "SELL",
        mt5.ORDER_TYPE_BUY_LIMIT: "BUY LIMIT",
        mt5.ORDER_TYPE_SELL_LIMIT: "SELL LIMIT",
        mt5.ORDER_TYPE_BUY_STOP: "BUY STOP",
        mt5.ORDER_TYPE_SELL_STOP: "SELL STOP",
        mt5.ORDER_TYPE_BUY_STOP_LIMIT: "BUY STOP LIMIT",
        mt5.ORDER_TYPE_SELL_STOP_LIMIT: "SELL STOP LIMIT",
    }

    print("\n=== سفارش‌های فعال ===")
    if not orders:
        print("موردی وجود ندارد.")
        return

    for index, order in enumerate(orders, start=1):
        print_record_details(
            order,
            f"سفارش {index} ({order_types.get(order.type, order.type)})",
        )


def print_all_account_info() -> None:
    print_account_info()
    print_positions()
    print_limit_orders()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    connected = False
    previous_internet_state = internet_is_available()
    waiting_reported = False
    try:
        while True:
            current_internet_state = internet_is_available()
            if current_internet_state != previous_internet_state:
                print(
                    "اینترنت دوباره وصل شد."
                    if current_internet_state
                    else "اتصال اینترنت قطع شد.",
                    flush=True,
                )
                previous_internet_state = current_internet_state

            if not current_internet_state or not mt5_is_running():
                if connected:
                    mt5.shutdown()
                    connected = False
                if not waiting_reported:
                    print(
                        "در انتظار اتصال اینترنت و بازشدن MetaTrader 5 هستم.",
                        flush=True,
                    )
                    waiting_reported = True
                time.sleep(1)
                continue

            if not connected:
                connected = initialize_mt5()
                if connected:
                    print("به MetaTrader 5 بازِ موجود متصل شد.", flush=True)
                    waiting_reported = False
                else:
                    time.sleep(1)
                    continue

            if mt5.terminal_info() is None:
                connected = False
                mt5.shutdown()
                time.sleep(1)
                continue

            try:
                print("\033[H\033[J", end="")
                print_all_account_info()
            except RuntimeError as error:
                print(f"دریافت اطلاعات ناموفق بود: {error}", flush=True)
                connected = False
                mt5.shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nبروزرسانی اطلاعات متوقف شد.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

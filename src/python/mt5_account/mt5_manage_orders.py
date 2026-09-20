"""Interactively modify MetaTrader 5 positions and pending orders."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from typing import Any

import MetaTrader5 as mt5

try:
    from .mt5_trade_orders import validate_legacy_order_gate
except ImportError:  # pragma: no cover - supports direct script execution
    from mt5_trade_orders import validate_legacy_order_gate


def _confirm_legacy_action(action: str) -> str:
    """Require an explicit per-operation confirmation for this compatibility path."""
    answer = input(f"تأیید {action}؟ برای ادامه YES وارد کنید: ").strip()
    if answer != "YES":
        raise RuntimeError(
            "عملیات لغو شد؛ برای اجرای زنده از confirmation در LiveOrderWorkflow استفاده کنید."
        )
    return "interactive-confirmed"


def _guard_request(request: dict[str, Any]) -> None:
    validate_legacy_order_gate(
        request.get("symbol"),
        request.get("volume"),
        magic=request.get("magic"),
        # Validate all risk settings before prompting.  The prompt below is
        # the explicit operator confirmation for this interactive-only path.
        dry_run=True,
    )


def _guard_legacy_path() -> None:
    """Fail closed before any MT5 query is made by this compatibility CLI."""
    validate_legacy_order_gate(dry_run=True)


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


def connect_to_open_terminal() -> None:
    if not mt5.initialize():
        raise RuntimeError(f"اتصال به MT5 ناموفق بود: {mt5.last_error()}")
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or not terminal.connected or account is None:
        mt5.shutdown()
        raise RuntimeError(f"حساب یا ترمینال آماده نیست: {mt5.last_error()}")


def wait_for_connection() -> None:
    reported = False
    while True:
        if not mt5_is_running() or not internet_is_available():
            if not reported:
                print("اتصال قطع است؛ در انتظار اتصال مجدد هستم.", flush=True)
                reported = True
            mt5.shutdown()
            time.sleep(1)
            continue

        try:
            terminal = mt5.terminal_info()
            account = mt5.account_info()
            if terminal is not None and terminal.connected and account is not None:
                if reported:
                    print("اتصال دوباره برقرار شد.", flush=True)
                return
        except (AttributeError, RuntimeError):
            pass

        mt5.shutdown()
        try:
            connect_to_open_terminal()
            print("به MT5 متصل شد.", flush=True)
            return
        except RuntimeError:
            time.sleep(1)


def _price(value: str | None) -> float | None:
    value = value.strip() if value is not None else ""
    return float(value) if value else None


def _filling_mode(symbol: str) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"اطلاعات نماد دریافت نشد: {mt5.last_error()}")

    supported = info.filling_mode
    for mode in (
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_RETURN,
    ):
        if supported & (1 << mode):
            return mode
    raise RuntimeError(f"هیچ حالت Filling برای نماد {symbol} پشتیبانی نمی‌شود.")


def _send_modify(request: dict[str, Any]) -> Any:
    """Reject the removed direct-send management path.

    Position changes must be routed through ``LiveOrderWorkflow`` so managed
    magic, symbol, confirmation, and audit gates are applied in one place.
    """
    raise RuntimeError(
        "Direct MT5 position management was removed. "
        "Use LiveOrderWorkflow for risk-gated position changes."
    )


def modify_position() -> None:
    _guard_legacy_path()
    ticket = int(input("تیکت پوزیشن: ").strip())
    position = mt5.positions_get(ticket=ticket)
    if not position:
        raise ValueError("پوزیشن با این تیکت پیدا نشد.")

    sl = _price(input("حد ضرر جدید (خالی برای حذف): "))
    tp = _price(input("حد سود جدید (خالی برای حذف): "))
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": position[0].symbol,
        "position": ticket,
        "sl": sl or 0.0,
        "tp": tp or 0.0,
        "magic": int(os.getenv("MT5_LIVE_MAGIC", "0")),
    }
    _send_modify(request)


def close_position() -> None:
    _guard_legacy_path()
    ticket = int(input("تیکت پوزیشن برای بستن کامل: ").strip())
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise ValueError("پوزیشن با این تیکت پیدا نشد.")

    position = positions[0]
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        raise RuntimeError(f"قیمت لحظه‌ای دریافت نشد: {mt5.last_error()}")

    is_buy = position.type == mt5.POSITION_TYPE_BUY
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
        "price": tick.bid if is_buy else tick.ask,
        "position": ticket,
        "deviation": 20,
        "magic": int(os.getenv("MT5_LIVE_MAGIC", "0")),
        "comment": "python-close-position",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(position.symbol),
    }
    _send_modify(request)


def close_all_positions() -> None:
    _guard_legacy_path()
    positions = mt5.positions_get()
    if positions is None:
        raise RuntimeError(f"دریافت پوزیشن‌ها ناموفق بود: {mt5.last_error()}")
    if not positions:
        print("هیچ پوزیشن بازی وجود ندارد.")
        return

    print(f"تعداد پوزیشن‌های قابل بستن: {len(positions)}")
    for position in positions:
        tick = mt5.symbol_info_tick(position.symbol)
        if tick is None:
            raise RuntimeError(
                f"قیمت لحظه‌ای {position.symbol} دریافت نشد: {mt5.last_error()}"
            )

        is_buy = position.type == mt5.POSITION_TYPE_BUY
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask,
            "position": position.ticket,
            "deviation": 20,
            "magic": int(os.getenv("MT5_LIVE_MAGIC", "0")),
            "comment": "python-close-all-positions",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": _filling_mode(position.symbol),
        }
        _send_modify(request)
        print(f"پوزیشن {position.ticket} بسته شد.", flush=True)


def modify_pending_order() -> None:
    _guard_legacy_path()
    ticket = int(input("تیکت سفارش Pending: ").strip())
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        raise ValueError("سفارش Pending با این تیکت پیدا نشد.")

    order = orders[0]
    price = _price(input(f"قیمت جدید (خالی برای {order.price_open}): "))
    sl = _price(input("حد ضرر جدید (خالی برای بدون SL): "))
    tp = _price(input("حد سود جدید (خالی برای بدون TP): "))
    request = {
        "action": mt5.TRADE_ACTION_MODIFY,
        "order": ticket,
        "symbol": order.symbol,
        "price": price if price is not None else order.price_open,
        "sl": sl or 0.0,
        "tp": tp or 0.0,
        "type_time": order.type_time,
        "expiration": order.time_expiration,
        "magic": int(os.getenv("MT5_LIVE_MAGIC", "0")),
    }
    _send_modify(request)


def delete_pending_order() -> None:
    _guard_legacy_path()
    ticket = int(input("تیکت سفارش Pending برای حذف: ").strip())
    orders = mt5.orders_get(ticket=ticket)
    if not orders:
        raise ValueError("سفارش Pending با این تیکت پیدا نشد.")
    _send_modify(
        {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
            "symbol": orders[0].symbol,
            "magic": int(os.getenv("MT5_LIVE_MAGIC", "0")),
        }
    )


def delete_all_pending_orders() -> None:
    _guard_legacy_path()
    orders = mt5.orders_get()
    if orders is None:
        raise RuntimeError(f"دریافت سفارش‌ها ناموفق بود: {mt5.last_error()}")
    if not orders:
        print("هیچ سفارش Pending فعالی وجود ندارد.")
        return

    print(f"تعداد سفارش‌های قابل حذف: {len(orders)}")
    for order in orders:
        _send_modify(
            {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": order.ticket,
                "symbol": order.symbol,
                "magic": int(os.getenv("MT5_LIVE_MAGIC", "0")),
            }
        )
        print(f"سفارش {order.ticket} حذف شد.", flush=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    _guard_legacy_path()
    wait_for_connection()
    account = mt5.account_info()
    print(f"حساب متصل: {account.login} / {account.server}")

    while True:
        wait_for_connection()
        print(
            "\n--- مدیریت سفارش‌ها ---\n"
            "1) ویرایش SL/TP پوزیشن\n"
            "2) ویرایش سفارش Pending\n"
            "3) حذف سفارش Pending\n"
            "4) بستن کامل پوزیشن\n"
            "5) بستن همه پوزیشن‌های باز\n"
            "6) حذف همه سفارش‌های Pending\n"
            "0) خروج"
        )
        try:
            choice = input("انتخاب: ").strip()
            if choice == "0":
                return
            if choice == "1":
                modify_position()
            elif choice == "2":
                modify_pending_order()
            elif choice == "3":
                delete_pending_order()
            elif choice == "4":
                close_position()
            elif choice == "5":
                close_all_positions()
            elif choice == "6":
                delete_all_pending_orders()
            else:
                print("انتخاب نامعتبر است.")
        except (ValueError, RuntimeError) as error:
            print(f"عملیات انجام نشد: {error}")
        print("برای عملیات بعدی آماده است.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nبرنامه متوقف شد.")
    finally:
        mt5.shutdown()

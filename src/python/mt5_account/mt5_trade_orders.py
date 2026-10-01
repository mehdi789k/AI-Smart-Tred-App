"""Send market and limit orders through an already-open MetaTrader 5 terminal.

The terminal must already be open and logged in. Install the dependency with:
    python -m pip install MetaTrader5
"""

from __future__ import annotations

import argparse
import math
import os
import socket
import subprocess
import sys
import time
from typing import Any

import MetaTrader5 as mt5


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def validate_legacy_order_gate(
    symbol: str | None = None,
    volume: float | None = None,
    *,
    magic: int | None = None,
    dry_run: bool = True,
    confirmation_token: str | None = None,
) -> dict[str, Any]:
    """Validate the deliberately restricted compatibility order path.

    This module predates ``LiveOrderWorkflow`` and must never become an
    alternate live-order API.  It is therefore disabled unless both explicit
    legacy and demo flags are enabled.  A non-dry run also needs an operator
    confirmation token; token issuance/validation belongs to
    ``LiveOrderWorkflow`` and callers should migrate there.
    """
    if not _env_true("MT5_LEGACY_ORDER_PATH_ENABLED"):
        raise RuntimeError(
            "Legacy MT5 order path is disabled. Use LiveOrderWorkflow; "
            "set MT5_LEGACY_ORDER_PATH_ENABLED=true only for controlled demo tests."
        )
    if not _env_true("MT5_DEMO_ENABLED"):
        raise RuntimeError(
            "Legacy MT5 order path requires MT5_DEMO_ENABLED=true; "
            "live execution must use LiveOrderWorkflow."
        )

    symbols = {
        item.strip().upper()
        for item in os.getenv("MT5_LIVE_SYMBOLS", "").split(",")
        if item.strip()
    }
    if not symbols:
        raise RuntimeError(
            "MT5_LIVE_SYMBOLS is required and must contain at least one symbol."
        )
    try:
        max_volume = float(os.getenv("MT5_MAX_POSITION_VOLUME", ""))
    except ValueError as exc:
        raise RuntimeError(
            "MT5_MAX_POSITION_VOLUME must be a positive number."
        ) from exc
    if not math.isfinite(max_volume) or max_volume <= 0:
        raise RuntimeError("MT5_MAX_POSITION_VOLUME must be a positive number.")
    try:
        configured_magic = int(os.getenv("MT5_LIVE_MAGIC", ""))
    except ValueError as exc:
        raise RuntimeError(
            "MT5_LIVE_MAGIC is required and must be an integer."
        ) from exc
    if configured_magic <= 0:
        raise RuntimeError("MT5_LIVE_MAGIC must be a positive integer.")

    if symbol is not None and symbol.upper() not in symbols:
        raise ValueError(f"نماد {symbol} در MT5_LIVE_SYMBOLS مجاز نیست.")
    if volume is not None and (not math.isfinite(volume) or volume <= 0):
        raise ValueError("حجم سفارش باید بزرگ‌تر از صفر و متناهی باشد.")
    if volume is not None and volume > max_volume:
        raise ValueError(
            f"حجم {volume} از MT5_MAX_POSITION_VOLUME={max_volume} بیشتر است."
        )
    if magic is not None and magic != configured_magic:
        raise ValueError("magic سفارش باید دقیقاً با MT5_LIVE_MAGIC یکسان باشد.")
    if not dry_run and not (confirmation_token or "").strip():
        raise RuntimeError(
            "اجرای غیر dry-run به confirmation_token نیاز دارد؛ "
            "برای اجرای زنده از LiveOrderWorkflow استفاده کنید."
        )
    return {
        "allowed_symbols": symbols,
        "max_position_volume": max_volume,
        "magic": configured_magic,
    }


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
    """Connect to the currently open MT5 terminal without logging in again."""
    if not mt5.initialize():
        raise RuntimeError(f"اتصال به MetaTrader 5 ناموفق بود: {mt5.last_error()}")

    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None:
        mt5.shutdown()
        raise RuntimeError(f"اطلاعات ترمینال/حساب دریافت نشد: {mt5.last_error()}")


def wait_for_connection() -> None:
    """Wait until MT5 is open, internet is available, and the account is usable."""
    waiting_reported = False
    while True:
        if not mt5_is_running() or not internet_is_available():
            if not waiting_reported:
                print(
                    "MetaTrader 5 یا اینترنت در دسترس نیست؛ در انتظار اتصال مجدد هستم.",
                    flush=True,
                )
                waiting_reported = True
            mt5.shutdown()
            time.sleep(1)
            continue

        try:
            terminal = mt5.terminal_info()
            account = mt5.account_info()
            if terminal is not None and terminal.connected and account is not None:
                if waiting_reported:
                    print("اتصال به MetaTrader 5 دوباره برقرار شد.", flush=True)
                return
        except (AttributeError, RuntimeError):
            pass

        mt5.shutdown()
        try:
            connect_to_open_terminal()
            print("به MetaTrader 5 بازِ موجود متصل شد.", flush=True)
            return
        except RuntimeError:
            time.sleep(1)


def _prepare_symbol(symbol: str) -> tuple[Any, Any]:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"نماد {symbol} پیدا نشد.")

    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"فعال‌کردن نماد {symbol} ناموفق بود: {mt5.last_error()}")

    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.bid <= 0 or tick.ask <= 0:
        raise RuntimeError(f"قیمت معتبر برای {symbol} دریافت نشد: {mt5.last_error()}")
    return info, tick


def _validate_volume(volume: float, info: Any) -> None:
    if volume <= 0:
        raise ValueError("حجم سفارش باید بزرگ‌تر از صفر باشد.")
    if volume < info.volume_min or volume > info.volume_max:
        raise ValueError(f"حجم باید بین {info.volume_min} و {info.volume_max} باشد.")
    steps = round((volume - info.volume_min) / info.volume_step)
    normalized = info.volume_min + steps * info.volume_step
    if abs(normalized - volume) > 1e-9:
        raise ValueError(f"حجم باید مضربی از گام {info.volume_step} باشد.")


def _normalize_price(price: float, info: Any) -> float:
    if price <= 0:
        raise ValueError("قیمت باید بزرگ‌تر از صفر باشد.")
    return round(price, info.digits)


def _validate_stops(
    side: str,
    reference_price: float,
    stop_loss: float | None,
    take_profit: float | None,
    info: Any,
) -> tuple[float | None, float | None]:
    normalized_sl = _normalize_price(stop_loss, info) if stop_loss is not None else None
    normalized_tp = (
        _normalize_price(take_profit, info) if take_profit is not None else None
    )
    minimum_distance = info.trade_stops_level * info.point

    if side == "BUY":
        if normalized_sl is not None and normalized_sl >= reference_price:
            raise ValueError("در BUY، حدضرر باید پایین‌تر از قیمت ورود باشد.")
        if normalized_tp is not None and normalized_tp <= reference_price:
            raise ValueError("در BUY، حدسود باید بالاتر از قیمت ورود باشد.")
        if (
            normalized_sl is not None
            and reference_price - normalized_sl < minimum_distance
        ):
            raise ValueError("فاصله حدضرر از قیمت ورود کمتر از حداقل مجاز نماد است.")
        if (
            normalized_tp is not None
            and normalized_tp - reference_price < minimum_distance
        ):
            raise ValueError("فاصله حدسود از قیمت ورود کمتر از حداقل مجاز نماد است.")
    else:
        if normalized_sl is not None and normalized_sl <= reference_price:
            raise ValueError("در SELL، حدضرر باید بالاتر از قیمت ورود باشد.")
        if normalized_tp is not None and normalized_tp >= reference_price:
            raise ValueError("در SELL، حدسود باید پایین‌تر از قیمت ورود باشد.")
        if (
            normalized_sl is not None
            and normalized_sl - reference_price < minimum_distance
        ):
            raise ValueError("فاصله حدضرر از قیمت ورود کمتر از حداقل مجاز نماد است.")
        if (
            normalized_tp is not None
            and reference_price - normalized_tp < minimum_distance
        ):
            raise ValueError("فاصله حدسود از قیمت ورود کمتر از حداقل مجاز نماد است.")

    return normalized_sl, normalized_tp


def _send_order(request: dict[str, Any]) -> Any:
    """Reject the removed direct-send compatibility path.

    Live order submission is owned by ``LiveOrderWorkflow``. Keeping a second
    implementation here would allow callers to bypass confirmation, daily-loss,
    spread, and audit gates.
    """
    raise RuntimeError(
        "Direct MT5 order submission was removed. "
        "Use LiveOrderWorkflow for risk-gated demo execution."
    )


def send_market_order(
    symbol: str,
    side: str,
    volume: float,
    *,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    deviation: int = 20,
    magic: int = 26090901,
    comment: str = "python-market-order",
    dry_run: bool = True,
    confirmation_token: str | None = None,
) -> Any:
    """Open a market BUY or SELL position and return MT5's result."""
    validate_legacy_order_gate(
        symbol,
        volume,
        magic=magic,
        dry_run=dry_run,
        confirmation_token=confirmation_token,
    )
    if not dry_run:
        raise RuntimeError(
            "Direct MT5 order submission was removed. "
            "Use LiveOrderWorkflow for risk-gated demo execution."
        )
    info, tick = _prepare_symbol(symbol)
    _validate_volume(volume, info)
    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("side باید BUY یا SELL باشد.")

    order_type = mt5.ORDER_TYPE_BUY if normalized_side == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if normalized_side == "BUY" else tick.bid
    normalized_sl, normalized_tp = _validate_stops(
        normalized_side, price, stop_loss, take_profit, info
    )
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "deviation": deviation,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    if normalized_sl is not None:
        request["sl"] = normalized_sl
    if normalized_tp is not None:
        request["tp"] = normalized_tp
    if dry_run:
        return {"status": "dry_run", "request": request}
    return _send_order(request)


def send_limit_order(
    symbol: str,
    side: str,
    volume: float,
    price: float,
    *,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    magic: int = 26090901,
    comment: str = "python-limit-order",
    dry_run: bool = True,
    confirmation_token: str | None = None,
) -> Any:
    """Place a BUY LIMIT or SELL LIMIT pending order."""
    validate_legacy_order_gate(
        symbol,
        volume,
        magic=magic,
        dry_run=dry_run,
        confirmation_token=confirmation_token,
    )
    if not dry_run:
        raise RuntimeError(
            "Direct MT5 order submission was removed. "
            "Use LiveOrderWorkflow for risk-gated demo execution."
        )
    info, tick = _prepare_symbol(symbol)
    _validate_volume(volume, info)
    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise ValueError("side باید BUY یا SELL باشد.")

    entry_price = _normalize_price(price, info)
    if normalized_side == "BUY" and entry_price >= tick.ask:
        raise ValueError("قیمت Buy Limit باید پایین‌تر از Ask فعلی باشد.")
    if normalized_side == "SELL" and entry_price <= tick.bid:
        raise ValueError("قیمت Sell Limit باید بالاتر از Bid فعلی باشد.")
    normalized_sl, normalized_tp = _validate_stops(
        normalized_side, entry_price, stop_loss, take_profit, info
    )

    order_type = (
        mt5.ORDER_TYPE_BUY_LIMIT
        if normalized_side == "BUY"
        else mt5.ORDER_TYPE_SELL_LIMIT
    )
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry_price,
        "magic": magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    if normalized_sl is not None:
        request["sl"] = normalized_sl
    if normalized_tp is not None:
        request["tp"] = normalized_tp
    if dry_run:
        return {"status": "dry_run", "request": request}
    return _send_order(request)


def _read_interactive_order() -> dict[str, Any] | None:
    symbol = input("نماد (خالی برای خروج): ").strip()
    if not symbol or symbol.lower() in {"exit", "quit", "خروج"}:
        return None

    side = input("نوع معامله (BUY یا SELL): ").strip().upper()
    volume = float(input("حجم (مثلاً 0.01): ").strip())
    kind = input("نوع سفارش (market یا limit): ").strip().lower() or "market"
    price = None
    if kind == "limit":
        price = float(input("قیمت ورود: ").strip())
    elif kind != "market":
        raise ValueError("نوع سفارش باید market یا limit باشد.")

    sl_text = input("حد ضرر (اختیاری، Enter برای خالی): ").strip()
    tp_text = input("حد سود (اختیاری، Enter برای خالی): ").strip()
    return {
        "symbol": symbol,
        "side": side,
        "volume": volume,
        "kind": kind,
        "price": price,
        "sl": float(sl_text) if sl_text else None,
        "tp": float(tp_text) if tp_text else None,
    }


def _send_from_values(values: dict[str, Any]) -> Any:
    if values["kind"] == "market":
        return send_market_order(
            values["symbol"],
            values["side"],
            values["volume"],
            stop_loss=values["sl"],
            take_profit=values["tp"],
        )
    if values["price"] is None:
        raise ValueError("قیمت برای سفارش limit الزامی است.")
    return send_limit_order(
        values["symbol"],
        values["side"],
        values["volume"],
        values["price"],
        stop_loss=values["sl"],
        take_profit=values["tp"],
    )


def _run_interactive() -> None:
    # Do not even connect to MT5 unless the compatibility path is explicitly
    # enabled for a demo account.
    validate_legacy_order_gate(dry_run=True)
    wait_for_connection()
    try:
        account = mt5.account_info()
        print(f"حساب متصل: {account.login} / {account.server}")
        while True:
            wait_for_connection()
            print("\n--- سفارش جدید ---")
            try:
                values = _read_interactive_order()
                if values is None:
                    print("برنامه متوقف شد.")
                    return
                result = _send_from_values(values)
                print(f"سفارش با موفقیت ارسال شد. ticket={result.order or result.deal}")
            except (ValueError, RuntimeError) as error:
                print(f"ارسال سفارش ناموفق بود: {error}")
            print("برای ثبت سفارش بعدی، اطلاعات جدید را وارد کنید.")
    finally:
        mt5.shutdown()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    validate_legacy_order_gate(dry_run=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", help="مثلاً EURUSD یا XAUUSD")
    parser.add_argument("--side", choices=("BUY", "SELL"))
    parser.add_argument("--volume", type=float)
    parser.add_argument("--kind", choices=("market", "limit"), default="market")
    parser.add_argument("--price", type=float, help="برای سفارش limit الزامی است")
    parser.add_argument("--sl", type=float)
    parser.add_argument("--tp", type=float)
    args = parser.parse_args()

    if args.symbol is None and args.side is None and args.volume is None:
        try:
            _run_interactive()
        except KeyboardInterrupt:
            print("\nبرنامه متوقف شد.")
        return
    if args.symbol is None or args.side is None or args.volume is None:
        parser.error("--symbol، --side و --volume باید با هم استفاده شوند.")

    if args.kind == "limit" and args.price is None:
        parser.error("--price برای سفارش limit الزامی است.")

    wait_for_connection()
    try:
        account = mt5.account_info()
        print(f"حساب متصل: {account.login} / {account.server}")
        result = _send_from_values(
            {
                "symbol": args.symbol,
                "side": args.side,
                "volume": args.volume,
                "kind": args.kind,
                "price": args.price,
                "sl": args.sl,
                "tp": args.tp,
            }
        )
        print(f"سفارش با موفقیت ارسال شد. ticket={result.order or result.deal}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

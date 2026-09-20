"""Launch MetaTrader 5 and log in using Windows environment variables."""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import MetaTrader5 as mt5


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"متغیر محیطی {name} تنظیم نشده است.")
    return value


def find_terminal() -> Path | None:
    configured_path = os.getenv("MT5_TERMINAL_PATH")
    if configured_path:
        return Path(configured_path)

    candidates = (
        Path(os.environ.get("PROGRAMFILES", "")) / r"MetaTrader 5\terminal64.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / r"MetaTrader 5\terminal64.exe",
        Path(os.environ.get("APPDATA", "")) / r"MetaQuotes\Terminal\terminal64.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


def internet_is_available() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return True
    except OSError:
        return False


def initialize_mt5(
    login: int, password: str, server: str, terminal_path: Path | None
) -> bool:
    initialize_args = {
        "login": login,
        "password": password,
        "server": server,
        "timeout": 60_000,
    }
    if terminal_path is not None:
        initialize_args["path"] = str(terminal_path)
    return mt5.initialize(**initialize_args)


def monitor_connection(
    stop_event: threading.Event,
    expected_login: int,
    password: str,
    expected_server: str,
    terminal_path: Path | None,
) -> None:
    previous_internet_state = internet_is_available()
    initial_terminal = mt5.terminal_info()
    previous_terminal_state = initial_terminal is not None
    print(f"وضعیت اینترنت: {'وصل است' if previous_internet_state else 'قطع است'}")
    print(f"وضعیت برنامه MT5: {'باز است' if previous_terminal_state else 'بسته است'}")

    while not stop_event.wait(1):
        current_internet_state = internet_is_available()
        if current_internet_state != previous_internet_state:
            if current_internet_state:
                print("اینترنت دوباره وصل شد.")
            else:
                print("اتصال اینترنت قطع شد.")
            previous_internet_state = current_internet_state

        terminal = mt5.terminal_info()
        current_terminal_state = terminal is not None
        if current_terminal_state != previous_terminal_state:
            if current_terminal_state:
                print("برنامه MetaTrader 5 دوباره باز شد.")
                mt5.shutdown()
                if initialize_mt5(
                    expected_login, password, expected_server, terminal_path
                ):
                    print("اتصال به MetaTrader 5 پس از بازشدن مجدد برقرار شد.")
                else:
                    print(f"اتصال مجدد به MetaTrader 5 ناموفق بود: {mt5.last_error()}")
            else:
                print("برنامه MetaTrader 5 بسته شد.")
                mt5.shutdown()
                if initialize_mt5(
                    expected_login, password, expected_server, terminal_path
                ):
                    print("پس از بسته‌شدن MT5، لاگین مجدد با موفقیت انجام شد.")
                    previous_terminal_state = True
                else:
                    print(
                        f"لاگین مجدد پس از بسته‌شدن MT5 ناموفق بود: {mt5.last_error()}"
                    )
            previous_terminal_state = current_terminal_state

        account = mt5.account_info()
        connected = (
            terminal is not None
            and terminal.connected
            and account is not None
            and account.login == expected_login
            and account.server == expected_server
        )

        if connected:
            print(f"اتصال برقرار است: حساب {account.login} روی سرور {account.server}")
        else:
            print(f"اتصال قطع است یا حساب تغییر کرده است: {mt5.last_error()}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    login = int(required_env("MT5_LOGIN"))
    password = required_env("MT5_PASSWORD")
    server = required_env("MT5_SERVER")
    terminal_path = find_terminal()

    if not initialize_mt5(login, password, server, terminal_path):
        error = mt5.last_error()
        raise RuntimeError(f"راه‌اندازی یا لاگین MT5 ناموفق بود: {error}")

    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError(f"لاگین انجام نشد: {mt5.last_error()}")

        print(f"ورود موفق بود: حساب {account.login} روی سرور {account.server}")
        stop_event = threading.Event()
        monitor = threading.Thread(
            target=monitor_connection,
            args=(stop_event, login, password, server, terminal_path),
            name="mt5-connection-monitor",
            daemon=True,
        )
        monitor.start()
        print("پایش اتصال هر یک ثانیه فعال شد. برای توقف Ctrl+C را بزنید.")
        try:
            while True:
                stop_event.wait(1)
        except KeyboardInterrupt:
            print("پایش اتصال متوقف شد.")
        finally:
            stop_event.set()
            monitor.join()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

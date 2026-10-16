# -*- coding: utf-8 -*-
"""
ماژول داده‌ها:
- دانلود داده تاریخی (yfinance) + کش محلی روی دیسک
- آپلود فایل CSV توسط کاربر
- شبیه‌سازی داده زنده (Live Feed) برای تست استراتژی در حالت Paper Trading
"""
import os
import time
import datetime as dt

import pandas as pd
import yfinance as yf

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _normalize(df):
    """یکسان‌سازی ستون‌ها و ایندکس تاریخ."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.rename(columns={c: c.title() for c in df.columns})
    keep = [c for c in OHLCV if c in df.columns]
    df = df[keep].dropna()
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def fetch_history(ticker, start, end, interval="1d", use_cache=True):
    """دانلود داده تاریخی با کش روی دیسک (برای اجرای آفلاین/سریع)."""
    if isinstance(start, dt.date):
        start = dt.datetime.combine(start, dt.time())
    if isinstance(end, dt.date):
        end = dt.datetime.combine(end, dt.time())
    cache_file = os.path.join(
        DATA_DIR, f"{ticker}_{interval}_{start:%Y%m%d}_{end:%Y%m%d}.csv")

    if use_cache and os.path.exists(cache_file):
        df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
        return _normalize(df)

    df = yf.download(ticker, start=start, end=end, interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _normalize(df)
    df.to_csv(cache_file)
    return df


def load_csv(fileobj):
    """خواندن فایل CSV آپلودشده کاربر (ستون‌های OHLCV یا Date+Close)."""
    try:
        df = pd.read_csv(fileobj)
    except Exception:
        fileobj.seek(0)
        df = pd.read_csv(fileobj, sep=";")
    # ستون تاریخ می‌تواند Date / datetime / first column باشد
    date_col = None
    for cand in ["Date", "date", "Datetime", "datetime", "timestamp"]:
        if cand in df.columns:
            date_col = cand
            break
    if date_col is None:
        date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col)
    if "Close" not in df.columns and "close" in df.columns:
        df = df.rename(columns={"close": "Close"})
    if "Close" not in df.columns:
        raise ValueError("ستون Close در فایل یافت نشد.")
    # ستون‌های غایب را از Close بساز تا feed بک‌تریدر خطا ندهد
    for col in ["Open", "High", "Low", "Volume"]:
        if col not in df.columns:
            df[col] = df["Close"] if col != "Volume" else 0
    return _normalize(df)


class LiveFeed:
    """
    فید زنده ساده‌شده برای پیاده‌سازی روی داده لایو:
    - در حالت واقعی، حلقه get() را به وب‌سوکت صرافی/کارگزار وصل کنید (ccxt, broker API)
    - اینجا برای نمایش معماری، آخرین قیمت را با نوسان تصادفی تولید می‌کند.
    نحوه استفاده: هر فراخوانی next_bar → یک بار جدید برای تزریق به cerebro.
    """

    def __init__(self, base_price=100.0, volatility=0.01, name="LIVE"):
        self.base_price = base_price
        self.volatility = volatility
        self.name = name

    def next_bar(self):
        import random
        change = random.gauss(0, self.volatility)
        self.base_price = max(0.01, self.base_price * (1 + change))
        o = self.base_price
        h = o * (1 + abs(random.gauss(0, self.volatility / 2)))
        l = o * (1 - abs(random.gauss(0, self.volatility / 2)))
        v = int(abs(random.gauss(1_000_000, 200_000)))
        return dict(time=dt.datetime.now(), open=o, high=h, low=l,
                    close=self.base_price, volume=v)

    def stream(self, n_bars=100, delay=0.5):
        for _ in range(n_bars):
            yield self.next_bar()
            time.sleep(delay)

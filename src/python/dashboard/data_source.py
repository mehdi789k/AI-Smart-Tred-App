# -*- coding: utf-8 -*-
"""
ماژول داده‌ها:
- دانلود داده تاریخی (yfinance) + کش محلی روی دیسک
- آپلود فایل CSV توسط کاربر
- شبیه‌سازی داده زنده (Live Feed) برای تست استراتژی در حالت Paper Trading
"""
import glob
import os
import time
import datetime as dt

import pandas as pd

try:  # yfinance اختیاری است؛ بدون آن هم داده MT5/CSV کار می‌کند.
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]

# کش CSV خروجی ابزار استخراج داده MT5 (src/python/data/main.py):
# market_data/historical/<symbol-hash>/<SYMBOL>_<TF>_<ts>_<uuid>.csv
MT5_EXPORT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "market_data", "historical")
)

# نگاشت بازبازه‌های MT5 به اینتروال‌های yfinance (برای دانلود مستقیم)
_MT5_TO_YF_INTERVAL = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "1h", "D1": "1d", "W1": "1wk", "MN1": "1mo",
}


def list_mt5_exports(symbol=None, timeframe=None):
    """فهرست فایل‌های CSV تولیدشده توسط استخراج MT5 در market_data/historical."""
    if not os.path.isdir(MT5_EXPORT_DIR):
        return []
    pattern = "*.csv"
    if symbol:
        pattern = f"*{symbol.upper()}*_{(timeframe or '*')}*.csv" if timeframe else f"*{symbol.upper()}*.csv"
    found = []
    for sym_dir in sorted(os.listdir(MT5_EXPORT_DIR)):
        d = os.path.join(MT5_EXPORT_DIR, sym_dir)
        if os.path.isdir(d) and sym_dir != "runs":
            found.extend(sorted(glob.glob(os.path.join(d, pattern))))
    return found


def load_mt5_csv(path):
    """خواندن یک CSV خروجی استخراج MT5 و تبدیل به DataFrame با ایندکس زمانی."""
    df = pd.read_csv(path, parse_dates=["time"]) if "time" in pd.read_csv(path, nrows=0).columns else None
    if df is None:
        # قالب استاندارد استخراج: timestamp/open/high/low/close/volume...
        raw = pd.read_csv(path)
        ts_col = next((c for c in ("timestamp", "time", "date", "datetime") if c in raw.columns), raw.columns[0])
        df = raw.copy()
        df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="mixed")
        df = df.set_index(ts_col).tz_localize(None)
        ren = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        df = df.rename(columns=ren)
        keep = [c for c in OHLCV if c in df.columns]
        df = df[keep].apply(pd.to_numeric, errors="coerce").dropna()
        if "Volume" not in df.columns:
            df["Volume"] = 0
        return df
    return _normalize(df)


def mt5_frame(symbol, timeframe="D1"):
    """آخرین (تازه‌ترین) CSV استخراج‌شده برای نماد/بازبازه MT5 را بارگذاری می‌کند.

    اگر داده‌ای یافت نشد، None برمی‌گرداند.
    """
    files = list_mt5_exports(symbol, timeframe)
    if not files:
        return None
    newest = max(files, key=os.path.getmtime)
    try:
        df = load_mt5_csv(newest)
        return df if not df.empty else None
    except Exception:
        return None


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


def mt5_available_symbols():
    """نمادهای موجود در کش CSV خروجی استخراج MT5."""
    symbols = []
    for path in list_mt5_exports():
        name = os.path.basename(path)
        parts = name.split("_")
        # قالب: SYMBOL_TF_timestamp_uuid.csv
        if len(parts) >= 4:
            sym = "_".join(parts[:-3])
            if sym and sym not in symbols:
                symbols.append(sym)
    return sorted(symbols)


def mt5_symbol_timeframes(symbol):
    """بازبازهایی که برای یک نماد در کش MT5 موجودند."""
    tfs = []
    for path in list_mt5_exports(symbol):
        parts = os.path.basename(path).split("_")
        if len(parts) >= 4:
            tf = parts[-3]
            if tf not in tfs:
                tfs.append(tf)
    order = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]
    return sorted(tfs, key=lambda t: order.index(t) if t in order else 99)


def fetch_history(ticker, start, end, interval="1d", use_cache=True):
    """دانلود داده تاریخی با کش روی دیسک (برای اجرای آفلاین/سریع)."""
    if yf is None:
        raise RuntimeError("ماژول yfinance نصب نیست؛ از منبع «داده MT5» یا آپلود CSV استفاده کنید.")
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

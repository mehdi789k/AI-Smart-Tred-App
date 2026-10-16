# -*- coding: utf-8 -*-
"""
استراتژی ۷: Trend Sniper X — بازپیاده‌سازی پایتونی اندیکاتور MQL5 (product/181101)
چندبازه‌ای (MTF) + تأیید Stochastic + فیلتر ADX + سشن لندن/نیویورک
https://www.mql5.com/en/market/product/181101
منطق اندیکاتور در فایل trend_sniper.py (ریشه پروژه) است.
"""
import numpy as np
import pandas as pd
import backtrader as bt

from strategies.base import RiskManagedStrategy, BASE_PARAMS
from trend_sniper import compute_trend_sniper, mtf_direction, session_flags


class TrendSniperX(RiskManagedStrategy):
    """استراتژی روند-دوست بر پایه فلگ‌های BUY/SELL اندیکاتور Trend Sniper.

    منطق (مطابق مستند محصول):
      - فلگ BUY → ورود خرید، فقط اگر جهت تایم‌فریم بالاتر (MTF) مخالف نباشد
        و در صورت فعال بودن Session Filter، بار داخل سشن London/NY باشد.
      - فلگ SELL → خروج از پوزیشن (و شورت در صورت allow_short).
      - کندل‌های ضعیف/مبهم سیگنال نمی‌دهند (Weak Bar Detection).
    """
    params = BASE_PARAMS + (
        ("fast", 12),                # EMA سریع تشخیص روند
        ("slow", 26),                # EMA کند تشخیص روند
        ("lookback", 5),             # پنجره ابقای شرط (تشخیص بار ضعیف)
        ("confirm_bars", 2),         # تعداد بار متوالی برای تأیید تغییر روند
        ("adx_period", 14),
        ("adx_threshold", 20.0),     # حداقل قدرت روند (Trend Strength Monitor)
        ("stoch_k", 14),             # دوره استوکستیک (Stochastic Confirmation)
        ("require_adx", True),
        ("require_stoch", True),
        ("use_mtf", True),           # تحلیل چندبازه‌ای
        ("htf_timeframe", "1d"),     # تایم‌فریم بالاتر: 1h | 4h | 1d | 1wk
        ("session_filter", False),   # فقط سشن London/New York
        ("exit_on_reverse", True),   # خروج با فلگ مخالف
        ("strategy_name", "Trend-Sniper-X"),
    )

    def __init__(self):
        super().__init__()
        # کش سیگنال: در حالت runonce یک‌بار روی کل سری محاسبه و سپس فقط
        # «برش تا بار جاری» می‌خورد (سریع + بدون lookahead).
        self._sig_cache = None
        self._sig_index_map = {}
        self._full_frame = None
        self._full_sig = None

    def _build_frame(self):
        """ساخت OHLCV تا بار جاری، بدون lookahead.

        چرا این روش؟ در حالت runonce (پیش‌فرض بک‌تریدر) همه داده از قبل در
        `.array` خط‌ها بارگذاری شده است و اندیس‌های معمولی مثل `line(-i)`
        یا `len(self.data)` موقعیت واقعی بار جاری را نشان نمی‌دهند؛
        نتیجه‌اش خطای `float() ... not '_LineDelay'` بود.

        راه‌حل درست: شمارش معکوس از انتهای آرایه datetime — آخرین عنصری
        که با زمان بار جاری (`self.data.datetime.datetime(0)`) برابر است،
        دقیقاً همان بار قابل‌مشاهده است و هیچ داده آینده‌ای وارد نمی‌شود.
        (در حالت realtime هم همین منطق کار می‌کند.)
        """
        darr = self.data.datetime.array
        now = self.data.datetime.datetime(0)
        end = None
        for k in range(len(darr) - 1, -1, -1):
            d = darr[k]
            if d != d:          # NaN → هنوز پر نشده
                continue
            if bt.num2date(d) <= now:
                end = k
                break
        if end is None:
            return None
        cols = {}
        for name, line in (("Open", self.data.open), ("High", self.data.high),
                           ("Low", self.data.low), ("Close", self.data.close),
                           ("Volume", self.data.volume)):
            arr = np.asarray(line.array[:end + 1], dtype=float)
            if name == "Volume":
                arr = np.nan_to_num(arr, nan=0.0)
            cols[name] = arr
        times = [bt.num2date(d) for d in darr[:end + 1]]
        return pd.DataFrame(cols, index=pd.DatetimeIndex(times))

    def _refresh_signals(self):
        """محاسبه سیگنالها روی کل تاریخ تا بار جاری (بدون lookahead).

        بهینه‌سازی سرعت: در حالت runonce آرایه‌های خط داده از قبل کامل هستند،
        پس یک‌بار روی کل سری محاسبه می‌شود؛ در هر فراخوانی بعدی فقط برش
        «تا بار جاری» اعمال می‌گردد (O(1)) — بدون تکرار محاسبات و بدون
        نشت داده آینده.
        """
        core = dict(
            fast=self.p.fast, slow=self.p.slow, lookback=self.p.lookback,
            confirm_bars=self.p.confirm_bars,
            adx_period=self.p.adx_period, adx_threshold=self.p.adx_threshold,
            stoch_k=self.p.stoch_k, require_adx=self.p.require_adx,
            require_stoch=self.p.require_stoch,
        )
        if self._full_frame is not None and self._full_sig is not None:
            # مسیر سریع: برش کش قبلی تا بار جاری
            now = self.data.datetime.datetime(0)
            pos = self._full_frame.index.searchsorted(pd.Timestamp(now),
                                                      side="right")
            sig = self._full_sig.iloc[:pos]
        else:
            df = self._build_frame()
            if df is None or len(df) < 5:
                return
            sig = compute_trend_sniper(df, **core)
            try:
                darr = self.data.datetime.array
                full_times = [bt.num2date(d) for d in darr if d == d]
                full_df = pd.DataFrame(
                    {name: np.asarray(line.array[:len(full_times)], dtype=float)
                     for name, line in (("Open", self.data.open),
                                        ("High", self.data.high),
                                        ("Low", self.data.low),
                                        ("Close", self.data.close),
                                        ("Volume", self.data.volume))},
                    index=pd.DatetimeIndex(full_times))
                if len(full_df) > len(df):     # فقط در runonce داده کامل هست
                    full_sig = compute_trend_sniper(full_df, **core)
                    if self.p.use_mtf:
                        full_sig["htf_dir"] = mtf_direction(
                            full_df, self.p.htf_timeframe, **core).values
                    else:
                        full_sig["htf_dir"] = 0
                    if self.p.session_filter:
                        full_sig["in_session"] = session_flags(
                            full_df.index).values
                    else:
                        full_sig["in_session"] = True
                    self._full_frame, self._full_sig = full_df, full_sig
                    sig = full_sig.iloc[:len(sig)]
            except Exception:
                pass   # اگر داده کامل در دسترس نبود، همان محاسبه تدریجی معتبر است

        if self.p.use_mtf and "htf_dir" not in sig.columns:
            sig["htf_dir"] = mtf_direction(
                self._build_frame(), self.p.htf_timeframe, **core).values[:len(sig)] \
                if len(sig) >= 5 else 0
        elif "htf_dir" not in sig.columns:
            sig["htf_dir"] = 0
        if "in_session" not in sig.columns:
            if self.p.session_filter:
                sig["in_session"] = session_flags(sig.index).values
            else:
                sig["in_session"] = True
        self._sig_cache = sig
        self._sig_index_map = {t: i for i, t in enumerate(sig.index)}

    def prenext(self):
        # در دوره گرم شدن هم منحنی سرمایه ثبت شود
        self.equity_curve.append(self.broker.getvalue())
        self.bar_times.append(self.data.datetime.datetime(0))

    def next(self):
        # مسیر سریع: اگر کش کامل موجود است، فقط برش تا بار جاری (O(1))
        if self._full_sig is not None:
            now = self.data.datetime.datetime(0)
            pos = self._full_frame.index.searchsorted(pd.Timestamp(now),
                                                      side="right")
            if (self._sig_cache is None or
                    len(self._sig_cache) != pos or
                    self._sig_index_map.get(now) != pos - 1):
                sig = self._full_sig.iloc[:pos]
                self._sig_cache = sig
                self._sig_index_map = {t: i for i, t in enumerate(sig.index)}
        elif (self._sig_cache is None or
              self.data.datetime.datetime(0) not in self._sig_index_map):
            self._refresh_signals()
        super().next()

    def _current_signal_row(self):
        now = self.data.datetime.datetime(0)
        i = self._sig_index_map.get(now)
        if i is None or self._sig_cache is None:
            return None
        return self._sig_cache.iloc[i]

    def _entry_logic(self):
        row = self._current_signal_row()
        if row is None:
            return
        buy_ok = (row["buy"] == 1 and row["in_session"] and
                  row["dir"] >= 0 and row["htf_dir"] >= 0)
        sell_ok = (row["sell"] == 1 and row["in_session"] and
                   row["dir"] <= 0 and row["htf_dir"] <= 0)
        if self.position:
            if self.p.exit_on_reverse and sell_ok and self.position.size > 0:
                self.order = self.close()
            elif self.p.exit_on_reverse and buy_ok and self.position.size < 0:
                self.order = self.close()
            return
        if buy_ok:
            self._place_entry(+1)
        elif sell_ok and self.p.allow_short:
            self._place_entry(-1)


SPEC = {
    "key": "Trend-Sniper-X",
    "title": "Trend Sniper X (چندبازه‌ای MTF)",
    "desc": ("بازپیاده‌سازی پایتونی اندیکاتور MQL5 «Trend Sniper X» "
             "(mql5.com/en/market/product/181101): فلگ BUY/SELL روی تأیید تغییر روند، "
             "تشخیص کندل ضعیف، تأیید استوکستیک (CONFIRMED/PARTIAL)، فیلتر قدرت ADX، "
             "تحلیل تایم‌فریم بالاتر و فیلتر سشن London/New York."),
    "docs_url": "https://www.mql5.com/en/market/product/181101",
    "indicator_overlay": True,   # داشبورد نمودار کندلی رنگی + فلگ رسم می‌کند
    "fields": [
        {"key": "fast", "label": "دوره EMA سریع", "type": "int", "min": 2, "max": 100, "default": 12},
        {"key": "slow", "label": "دوره EMA کند", "type": "int", "min": 5, "max": 200, "default": 26},
        {"key": "lookback", "label": "پنجره تشخیص بار ضعیف (بار)", "type": "int", "min": 2, "max": 30, "default": 5},
        {"key": "confirm_bars", "label": "بارهای تأیید تغییر روند", "type": "int", "min": 1, "max": 10, "default": 2},
        {"key": "adx_period", "label": "دوره ADX", "type": "int", "min": 5, "max": 50, "default": 14},
        {"key": "adx_threshold", "label": "حداقل قدرت روند (ADX)", "type": "float", "min": 5.0, "max": 50.0, "step": 1.0, "default": 20.0},
        {"key": "stoch_k", "label": "دوره استوکستیک (K)", "type": "int", "min": 5, "max": 50, "default": 14},
        {"key": "require_adx", "label": "فعال‌سازی فیلتر ADX", "type": "bool", "default": True},
        {"key": "require_stoch", "label": "تأیید استوکستیک سیگنال", "type": "bool", "default": True},
        {"key": "use_mtf", "label": "تحلیل چندبازه‌ای (MTF)", "type": "bool", "default": True},
        {"key": "htf_timeframe", "label": "تایم‌فریم بالاتر (HTF)", "type": "select",
         "options": ["1h", "4h", "1d", "1wk"], "default": "1d"},
        {"key": "session_filter", "label": "فقط سشن London/New York", "type": "bool", "default": False},
        {"key": "exit_on_reverse", "label": "خروج با فلگ مخالف", "type": "bool", "default": True},
    ],
}


def get_strategy_class():
    return TrendSniperX

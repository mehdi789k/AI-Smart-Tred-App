# -*- coding: utf-8 -*-
"""
کتابخانه استراتژی‌ها — هر استراتژی:
  1. یک کلاس با پارامترهای مشخص (قابل ویرایش کاربر)
  2. یک SPEC در STRATEGY_SPECS که داشبورد بر اساس آن فرم تنظیمات می‌سازد

برای افزودن استراتژی جدید:
  - یک کلاس بسازید که متد _entry_logic را بازنویسی کند
  - یک SPEC به انتهای STRATEGY_SPECS اضافه کنید
  (هیچ تغییری در app.py لازم نیست — داشبورد خودکار آن را نمایش می‌دهد)
"""
import numpy as np
import pandas as pd
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS
from trend_sniper import compute_trend_sniper, mtf_direction, session_flags


# ======================================================================
# 1) تقاطع میانگین متحرک (SMA / EMA Crossover)
# ======================================================================
class MovingAverageCrossover(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("fast_period", 10),
        ("slow_period", 30),
        ("ma_type", "SMA"),   # SMA | EMA
        ("use_volume_filter", False),
        ("volume_factor", 1.5),
        ("strategy_name", "MA-Crossover"),
    )

    def __init__(self):
        super().__init__()
        ma = bt.indicators.EMA if self.p.ma_type == "EMA" else bt.indicators.SMA
        self.fast_ma = ma(period=self.p.fast_period)
        self.slow_ma = ma(period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.vol_avg = bt.indicators.SMA(self.data.volume, period=20)

    def _entry_logic(self):
        vol_ok = True
        if self.p.use_volume_filter:
            vol_ok = self.data.volume[0] > self.vol_avg[0] * self.p.volume_factor
        if self.crossover > 0 and vol_ok:
            self._place_entry(+1)
        elif self.crossover < 0:
            self._place_entry(-1)


# ======================================================================
# 2) RSI بازگشت به میانگین (Mean Reversion)
# ======================================================================
class RSIMeanReversion(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("rsi_period", 14),
        ("oversold", 30),
        ("overbought", 70),
        ("exit_level", 50),
        ("strategy_name", "RSI-MeanRev"),
    )

    def __init__(self):
        super().__init__()
        self.rsi = bt.indicators.RSI_Safe(self.data.close, period=self.p.rsi_period)

    def _entry_logic(self):
        if not self.position:
            if self.rsi[0] < self.p.oversold:
                self._place_entry(+1)
            elif self.p.allow_short and self.rsi[0] > self.p.overbought:
                self._place_entry(-1)
        else:
            # خروج وقتی RSI به سطح میانی رسید
            pos_size = self.position.size
            if pos_size > 0 and self.rsi[0] > self.p.exit_level:
                self.order = self.close()
            elif pos_size < 0 and self.rsi[0] < self.p.exit_level:
                self.order = self.close()


# ======================================================================
# 3) شکست کانال دونچیان (Breakout)
# ======================================================================
class DonchianBreakout(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("entry_period", 20),
        ("exit_period", 10),
        ("atr_mult", 2.0),      # فاصله حد ضرر بر حسب ATR
        ("strategy_name", "Donchian-Breakout"),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.entry_period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.entry_period)
        self.exit_low = bt.indicators.Lowest(self.data.low, period=self.p.exit_period)
        self.exit_high = bt.indicators.Highest(self.data.high, period=self.p.exit_period)
        self.atr = bt.indicators.ATR(period=14)

    def _entry_logic(self):
        if self.position:
            # خروج با کانال کوتاه‌مدت
            if self.position.size > 0 and self.data.close[0] < self.exit_low[-1]:
                self.order = self.close()
            elif self.position.size < 0 and self.data.close[0] > self.exit_high[-1]:
                self.order = self.close()
            return
        if self.data.close[0] > self.highest[-1]:
            self._place_entry(+1)
        elif self.p.allow_short and self.data.close[0] < self.lowest[-1]:
            self._place_entry(-1)


# ======================================================================
# 4) MACD + روند
# ======================================================================
class MacdTrend(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("macd_fast", 12),
        ("macd_slow", 26),
        ("macd_signal", 9),
        ("trend_period", 100),
        ("strategy_name", "MACD-Trend"),
    )

    def __init__(self):
        super().__init__()
        self.macd = bt.indicators.MACD(
            self.data.close,
            period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow,
            period_signal=self.p.macd_signal)
        self.cross = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)
        self.trend = bt.indicators.EMA(self.data.close, period=self.p.trend_period)

    def _entry_logic(self):
        uptrend = self.data.close[0] > self.trend[0]
        if self.cross > 0 and uptrend:
            self._place_entry(+1)
        elif self.cross < 0:
            self._place_entry(-1)


# ======================================================================
# 5) بولینگر بان
# ======================================================================
class BollingerReversion(RiskManagedStrategy):
    params = BASE_PARAMS + (
        ("bb_period", 20),
        ("bb_dev", 2.0),
        ("exit_at_middle", True),
        ("strategy_name", "Bollinger-Reversion"),
    )

    def __init__(self):
        super().__init__()
        self.bb = bt.indicators.BollingerBands(
            period=self.p.bb_period, devfactor=self.p.bb_dev)

    def _entry_logic(self):
        if not self.position:
            if self.data.close[0] < self.bb.lines.bot[0]:
                self._place_entry(+1)
            elif self.p.allow_short and self.data.close[0] > self.bb.lines.top[0]:
                self._place_entry(-1)
        elif self.p.exit_at_middle:
            mid = self.bb.lines.mid[0]
            if self.position.size > 0 and self.data.close[0] >= mid:
                self.order = self.close()
            elif self.position.size < 0 and self.data.close[0] <= mid:
                self.order = self.close()


# ======================================================================
# 6) استراتژی سفارشی کاربر (فرمول دلخواه روی ستون‌های df)
# ======================================================================
class CustomFormulaStrategy(RiskManagedStrategy):
    """
    کاربر با پایتون ساده شرط ورود/خروج می‌نویسد، مثلاً:
      entry: close > sma(20) and rsi(14) < 40
      exit : rsi(14) > 60
    توابع آماده: sma(n), ema(n), rsi(n), macd(), atr(n), bb(n), roc(n), volume_sma(n)
    """
    params = BASE_PARAMS + (
        ("entry_formula", "close > sma(20)"),
        ("exit_formula", "close < sma(20)"),
        ("strategy_name", "Custom-Formula"),
    )

    def __init__(self):
        super().__init__()
        self._cache = {}
        # اندیکاتورهای فرمول را از قبل می‌سازیم تا خطای "min period" ندهند؛
        # خود backtrader تا گرم شدن (warm-up) فراخوانی next را به تعویق می‌اندازد.
        self._prebuild(self.p.entry_formula)
        self._prebuild(self.p.exit_formula)

    def _prebuild(self, formula):
        """استخراج sma(n)/ema(n)/rsi(n)/atr(n)/roc(n) از متن فرمول و ساخت کشیده.
        فقط ساخت اندیکاتور کافی است؛ مقداردهی در زمان اجرا انجام می‌شود
        (backtrader تا گرم شدنِ همه خطوط، next را صدا نمی‌زند)."""
        import re
        for fn in ("sma", "ema", "rsi", "atr", "roc"):
            for m in re.finditer(rf"\b{fn}\s*\(\s*(\d+)\s*\)", formula or ""):
                n = int(m.group(1))
                key = f"{fn}{n}"
                if key not in self._cache:
                    self._cache[key] = self._make_indicator(fn, n)

    def _make_indicator(self, fn, n):
        if fn == "sma":
            return bt.indicators.SMA(self.data.close, period=n)
        if fn == "ema":
            return bt.indicators.EMA(self.data.close, period=n)
        if fn == "rsi":
            return bt.indicators.RSI_Safe(self.data.close, period=n)
        if fn == "atr":
            return bt.indicators.ATR(period=n)
        if fn == "roc":
            return bt.indicators.ROC(self.data.close, period=n)
        raise ValueError(fn)

    # ---- توابع اندیکاتور برای محیط eval ----
    def _sma(self, n):
        key = f"sma{n}"
        if key not in self._cache:
            self._cache[key] = self._make_indicator("sma", int(n))
        return self._cache[key][0]

    def _ema(self, n):
        key = f"ema{n}"
        if key not in self._cache:
            self._cache[key] = self._make_indicator("ema", int(n))
        return self._cache[key][0]

    def _rsi(self, n=14):
        key = f"rsi{n}"
        if key not in self._cache:
            self._cache[key] = self._make_indicator("rsi", int(n))
        return self._cache[key][0]

    def _atr(self, n=14):
        key = f"atr{n}"
        if key not in self._cache:
            self._cache[key] = self._make_indicator("atr", int(n))
        return self._cache[key][0]

    def _roc(self, n=10):
        key = f"roc{n}"
        if key not in self._cache:
            self._cache[key] = self._make_indicator("roc", int(n))
        return self._cache[key][0]

    def _vol(self):
        return self.data.volume[0]

    def _env(self):
        return dict(
            close=self.data.close[0], open=self.data.open[0],
            high=self.data.high[0], low=self.data.low[0],
            volume=self.data.volume[0],
            sma=self._sma, ema=self._ema, rsi=self._rsi,
            atr=self._atr, roc=self._roc,
            abs=abs, min=min, max=max,
        )

    def _eval(self, formula):
        try:
            return bool(eval(formula, {"__builtins__": {}}, self._env()))
        except Exception as e:
            # یک‌بار خطا را چاپ کن تا کاربر فرمول غلط را متوجه شود (تکرار نمی‌شود)
            if not getattr(self, "_formula_warned", False):
                print(f"[Custom-Formula] خطا در فرمول '{formula}': {e}")
                self._formula_warned = True
            return False

    def _entry_logic(self):
        if not self.position:
            if self._eval(self.p.entry_formula):
                self._place_entry(+1)
        else:
            if self._eval(self.p.exit_formula):
                self.order = self.close()


# ======================================================================
# 7) Trend Sniper X — بازپیاده‌سازی پایتونی اندیکاتور MQL5 (product/181101)
#    چندبازه‌ای (MTF) + تأیید Stochastic + فیلتر ADX + سشن لندن/نیویورک
#    https://www.mql5.com/en/market/product/181101
# ======================================================================
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
        ("lookback", 5),            # پنجره ابقای شرط (تشخیص بار ضعیف)
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


# ======================================================================
# REGISTER — منبع واحد حقیقت برای داشبورد
# ======================================================================
STRATEGY_SPECS = {
    "MA-Crossover": {
        "class": MovingAverageCrossover,
        "title": "تقاطع میانگین متحرک",
        "desc": "خرید هنگام قطع MA سریع به بالا؛ فروش/خروج هنگام قطع به پایین.",
        "fields": [
            {"key": "fast_period", "label": "دوره MA سریع", "type": "int", "min": 2, "max": 120, "default": 10},
            {"key": "slow_period", "label": "دوره MA کند", "type": "int", "min": 5, "max": 300, "default": 30},
            {"key": "ma_type", "label": "نوع میانگین", "type": "select", "options": ["SMA", "EMA"], "default": "SMA"},
            {"key": "use_volume_filter", "label": "فیلتر حجم معاملات", "type": "bool", "default": False},
            {"key": "volume_factor", "label": "ضریب حجم (نسبت به میانگین)", "type": "float", "min": 1.0, "max": 5.0, "step": 0.1, "default": 1.5},
        ],
    },
    "RSI-MeanRev": {
        "class": RSIMeanReversion,
        "title": "بازگشت به میانگین با RSI",
        "desc": "خرید در اشباع فروش و خروج نزدیک خط وسط؛ مناسب بازار رِنج.",
        "fields": [
            {"key": "rsi_period", "label": "دوره RSI", "type": "int", "min": 2, "max": 60, "default": 14},
            {"key": "oversold", "label": "آستانه اشباع فروش", "type": "int", "min": 5, "max": 45, "default": 30},
            {"key": "overbought", "label": "آستانه اشباع خرید", "type": "int", "min": 55, "max": 95, "default": 70},
            {"key": "exit_level", "label": "سطح خروج", "type": "int", "min": 40, "max": 60, "default": 50},
        ],
    },
    "Donchian-Breakout": {
        "class": DonchianBreakout,
        "title": "شکست کانال دونچیان",
        "desc": "ورود با شکست سقف N روز و خروج با کانال کوتاه‌تر (روند-دوست).",
        "fields": [
            {"key": "entry_period", "label": "دوره کانال ورود", "type": "int", "min": 5, "max": 120, "default": 20},
            {"key": "exit_period", "label": "دوره کانال خروج", "type": "int", "min": 2, "max": 60, "default": 10},
            {"key": "atr_mult", "label": "ضریب ATR برای حد ضرر", "type": "float", "min": 0.5, "max": 6.0, "step": 0.1, "default": 2.0},
        ],
    },
    "MACD-Trend": {
        "class": MacdTrend,
        "title": "MACD هم‌جه با روند",
        "desc": "تنها وقتی وارد شو که MACD سیگنال داد و قیمت بالای EMA روند باشد.",
        "fields": [
            {"key": "macd_fast", "label": "MACD سریع", "type": "int", "min": 2, "max": 50, "default": 12},
            {"key": "macd_slow", "label": "MACD کند", "type": "int", "min": 5, "max": 100, "default": 26},
            {"key": "macd_signal", "label": "دوره سیگنال", "type": "int", "min": 2, "max": 30, "default": 9},
            {"key": "trend_period", "label": "دوره EMA روند", "type": "int", "min": 20, "max": 300, "default": 100},
        ],
    },
    "Bollinger-Reversion": {
        "class": BollingerReversion,
        "title": "نوارهای بولینگر",
        "desc": "خرید با برخورد به بان پایین و خروج در خط وسط.",
        "fields": [
            {"key": "bb_period", "label": "دوره بولینگر", "type": "int", "min": 5, "max": 100, "default": 20},
            {"key": "bb_dev", "label": "انحراف معیار", "type": "float", "min": 0.5, "max": 4.0, "step": 0.1, "default": 2.0},
            {"key": "exit_at_middle", "label": "خروج در خط وسط", "type": "bool", "default": True},
        ],
    },
    "Custom-Formula": {
        "class": CustomFormulaStrategy,
        "title": "فرمول سفارشی کاربر",
        "desc": ("شرط ورود/خروج را بنویسید. متغیرها: close open high low volume — "
                 "توابع: sma(n) ema(n) rsi(n) atr(n) roc(n)"),
        "fields": [
            {"key": "entry_formula", "label": "فرمول ورود", "type": "text",
             "default": "close > sma(20) and rsi(14) < 45"},
            {"key": "exit_formula", "label": "فرمول خروج", "type": "text",
             "default": "close < sma(20)"},
        ],
    },
    "Trend-Sniper-X": {
        "class": TrendSniperX,
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
    },
}

# پارامترهای مشترک مدیریت ریسک (همه استراتژی‌ها از base ارث می‌برند)
RISK_FIELDS = [
    {"key": "risk_per_trade", "label": "ریسک هر معامله (٪ سرمایه)", "type": "float",
     "min": 0.001, "max": 0.5, "step": 0.005, "default": 0.02},
    {"key": "stop_loss_pct", "label": "حد ضرر (٪) — صفر = غیرفعال", "type": "float",
     "min": 0.0, "max": 0.5, "step": 0.005, "default": 0.05},
    {"key": "take_profit_pct", "label": "حد سود (٪) — صفر = غیرفعال", "type": "float",
     "min": 0.0, "max": 2.0, "step": 0.01, "default": 0.15},
    {"key": "trailing_stop_pct", "label": "حد ضرر متحرک (٪) — صفر = غیرفعال", "type": "float",
     "min": 0.0, "max": 0.5, "step": 0.005, "default": 0.0},
    {"key": "allow_short", "label": "اجازه فروش تعهدی (Short)", "type": "bool", "default": False},
]


def build_params(spec_key, user_values):
    """ترکیب مقادیر کاربر + نام استراتژی برای ارسال به cerebro."""
    params = dict(user_values)
    params["strategy_name"] = spec_key
    return params

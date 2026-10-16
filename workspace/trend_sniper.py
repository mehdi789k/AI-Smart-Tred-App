# -*- coding: utf-8 -*-
"""
Trend Sniper X — پیاده‌سازی پایتونی اندیکاتور ترند اسنایپر برای MetaTrader 5
=============================================================================
منبع ایده/نام: https://www.mql5.com/en/market/product/181101
(«Trend Sniper X is a multi-timeframe trend-following indicator for
MetaTrader 5 that helps traders identify trend direction and potential
reversal points» — نویسنده: Sarvarbek Abduvoxobov)

نکته مهم: سورس MQL5 این محصول تجاری و بسته است؛ آنچه اینجا ساخته شده
بازپیاده‌سازی رفتار مستندشده اندیکاتور با پایتون است تا در موتور بک‌ترید
و سیگنال‌دهی زنده پروژه قابل استفاده باشد:

  1) تحلیل چندبازه‌ای (Multi-Timeframe / MTF):
     جهت روند تایم‌فریم بالاتر روی نمودار فعلی پروژه می‌شود.
  2) تشخیص کندل‌های ضعیف/بلاتکلیف (Weak Bar Detection):
     وقتی شرط روند فقط نصف پنجره بررسی را پوشش دهد، آن بار «ضعیف» علامت
     می‌خورد (ناحیه رِنج = ورود نکن).
  3) فلگ BUY/SELL:
     پس از تأیید تغییر روند، فلگ روی بارِ بعد از تأیید نصب می‌شود.
  4) تأیید استوکستیک (v2.2): سه حالت CONFIRMED / PARTIAL / NOT CONFIRMED
  5) پایش قدرت روند با ADX (v2.2): فیلتر شرایط رِنج
  6) جعبه سشن‌های معاملاتی (Session Box): Sydney/Tokyo/London/New York
     طبق توضیح محصول: بهترین سیگنال‌ها در سشن لندن و نیویورک.

خروجی compute_trend_sniper یک DataFrame هم‌اندازه داده تایم‌فریم پایین با
ستون‌های:
  dir        : جهت روند (+1 صعودی / -1 نزولی / 0 بی‌روند)
  state      : رنگ کندل: STRONG_UP | WEAK_UP | UP_CHANGE | DOWN_CHANGE |
               WEAK_DOWN | STRONG_DOWN
  buy/sell   : فلگ تأییدشده (1/0)
  stoch_conf : تأیید استوکستیک: CONFIRMED | PARTIAL | NOT_CONFIRMED
  adx        : قدرت روند
  in_session : آیا بار داخل سشن London/NY (با احتساب option) است
"""
import datetime as dt

import numpy as np
import pandas as pd


# ======================================================================
# اندیکاتورهای پایه (معادل iADX / iStochastic در MQL5)
# ======================================================================
def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """هموارسازی Wilder (روش رسمی ADX ویلدر)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.DataFrame:
    """محاسبه ADX/+DI/-DI به سبک MetaTrader (Wilder)."""
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = wilder_smooth(tr, period)
    pdi_s = wilder_smooth(pd.Series(plus_dm, index=high.index), period)
    mdi_s = wilder_smooth(pd.Series(minus_dm, index=high.index), period)

    plus_di = 100.0 * pdi_s / atr.replace(0, np.nan)
    minus_di = 100.0 * mdi_s / atr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = wilder_smooth(dx.fillna(0.0), period)
    return pd.DataFrame({"adx": adx_, "+di": plus_di, "-di": minus_di})


def stochastic_k(high: pd.Series, low: pd.Series, close: pd.Series,
                 k_period: int = 14, smooth_k: int = 3,
                 d_period: int = 3) -> pd.DataFrame:
    """استوکستیک کلاسیک (Slow) با خطوط %K و %D."""
    ll = low.rolling(k_period, min_periods=1).min()
    hh = high.rolling(k_period, min_periods=1).max()
    raw = 100.0 * (close - ll) / (hh - ll).replace(0, np.nan)
    k = raw.rolling(smooth_k, min_periods=1).mean()
    d = k.rolling(d_period, min_periods=1).mean()
    return pd.DataFrame({"k": k, "d": d})


# ======================================================================
# منطق اصلی اندیکاتور روی یک تایم‌فریم
# ======================================================================
def _trend_core(df: pd.DataFrame, fast: int, slow: int):
    """قلب تشخیص روند: تقاطع EMA.

    bull_raw[i] یعنی در بار i قیمت بالای EMA سریع و EMA سریع بالای EMA کند است.
    (تأیید چندبارِ ابقا در compute_trend_sniper با پنجره confirm انجام می‌شود.)
    """
    ema_f = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["Close"].ewm(span=slow, adjust=False).mean()
    bull_raw = ((df["Close"] > ema_f) & (ema_f > ema_s)).to_numpy()
    bear_raw = ((df["Close"] < ema_f) & (ema_f < ema_s)).to_numpy()
    return bull_raw, bear_raw, ema_f, ema_s


def compute_trend_sniper(df: pd.DataFrame,
                         fast: int = 12,
                         slow: int = 26,
                         lookback: int = 5,
                         confirm_bars: int = 2,
                         adx_period: int = 14,
                         adx_threshold: float = 20.0,
                         stoch_k: int = 14,
                         stoch_d: int = 3,
                         require_adx: bool = True,
                         require_stoch: bool = True) -> pd.DataFrame:
    """محاسبه کامل سیگنال‌های Trend Sniper روی یک DataFrame OHLCV.

    پارامترها همه از داشبورد قابل تغییرند (دسترسی کامل کاربر).
    """
    n = len(df)
    bull_raw, bear_raw, ema_f, ema_s = _trend_core(df, fast, slow)

    # --- شمارش شرط روند در پنجره lookback (تشخیص بار ضعیف) ---
    bull_cnt = pd.Series(bull_raw.astype(float)).rolling(
        lookback, min_periods=1).sum().to_numpy()
    bear_cnt = pd.Series(bear_raw.astype(float)).rolling(
        lookback, min_periods=1).sum().to_numpy()

    half = max(1, lookback // 2)
    # جهت غالب: اگر بیشتر پنجره صعودی بود => صعودی
    direction = np.zeros(n, dtype=int)
    direction[bull_cnt >= half] = 1
    direction[(bear_cnt >= half) & (bull_cnt < half)] = -1
    # بار ضعیف: شرط روند تنها نیمی از پنجره را دارد (منطقه مبهم)
    weak_up = bull_raw & (bull_cnt < lookback)
    weak_down = bear_raw & (bear_cnt < lookback)

    # --- «بار تغییر»: آخرین باری که شرط روند قبلی شکسته شد ---
    change_up = (~bull_raw) & np.roll(bull_raw, 1)
    change_up[0] = False
    change_dn = (~bear_raw) & np.roll(bear_raw, 1)
    change_dn[0] = False

    # --- تأیید ابقا: شرط روند حداقل confirm_bars بار متوالی برقرار باشد ---
    if confirm_bars <= 1:
        bull_confirm = bull_raw.copy()
        bear_confirm = bear_raw.copy()
    else:
        bull_confirm = pd.Series(bull_raw.astype(float)).rolling(
            confirm_bars, min_periods=confirm_bars).min().to_numpy() > 0.5
        bear_confirm = pd.Series(bear_raw.astype(float)).rolling(
            confirm_bars, min_periods=confirm_bars).min().to_numpy() > 0.5

    # --- فلگ BUY/SELL: روی بارِ «بعد از» تأیید (مطابق مستند محصول) ---
    # شرط روند باید حداقل confirm_bars بار متوالی برقرار بماند؛ سپس فلگ روی
    # همان بارِ تکمیل تأیید نصب می‌شود (یعنی بار بعد از تغییر + تأیید).
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    last_dir = 0       # آخرین جهت fully-flagged شده
    seen_dir = 0       # جهت جاری (حتی اگر هنوز تأیید نشده)
    for i in range(1, n):
        cur_dir = 1 if bull_raw[i] else (-1 if bear_raw[i] else 0)
        if cur_dir != 0:
            seen_dir = cur_dir
        if seen_dir == 1 and last_dir != 1 and bull_confirm[i]:
            buy[i] = True          # ورود صعودی پس از تأیید ابقا
            last_dir = 1
        elif seen_dir == -1 and last_dir != -1 and bear_confirm[i]:
            sell[i] = True         # ورود نزولی پس از تأیید ابقا
            last_dir = -1
    # جهت نهایی = جهت روند جاری (ffill روی آخرین جهت معتبر)
    dir_series = pd.Series(bull_raw.astype(int) - bear_raw.astype(int),
                           index=df.index).replace(0, np.nan).ffill().fillna(0)

    # --- ADX (پایش قدرت روند v2.2) ---
    adx_df = adx(df["High"], df["Low"], df["Close"], adx_period)
    adx_ok = (adx_df["adx"] >= adx_threshold).to_numpy()

    # --- تأیید استوکستیک (v2.2) ---
    st_df = stochastic_k(df["High"], df["Low"], df["Close"], stoch_k, 3, stoch_d)
    k_, d_ = st_df["k"].to_numpy(), st_df["d"].to_numpy()
    stoch_bull = (k_ > d_) & (k_ < 80)          # مومنتوم صعودی سالم
    stoch_bear = (k_ < d_) & (k_ > 20)          # مومنتوم نزولی سالم

    # --- ترکیب فیلترها روی فلگ‌ها (رفتار v2.2) ---
    # در این حالت «فلگ BUY/SELL» همان تغییر جهتِ تأییدشده است و ADX/Stochastic
    # نقش لایه‌های تأیید را دارند: سیگنال ضعیف‌تر حذف یا PARTIAL می‌شود.
    if require_adx and require_stoch:
        # هر دو فیلتر لازم‌اند؛ تأیید کامل = stoch هم‌جهت، در غیر صورت PARTIAL
        # (برای اینکه سیگنال‌ها بیش از حد کمیاب نشوند، آستانه ADX روی بارهای
        #  تغییر با tolerance نرم‌تری بررسی می‌شود: adx >= threshold*0.85)
        soft_adx_ok = adx_ok | ((adx_df["adx"] >= adx_threshold * 0.85).to_numpy()
                                & np.roll(adx_ok, 1))
        buy &= soft_adx_ok
        sell &= soft_adx_ok
        conf_buy = buy & stoch_bull
        part_buy = buy & ~stoch_bull
        conf_sell = sell & stoch_bear
        part_sell = sell & ~stoch_bear
        keep = conf_buy | part_buy | conf_sell | part_sell
        stoch_state = np.array(["NOT_CONFIRMED"] * n, dtype=object)
        stoch_state[conf_buy | conf_sell] = "CONFIRMED"
        stoch_state[part_buy | part_sell] = "PARTIAL"
        # فلگ‌ها حفظ می‌شوند ولی برچسب تأیید روی آن‌هاست (مثل داشبورد محصول)
    elif require_adx:
        buy &= adx_ok
        sell &= adx_ok
        stoch_state = np.array(["CONFIRMED"] * n, dtype=object)
    elif require_stoch:
        conf_buy = buy & stoch_bull
        conf_sell = sell & stoch_bear
        part_buy = buy & ~stoch_bull
        part_sell = sell & ~stoch_bear
        stoch_state = np.array(["NOT_CONFIRMED"] * n, dtype=object)
        stoch_state[conf_buy | conf_sell] = "CONFIRMED"
        stoch_state[part_buy | part_sell] = "PARTIAL"
        # فقط سیگنال‌های تأییدشده فلگ می‌مانند
        buy = conf_buy
        sell = conf_sell
    else:
        stoch_state = np.array(["CONFIRMED"] * n, dtype=object)

    # --- رنگ وضعیت کندل (Colored Trend Candles) ---
    state = np.array(["WEAK_UP"] * n, dtype=object)
    strong_up = bull_raw & ~weak_up
    strong_dn = bear_raw & ~weak_down
    state[np.zeros(n, dtype=bool)] = ""
    state = np.where(strong_up, "STRONG_UP",
            np.where(weak_up, "WEAK_UP",
            np.where(change_dn, "UP_CHANGE",           # روند صعودی در حال تغییر
            np.where(change_up, "DOWN_CHANGE",         # روند نزولی در حال تغییر
            np.where(strong_dn, "STRONG_DOWN",
            np.where(weak_down, "WEAK_DOWN", "NEUTRAL"))))))

    out = pd.DataFrame({
        "dir": dir_series.astype(int).to_numpy(),
        "state": state,
        "buy": buy.astype(int),
        "sell": sell.astype(int),
        "stoch_conf": stoch_state,
        "adx": adx_df["adx"].to_numpy(),
        "ema_fast": ema_f.to_numpy(),
        "ema_slow": ema_s.to_numpy(),
        "stoch_k": st_df["k"].to_numpy(),
        "stoch_d": st_df["d"].to_numpy(),
    }, index=df.index)
    return out


# ======================================================================
# تحلیل چندبازه‌ای (MTF): پروژه جهت HTF روی تایم‌فریم پایین
# ======================================================================
_HTF_RULES = {
    "1h": "1h", "2h": "2h", "3h": "3h", "4h": "4h",
    "1d": "1D", "1wk": "1W", "1M": "ME",
}


def mtf_direction(low_df: pd.DataFrame, htf_timeframe: str, **core_kwargs) -> pd.Series:
    """جهت روند تایم‌فریم بالاتر را محاسبه و روی بارهای تایم پایین نگاشت می‌کند.

    دقیقاً مثل قابلیت «Higher Timeframe (M1–MN1)» در Trend Sniper X:
    تحلیل روی HTF انجام می‌شود ولی نتیجه روی چارت فعلی نمایش داده می‌شود.
    بدون lookahead: مقدار HTF فقط پس از بسته شدن بار HTF قابل استفاده است
    (shift(1) قبل از reindex.ffill).
    """
    rule = _HTF_RULES.get(htf_timeframe.lower(), htf_timeframe.upper())
    try:
        htf = low_df.resample(rule).agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna(subset=["Close"])
    except ValueError:
        # ایندکس مرتب نیست
        tmp = low_df.sort_index()
        htf = tmp.resample(rule).agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna(subset=["Close"])
    sig = compute_trend_sniper(htf, **core_kwargs)
    htf_dir = sig["dir"].shift(1)  # فقط بار بسته‌شده HTF مجاز است
    return htf_dir.reindex(low_df.index, method="ffill").fillna(0).astype(int)


# ======================================================================
# جعبه سشن‌های بازار (Session Market Box)
# ======================================================================
# ساعت‌بندی UTC تقریبی مطابق رایج MT5 (قابل ویرایش از داشبورد)
SESSIONS = {
    "Sydney":     ((21, 0), (6, 0)),
    "Tokyo":      ((0, 0),  (9, 0)),
    "London":     ((8, 0),  (17, 0)),
    "New York":   ((13, 0), (22, 0)),
}


def session_flags(index: pd.DatetimeIndex,
                  sessions=("London", "New York")) -> pd.Series:
    """True اگر ساعت هر بار (UTC) داخل یکی از سشن‌های انتخابی باشد."""
    hours = index.hour.to_numpy()
    flags = np.zeros(len(index), dtype=bool)
    for name in sessions:
        (sh, sm), (eh, em) = SESSIONS[name]
        start = sh + sm / 60.0
        end = eh + em / 60.0
        h = hours.astype(float)
        if start <= end:
            inside = (h >= start) & (h < end)
        else:  # سشن شبانه که نیمه‌شب را رد می‌کند (Sidney)
            inside = (h >= start) | (h < end)
        flags |= inside
    return pd.Series(flags, index=index)

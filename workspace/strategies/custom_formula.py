# -*- coding: utf-8 -*-
"""استراتژی ۶: فرمول سفارشی کاربر (شرط ورود/خروج به زبان پایتون ساده)."""
import re

import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS


class CustomFormulaStrategy(RiskManagedStrategy):
    """
    کاربر با پایتون ساده شرط ورود/خروج می‌نویسد، مثلاً:
      entry: close > sma(20) and rsi(14) < 40
      exit : rsi(14) > 60
    توابع آماده: sma(n), ema(n), rsi(n), atr(n), roc(n)
    متغیرها: close open high low volume
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
        """استخراج sma(n)/ema(n)/rsi(n)/atr(n)/roc(n) از متن فرمول و ساخت کشیده."""
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


SPEC = {
    "key": "Custom-Formula",
    "title": "فرمول سفارشی کاربر",
    "desc": ("شرط ورود/خروج را بنویسید. متغیرها: close open high low volume — "
             "توابع: sma(n) ema(n) rsi(n) atr(n) roc(n)"),
    "fields": [
        {"key": "entry_formula", "label": "فرمول ورود", "type": "text",
         "default": "close > sma(20) and rsi(14) < 45"},
        {"key": "exit_formula", "label": "فرمول خروج", "type": "text",
         "default": "close < sma(20)"},
    ],
}


def get_strategy_class():
    return CustomFormulaStrategy

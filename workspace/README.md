# 📈 داشبورد بک‌تست استراتژی — معماری پلاگینی (یک فایل = یک استراتژی)

## پوشه strategies/
هر استراتژی یک فایل `*.py` مستقل در `strategies/` است:

| فایل | استراتژی |
|---|---|
| `base.py` | کلاس پایه `RiskManagedStrategy` + سیستم پلاگین (اسکن/آپلود/ویرایش/حذف) |
| `ma_crossover.py` | تقاطع میانگین متحرک (SMA/EMA + فیلتر حجم) |
| `rsi_meanrev.py` | بازگشت به میانگین با RSI |
| `donchian_breakout.py` | شکست کانال دونچیان |
| `macd_trend.py` | MACD هم‌جه با روند |
| `bollinger_reversion.py` | نوارهای بولینگر |
| `custom_formula.py` | فرمول سفارشی کاربر |
| `trend_sniper_x.py` | Trend Sniper X (بازپیاده‌سازی اندیکاتور MQL5، چندبازه‌ای MTF) |
| `_archive/` | بایگانی حذف‌های نرم و نسخه قدیمی `library.py` |

## ساختار اجباری هر فایل استراتژی
```python
import backtrader as bt
from strategies.base import RiskManagedStrategy, BASE_PARAMS

class MyStrategy(RiskManagedStrategy):
    params = BASE_PARAMS + (("fast_period", 10), ("strategy_name", "My-Strat"))
    def __init__(self):
        super().__init__()
        # اندیکاتورها...
    def _entry_logic(self):
        # self._place_entry(+1) ورود خرید | self._place_entry(-1) فروش/شورت
        # self.order = self.close() خروج   |   self.position پوزیشن باز

SPEC = {"key": "My-Strat", "title": "...", "desc": "...",
        "fields": [{"key": "fast_period", "label": "...", "type": "int",
                    "min": 2, "max": 120, "default": 10}]}

def get_strategy_class():
    return MyStrategy
```
فرم تنظیمات داشبورد به‌صورت خودکار از `SPEC["fields"]` ساخته می‌شود.
انواع فیلد: `int | float | bool | select | text`.

## آپلود و ویرایش از داشبورد (تب «📁 مدیریت فایل استراتژی‌ها»)
- **آپلود**: فایل `.py` را انتخاب و در `strategies/` ذخیره کنید.
- **قالب جدید**: ساخت فایل از الگوی آماده + دانلود قالب.
- **ویرایش**: نمایش/ویرایش سورس هر استراتژی و ذخیره (hot-reload بدون ری‌استارت).
- **حذف**: انتقال نرم به `strategies/_archive/`.
- اعتبارسنجی خودکار قبل از ذخیره: سینتکس پایتون، ممنوعیت `exec/eval/open/import شبکه`،
  وجود کلاس فرزندِ `RiskManagedStrategy`، `SPEC` و `get_strategy_class()`.

فایل‌های تازه/ویرایش‌شده بلافاصله در تب‌های بک‌تست، بهینه‌سازی و سیگنال زنده ظاهر می‌شوند.

## اجرا
```bash
streamlit run app.py
python3 test_engine.py     # تست خودکار موتور + همه استراتژی‌ها
```

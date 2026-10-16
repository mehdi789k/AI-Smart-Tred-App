# AI-Smart-Tred-App 🤖📈

اپلیکیشن معاملاتی هوشمند مبتنی بر **MetaTrader 5** — شامل جمع‌آوری خودکار داده‌های تاریخی بازار، داشبورد بک‌تست و بهینه‌سازی استراتژی (Streamlit + Backtrader)، و زیرساخت استریم داده بلادرنگ.

> ⚠️ **سلب مسئولیت:** این پروژه صرفاً برای اهداف آموزشی و پژوهشی است و توصیه مالی نیست. معاملات فارکس/رمزارز ریسک از دست دادن کل سرمایه را دارد. قبل از استفاده روی حساب واقعی، حتماً با حساب دمو تست کنید.

---

## ✨ ویژگی‌ها

- **اتصال به MetaTrader 5:** اتصال برنامه‌ای به ترمینال MT5 (ویندوز) با اعتبارسنجی از طریق متغیرهای محیطی یا فایل `.env`.
- **استخراج داده تاریخی افزایشی (Incremental):** دانلود کندل‌ها برای همه نمادهای Market Watch در ۹ تایم‌فریم (`M1, M5, M15, M30, H1, H4, D1, W1, MN1`) و ذخیره در CSV با ادغام هوشمند رکوردهای جدید/به‌روزشده بدون تکرار.
- **داشبورد مدیریت استراتژی:** رابط گرافیکی Streamlit برای انتخاب استراتژی، بارگذاری داده (MT5 زنده یا CSV)، اجرای بک‌تست، بهینه‌سازی پارامترها و مشاهده گزارش‌ها.
- **معماری «یک فایل، یک استراتژی»:** هر فایل `.py` در پوشه `strategies/` یک استراتژی مستقل است؛ داشبورد به‌صورت خودکار آن‌ها را اسکن می‌کند — افزودن استراتژی جدید یعنی گذاشتن یک فایل.
- **مدیریت ریسک داخلی استراتژی‌ها:** ریسک هر معامله (٪ سرمایه)، حد ضرر، حد سود، حد ضرر متحرک (Trailing Stop) و اجازه فروش تعهدی (Short).
- **استریم داده بلادرنگ:** انتشار آپدیت‌های بازار روی ZeroMQ (PUB/SUB) با fallback نرم‌افزاری وقتی `pyzmq` نصب نیست.
- **CI/CD:** سه Workflow گیت‌هاب اکشنز — تست و صحت‌سنجی پایتون (Ruff + MyPy strict + Pytest + Coverage)، تحلیل امنیتی CodeQL و بررسی وابستگی‌ها (Dependency Review).

---

## 🗂 ساختار پروژه

```text
AI-Smart-Tred-App/
├── src/python/
│   ├── data/                      # زیرسیستم جمع‌آوری داده MT5
│   │   ├── main.py                # CLI استخراج داده تاریخی (Market Watch → CSV)
│   │   ├── mt5_connector.py       # لایه اتصال به MetaTrader 5 API
│   │   ├── historical_loader.py   # منطق دانلود و تجمیع کندل‌ها
│   │   ├── config.py              # تایم‌فریم‌ها و خواندن تنظیمات (.env / متغیرهای محیطی)
│   │   ├── models.py              # مدل‌های داده (رکوردهای OHLCV)
│   │   ├── database.py            # لایه SQLAlchemy (اختیاری)
│   │   └── data_streamer.py       # استریم بلادرنگ روی ZeroMQ
│   └── dashboard/                 # داشبورد استراتژی (Streamlit)
│       ├── app.py                 # ورودی داشبورد
│       ├── engine.py              # موتور بک‌تست و بهینه‌ساز پارامتر (Backtrader)
│       ├── data_source.py         # منبع داده: MT5 زنده یا فایل CSV
│       ├── strategies/            # 🔌 هر فایل = یک استراتژی (auto-discovery)
│       │   ├── base.py            # کلاس پایه RiskManagedStrategy + SPEC + discover_strategies
│       │   ├── trend_sniper_x.py  # Trend Sniper X (EMA + ADX + Stochastic)
│       │   ├── ma_crossover.py    # تقاطع میانگین متحرک
│       │   ├── macd_trend.py      # روند MACD
│       │   ├── rsi_meanrev.py     # بازگشت به میانگین RSI
│       │   ├── bollinger_reversion.py  # بازگشت بولینگر
│       │   ├── donchian_breakout.py    # شکست دانچیان
│       │   └── custom_formula.py       # فرمول سفارشی کاربر
│       └── reports/               # تاریخچه اجراها (runs_history.jsonl)
├── scripts/
│   ├── launch_mt5.py              # پیدا کردن و اجرای ترمینال MT5 روی ویندوز
│   └── mt5_market_watch.py        # نمایش لیست Watchlist نمادها (جدول/JSON/Demo)
├── tests/python/                  # تست‌های pytest (data + dashboard)
├── market_data/historical/        # خروجی CSVها + خلاصه هر اجرا (runs/*/summary.json)
├── .github/workflows/             # CI: python-validation, codeql, dependency-review
├── .env.example                   # الگوی تنظیمات حساب MT5
└── README.md
```

---

## 🚀 شروع سریع

### پیش‌نیازها

| مورد | توضیح |
|------|-------|
| **OS** | ویندوز (برای اتصال مستقیم MT5) — سایر بخش‌ها cross-platform هستند |
| **Python** | 3.12 (نسخه‌ای که CI روی آن اجرا می‌شود) |
| **MetaTrader 5** | ترمینال نصب‌شده + حساب بروکر (حساب دمو پیشنهاد می‌شود) |
| **پکیج‌ها** | `MetaTrader5`, `streamlit`, `plotly`, `pandas`, `backtrader`, `numpy` و (اختیاری) `pyzmq`, `sqlalchemy` |

```bash
pip install MetaTrader5 streamlit plotly pandas numpy backtrader
# اختیاری:
pip install pyzmq sqlalchemy
```

### تنظیمات محیط

فایل `.env.example` را به `.env` کپی کنید و مقادیر حساب MT5 خود را وارد نمایید:

```ini
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=Broker-Server-Name
```

> 🔒 فایل `.env` در `.gitignore` قرار دارد و هرگز commit نمی‌شود. تمام ماژول‌ها ابتدا متغیرهای محیطی را بررسی می‌کنند و در صورت نبود، فایل `.env` را می‌خوانند.

---

## 🛠 راهنمای استفاده

### ۱) اجرای ترمینال MT5 (اختیاری)

اگر ترمینال باز نباشد، اسکریپت `launch_mt5.py` مسیر نصب را به‌صورت خودکار در مسیرهای رایج ویندوز پیدا می‌کند:

```bash
python scripts/launch_mt5.py                 # پیدا کن و اجرا کن
python scripts/launch_mt5.py --dry-run       # فقط نمایش مسیر پیدا‌شده
python scripts/launch_mt5.py --path "C:\Program Files\MetaTrader 5\terminal64.exe" --wait
```

### ۲) مشاهده Watchlist نمادها

```bash
python scripts/mt5_market_watch.py --symbols EURUSD,XAUUSD   # جدول
python scripts/mt5_market_watch.py --json                     # خروجی JSON
python scripts/mt5_market_watch.py --demo                     # نمونه بدون اتصال به MT5
```

### ۳) استخراج داده تاریخی (CLI اصلی)

همه نمادهای Market Watch (یا فهرست دلخواه) در تمام تایم‌فریم‌ها دانلود و به‌صورت افزایشی در `market_data/historical/` ذخیره می‌شوند:

```bash
python -m src.python.data.main
# یا با پارامترهای سفارشی:
python -m src.python.data.main \
  --symbols XAUUSD,BTCUSD \
  --timeframes M5,H1,D1 \
  --count 5000 \
  --output-dir market_data/historical
```

خروجی:
- یک پوشه به ازای هر نماد: `market_data/historical/<SYMBOL>-<hash>/<SYMBOL>_<TF>_<timestamp>_<uuid>.csv`
- ادغام هوشمند: رکوردهای جدید append و رکوردهای تکراری update می‌شوند (بدون duplicate).
- خلاصه هر اجرا: `market_data/historical/runs/<run_id>/summary.json` شامل تعداد رکوردها، فایل‌های خروجی و خطاها.

### ۴) داشبورد استراتژی (Streamlit)

```bash
streamlit run src/python/dashboard/app.py
```

در مرورگر می‌توانید:
- نماد و تایم‌فریم را از **MT5 زنده** بگیرید یا فایل **CSV** آپلود کنید،
- یکی از استراتژی‌های موجود را انتخاب و پارامترهایش را تنظیم کنید،
- **بک‌تست** (با سرمایه اولیه دلخواه) و **بهینه‌سازی شبکه پارامترها** را اجرا کنید،
- چارت‌های Plotly، نتایج و گزارش‌ها را مشاهده کنید؛ هر اجرا در `src/python/dashboard/reports/runs_history.jsonl` ثبت می‌شود.

> 💡 اگر فایل `app.py` را مستقیماً با Python/IDLE اجرا کنید، هشدار `missing ScriptRunContext!` ظاهر می‌شود؛ این طبیعی است و باید از دستور `streamlit run` استفاده کنید.

### ۵) افزودن استراتژی جدید

یک فایل `.py` در `src/python/dashboard/strategies/` بسازید:

```python
from base import RiskManagedStrategy, BASE_PARAMS

class MyStrategy(RiskManagedStrategy):
    params = BASE_PARAMS + (("period", 14),)

    def _entry_logic(self):
        ...  # منطق سیگنال خرید/فروش

SPEC = {
    "key": "My-Strategy",
    "title": "استراتژی من",
    "desc": "...",
    "fields": [{"key": "period", "label": "دوره", "type": "int",
                "min": 2, "max": 100, "default": 14}],
}

def get_strategy_class():
    return MyStrategy
```

داشبورد به‌طور خودکار فایل را اسکن کرده و استراتژی را در لیست نمایش می‌دهد — بدون نیاز به تغییر کد داشبورد.

### ۶) استریم داده بلادرنگ

```python
from src.python.data.data_streamer import DataStreamer

streamer = DataStreamer(endpoint="tcp://127.0.0.1:5555")  # ZeroMQ PUB
streamer.publish("XAUUSD", "M5", {"bid": 4000.1, "ask": 4000.3})
```

اگر `pyzmq` نصب نباشد، `DataStreamer` به‌صورت no-op عمل می‌کند و برنامه بدون خطا کار می‌کند.

---

## 🧪 تست و کیفیت کد

```bash
python -m pytest -q --import-mode=importlib --cov=src/python
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy --strict --follow-imports=skip src/python/data/*.py
```

CI در هر push/pull-request روی **windows-latest** (به‌دلیل وابستگی MT5) موارد زیر را اجرا می‌کند:
- `compileall`، فرمت و لینت **Ruff**، **MyPy strict** برای فایل‌های تغییر یافته،
- **Pytest** با پوشش دهی (coverage.xml به‌عنوان artifact)،
- تحلیل امنیتی **CodeQL** و **Dependency Review** (fail روی moderate به بالا).

---

## ⚙️ عیب‌یابی

| مشکل | راه‌حل |
|-------|--------|
| `MT5ConnectionError` هنگام اتصال | مطمئن شوید ترمینال MT5 باز و لاگین‌شده است؛ خروجی `python scripts/launch_mt5.py --dry-run` را بررسی کنید و `MT5_PATH` را در `.env` صحیح تنظیم نمایید. |
| داده‌ای برای نماد برنمی‌گردد | نماد را در Market Watch ترمینال فعال کنید؛ برخی نمادهای بروکر پیشوند/پسوند دارند (مثل `XAUUSD_l`). |
| هشدار `missing ScriptRunContext` | داشبورد را فقط با `streamlit run src/python/dashboard/app.py` اجرا کنید. |
| استراتژی جدید در لیست نیست | فایل باید در پوشه `strategies/` باشد و `SPEC` + `get_strategy_class()` را export کند؛ سپس صفحه را refresh کنید. |
| خواندن CSVهای خروجی | فایل‌ها UTF-8 با سربرگ استاندارد نوشته می‌شوند؛ کافی است با `pandas.read_csv` بخوانید. |

---

## 🔐 امنیت

- هرگز اعتبارنامه واقعی را commit نکنید؛ فقط `.env.example` در مخزن قرار دارد.
- Dependency Review و CodeQL به‌صورت خودکار روی PRها اجرا می‌شوند.
- رمز عبور حساب MT5 را در لاگ‌ها یا خروجی ترمینال چاپ نکنید.

---

## 🗺 نقشه راه

- [ ] موتور ML برای تشخیص/پیش‌بینی روند (بر اساس داده‌های تاریخی جمع‌آوری‌شده)
- [ ] لایه API و پایگاه داده time-series (SQLAlchemy/Postgres) برای ذخیره مداوم داده
- [ ] پنل ریسک و Position Sizing پیشرفته‌تر
- [ ] پشتیبانی از Paper-Trading بلادرنگ روی سیگنال‌های استراتژی‌ها

---

## 📄 مجوز

این مخزن تحت مالکیت [mehdi789k](https://github.com/mehdi789k/AI-Smart-Tred-App) است. تا زمانی که فایل LICENSE اضافه نشده، حقوق نشر برای صاحب مخزن محفوظ است.

---

ساخته‌شده با ❤️ برای معامله‌گران برنامه‌نویس — اگر مفید بود، یک ⭐ فراموش نشود!

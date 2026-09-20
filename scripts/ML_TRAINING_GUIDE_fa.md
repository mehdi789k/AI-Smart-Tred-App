# راهنمای آموزش و استفاده از هوش مصنوعی در سیستم معاملاتی

## مرحله B: مدل پایهٔ قابل تکرار

پس از تکمیل Stage A، baseline رسمی بدون Auto Trading با Random Forest اجرا می‌شود.
این مسیر از دادهٔ معتبر PostgreSQL/TimescaleDB استفاده می‌کند، حداقل ۳۰٬۰۰۰ کندل
را مطالبه می‌کند و هیچ GridSearch یا tuning سنگینی انجام نمی‌دهد:

```powershell
$line = Get-Content ".env" | Where-Object { $_ -match "^DATABASE_URL=" } | Select-Object -First 1
$env:DATABASE_URL = $line.Substring(13)
$env:PYTHONPATH = "."
py -3 scripts/train_model.py --stage-b --symbol XAUUSD --timeframe M5
```

خروجی‌ها:

- `models/random_forest_latest.pkl`: artifact پژوهشی مدل؛ برای اجرای سفارش استفاده نمی‌شود.
- `models/training_summary_XAUUSD_M5.json`: تعداد نمونه، ویژگی‌ها، accuracy، macro
  precision/recall/F1، confusion matrix و classification report.
- `models/random_forest_feature_importance_*.csv`: اهمیت ویژگی‌ها.

تقسیم زمانی داده به‌صورت train/validation/test انجام می‌شود و ترتیب زمانی حفظ
می‌گردد. برچسب‌های confusion matrix به‌ترتیب `SELL`, `HOLD`, `BUY` هستند. پایین
بودن F1، ضعف recall کلاس HOLD یا هر نشانهٔ leakage به معنی توقف در همین مرحله است؛
این artifact تا تکمیل backtest مستقل و paper trading نباید وارد Auto Trading شود.

### اعتبارسنجی walk-forward

پس از آموزش baseline، ارزیابی چندپنجره‌ای را اجرا کنید:

```powershell
$env:PYTHONPATH = "."
py -3 scripts/stage_b_validation.py `
  --splits 5 `
  --test-size 3000 `
  --output models/stage_b_walk_forward_XAUUSD_M5.json
```

این ابزار در هر پنجره، ۲۰ کندل انتهایی train را purge می‌کند؛ چون برچسب
Triple Barrier آن‌ها ممکن است به آیندهٔ پنجرهٔ آزمون نگاه کند. علاوه بر Random
Forest، baseline کلاس غالب را محاسبه می‌کند. در اجرای baseline فعلی، میانگین
Macro F1 مدل `0.3862` و baseline `0.2610` بود؛ مدل بهتر از baseline است اما هنوز
به‌عنوان مجوز معامله تلقی نمی‌شود. انحراف معیار F1 برابر `0.0310` است و باید
پایداری آن با backtest پس از هزینه‌های معامله بررسی شود.

گزارش OOS علاوه بر معیارهای طبقه‌بندی، spread فرضی `0.30`، slippage برابر
`0.05` در هر طرف و commission برابر `0.07` برای هر معامله را اعمال می‌کند.
در اجرای واقعی فعلی، ۱۴٬۳۴۸ معامله با بازده خالص `-1347.58`، expectancy برابر
`-0.0905`، profit factor برابر `0.077` و maximum drawdown برابر `8.59%` ثبت
شد؛ بنابراین paper trading هنوز شروع نمی‌شود.

تحلیل حساسیت labeling نشان داد افق ۱۰ کندلی تعداد HOLD را به ۵٬۰۸۲ می‌رساند،
درحالی‌که افق ۲۰ کندلی ۱٬۵۳۸ HOLD و افق ۴۰ کندلی ۳۵۰ HOLD تولید می‌کند.
افق ۱۰ کاندید بهتری برای آزمایش مجدد است، اما تا آموزش مجدد و تکرار کامل OOS
نباید به‌عنوان انتخاب نهایی پذیرفته شود.

در اجرای مجدد با افق ۱۰، Macro F1 برابر `0.4364`، انحراف معیار آن `0.0143`،
بازده خالص `1711.03`، expectancy برابر `0.1415`، profit factor برابر `1.1307`
و maximum drawdown برابر `2.21%` به‌دست آمد. این نتیجه فقط یک gate پژوهشی است؛
backtest فعلی هر معامله را با یک کندل باز و بسته می‌کند و باید قبل از تصمیم نهایی
با spread واقعی broker و مدل اندازه‌پوزیشن مستقل تأیید شود.

با عبور این نسخه از gate اولیه، مرحلهٔ بعد فقط **Shadow Trading** است:

```powershell
$env:EXECUTION_MODE = "shadow"
$env:SHADOW_LEDGER_PATH = "data/shadow_orders_h10.jsonl"
```

هرگز `EXECUTION_MODE=live` را برای این artifact تنظیم نکنید. Shadow Trading باید
حداقل روی یک بازهٔ مستقل اجرا شود و ledger، prediction، decision، هزینه و
ردشدن‌های risk را ثبت کند؛ تا پایان آن هیچ سفارش MT5 ارسال نمی‌شود.

## نمای کلی

این پروژه از یک سیستم یادگیری ماشین پیشرفته برای بهبود تصمیم‌گیری‌های معاملاتی استفاده می‌کند. این راهنما تمام مراحل لازم برای آموزش، ارزیابی و نگهداری مدل‌های ML را پوشش می‌دهد.

---

## ۱. پیش‌نیازها

### مسیر واقعی پیشنهادی: MT5 روی Windows و دیتابیس در Docker

ترمینال MT5 باید روی Windows اجرا شود؛ سرویس Linux داخل Docker به ترمینال
محلی MT5 دسترسی مستقیم ندارد. ابتدا وابستگی host را نصب کنید:

```powershell
py -3.12 -m pip install -r requirements/lock/mt5.txt
Copy-Item .env.example .env
```

در `.env` مقدار `POSTGRES_PASSWORD` و اطلاعات حساب MT5 را تکمیل کنید. سپس
TimescaleDB را اجرا کنید:

```powershell
docker compose up -d timescaledb
```

پس از فعال بودن MT5 و قابل مشاهده بودن نماد `XAUUSD`، حداقل ۳۰٬۰۰۰ کندل واقعی
را دریافت و quality-gate کنید:

```powershell
$env:PYTHONPATH = "src\python"
py -3 -m data.main --stage-a --bars 30000
```

برای آموزش مدل از داده‌ی ذخیره‌شده در PostgreSQL استفاده کنید؛ اجرای زیر هیچ
سفارش معاملاتی ارسال نمی‌کند:

```powershell
py -3 scripts/train_model.py `
  --source database `
  --database-url $env:DATABASE_URL `
  --symbol XAUUSD `
  --timeframe M5 `
  --output-dir models
```

یا آموزش را در container اجرا کنید:

```powershell
docker compose --profile training run --rm ml-training
```

منبع `database` به‌صورت fail-closed در صورت نبودن `DATABASE_URL` یا کمتر بودن
داده از حداقل نمونه‌ها متوقف می‌شود. مسیر `json` فقط برای داده‌ی قدیمی و
آزمایش‌های محلی است و جایگزین داده‌ی معتبر MT5 نیست.

### نصب کتابخانه‌های مورد نیاز

```bash
pip install scikit-learn xgboost pandas numpy optuna
```

یا با استفاده از ورودی کامل dependency:

```bash
pip install -r requirements/full.in
```

---

## ۲. ساختار فایل‌های آموزش ML

### فایل‌های ایجاد شده:

```
scripts/
├── train_model.py              # اسکریپت اصلی آموزش
├── backtest_comparison.py      # مقایسه عملکرد با/بدون ML
├── retrain_model.py            # بازآموزی دوره‌ای
└── ml_training_config.json     # تنظیمات آموزش

models/                         # مدل‌های آموزش‌دیده ذخیره می‌شوند
backtest_results/               # نتایج بک‌تست
```

---

## ۳. مراحل آموزش مدل

### مرحله ۱: آماده‌سازی داده‌ها

داده‌های تاریخی به‌طور خودکار از پوشه `market_data/` بارگذاری می‌شوند.

**نکته مهم:** داده‌ها باید شامل دوره‌های مختلف بازار باشند:
- روندهای صعودی
- روندهای نزولی  
- بازارهای رنج

### مرحله ۲: اجرای آموزش اولیه

برای آموزش مدل با تنظیمات پیش‌فرض:

```bash
python scripts/train_model.py --symbol XAUUSD_l --timeframe M5 --years 2
```

**پارامترهای مهم:**
- `--symbol`: نماد معاملاتی (مثلاً XAUUSD_l, EURUSD)
- `--timeframe`: تایم‌فریم (M15, H1, H4)
- `--years`: تعداد سال‌های داده تاریخی
- `--config`: مسیر فایل تنظیمات JSON

### مرحله ۳: آموزش با تنظیمات سفارشی

با استفاده از فایل پیکربندی:

```bash
python scripts/train_model.py --config scripts/ml_training_config.json
```

**تنظیمات قابل تغییر در `ml_training_config.json`:**

```json
{
  "symbols": ["XAUUSD_l", "EURUSD", "GBPUSD"],
  "timeframes": ["M15", "H1", "H4"],
  "years_of_data": 2,
  
  "feature_engineering": {
    "use_rsi": true,
    "use_macd": true,
    "use_bollinger": true,
    "use_atr": true,
    "use_smc_features": true
  },
  
  "triple_barrier": {
    "profit_target_multiplier": 2.0,
    "stop_loss_multiplier": 1.0,
    "time_barrier_bars": 20
  },
  
  "models": {
    "model_type": "ensemble",
    "random_forest": {
      "n_estimators": 200,
      "max_depth": 15
    }
  }
}
```

---

## ۴. ویژگی‌های مهندسی‌شده (Feature Engineering)

مدل از ویژگی‌های زیر استفاده می‌کند:

### الف) ویژگی‌های پایه:
- بازدهی قیمت (Return_1, Return_5, Return_10)
- نوسان‌پذیری (Volatility)
- موقعیت قیمت در کندل
- میانگین‌های متحرک (SMA)

### ب) اندیکاتورهای تکنیکال:
- **RSI**: شاخص قدرت نسبی + مشتقات آن
- **MACD**: واگرایی همگرایی میانگین متحرک
- **Bollinger Bands**: باندهای بولینگر + موقعیت قیمت
- **ATR**: محدوده واقعی میانگین
- **ADX**: شاخص جهت‌دار میانگین
- **Stochastic**: اسیلاتور تصادفی

### ج) ویژگی‌های حجم:
- میانگین حجم
- نسبت حجم
- اسپایک‌های حجمی

### د) ویژگی‌های SMC:
- سقف‌ها و کف‌های Swing
- فاصله از سطوح کلیدی

### ه) الگوهای کندلی:
- اندازه بدنه کندل
- سایه‌های بالا و پایین
- الگوی Doji
- الگوی Engulfing

---

## ۵. روش برچسب‌گذاری هوشمند (Triple Barrier Method)

این روش پیشرفته‌ترین روش برچسب‌گذاری برای معاملات است:

### سه مانع:
1. **مانع سود (Upper Barrier)**: `entry_price * (1 + 2.0 * ATR)`
2. **مانع ضرر (Lower Barrier)**: `entry_price * (1 - 1.0 * ATR)`
3. **مانع زمانی (Time Barrier)**: حداکثر ۲۰ کندل انتظار

### برچسب‌ها:
- **1 (BUY موفق)**: قیمت ابتدا به حد سود رسید
- **-1 (SELL موفق)**: قیمت ابتدا به حد ضرر رسید
- **0 (HOLD)**: زمان تمام شد یا هیچ‌کدام hit نشد

**مزیت:** این روش واقعیت معاملات را بهتر از برچسب‌های ساده سود/ضرر نشان می‌دهد.

---

## ۶. آموزش مدل‌ها

### مدل‌های پشتیبانی‌شده:

#### ۱. Random Forest
- مناسب برای داده‌های جدولی
- مقاومت بالا در برابر Overfitting
- قابلیت تفسیرپذیری (Feature Importance)

#### ۲. XGBoost
- عملکرد عالی در مسابقات Kaggle
- سرعت آموزش بالا
- دقت بیشتر در داده‌های بزرگ

#### ۳. Ensemble (پیشنهادی)
- ترکیب هر دو مدل با Voting
- بهترین عملکرد کلی

### تنظیم هایپرپارامترها:

سیستم به‌طور خودکار با استفاده از `GridSearchCV` بهترین پارامترها را پیدا می‌کند:

```python
param_grid = {
    'n_estimators': [100, 200, 300],
    'max_depth': [10, 15, 20, None],
    'min_samples_split': [2, 5, 10]
}
```

---

## ۷. ارزیابی مدل

### معیارهای ارزیابی:

پس از آموزش، معیارهای زیر گزارش می‌شوند:

- **Accuracy**: دقت کلی پیش‌بینی
- **Precision**: دقت پیش‌بینی‌های مثبت
- **Recall**: نرخ شناسایی موارد مثبت
- **F1 Score**: میانگین هارمونیک Precision و Recall
- **Confusion Matrix**: ماتریس خطا

### خروجی نمونه:

```
Random Forest Performance:
Accuracy: 0.6234
Precision (macro): 0.6105
Recall (macro): 0.5987
F1 Score (macro): 0.6045

Confusion Matrix:
[[120  30  25]
 [ 35 110  40]
 [ 20  35 125]]
```

---

## ۸. بک‌تست مقایسه‌ای (A/B Testing)

برای مقایسه عملکرد استراتژی با و بدون ML:

```bash
python scripts/backtest_comparison.py \
  --symbol XAUUSD_l \
  --timeframe M5 \
  --model-path models/random_forest_latest.pkl
```

### معیارهای مقایسه:

| معیار | بدون ML | با ML | بهبود |
|-------|---------|-------|--------|
| Total Return | $X | $Y | +Z% |
| Win Rate | A% | B% | +(B-A)% |
| Profit Factor | P1 | P2 | +(P2-P1) |
| Max Drawdown | D1% | D2% | -(D1-D2)% |
| Sharpe Ratio | S1 | S2 | +(S2-S1) |

### تفسیر نتایج:

- **Profit Factor > 1.5**: خوب
- **Win Rate > 55%**: قابل قبول
- **Max Drawdown < 20%**: مدیریت ریسک خوب
- **Sharpe Ratio > 1.0**: بازده تعدیل‌شده با ریسک خوب

---

## ۹. ادغام با سیستم معاملاتی

### بارگذاری مدل در سیستم:

مدل‌های آموزش‌دیده به‌طور خودکار در پوشه `models/` ذخیره می‌شوند و سیستم اصلی از طریق `model_manager.py` آن‌ها را بارگذاری می‌کند.

**مسیرهای مدل:**
- `models/random_forest_latest.pkl`: آخرین مدل Random Forest
- `models/xgboost_latest.pkl`: آخرین مدل XGBoost
- `models/{SYMBOL}_{TIMEFRAME}_production.pkl`: مدل production برای نماد خاص

### وزن‌دهی ML در تصمیم‌گیری:

در سیستم اصلی (`src/python/strategies/`), ML با وزن ۲۵٪ در امتیاز نهایی سیگنال تأثیر دارد:

```python
if ml_prediction == strategy_signal:
    score += 0.2  # تقویت سیگنال
elif ml_prediction != strategy_signal:
    score -= 0.3  # تضعیف سیگنال مخالف
```

---

## ۱۰. بازآموزی دوره‌ای (Retraining)

بازارها پویا هستند و مدل‌ها باید به‌روز شوند.

### برنامه پیشنهادی:

| تایم‌فریم | تناوب بازآموزی | حداقل داده جدید |
|-----------|----------------|-----------------|
| M1-M15    | هفته‌ای یکبار  | ۵۰۰ کندل        |
| H1-H4     | ماهی یکبار     | ۲۰۰ کندل        |
| D1-W1     | هر ۳ ماه       | ۱۰۰ کندل        |

### اجرای بازآموزی:

```bash
# بازآموزی دستی
python scripts/retrain_model.py --symbol XAUUSD_l --timeframe M5 --force

# بازآموزی هفتگی
python scripts/retrain_model.py --symbol XAUUSD_l --timeframe M5 --schedule weekly
```

### معیارهای جایگزینی مدل:

مدل جدید فقط در صورتی جایگزین می‌شود که:
- بهبود Accuracy ≥ 2% **یا**
- بهبود Profit Factor ≥ 0.1

در غیر این صورت، مدل جدید بایگانی می‌شود.

---

## ۱۱. اتوماسیون با Cron (Linux/Mac)

### تنظیم بازآموزی خودکار:

```bash
# ویرایش crontab
crontab -e

# اضافه کردن job هفتگی (هر دوشنبه ساعت ۲ بامداد)
0 2 * * 1 cd /workspace && python scripts/retrain_model.py --schedule weekly >> logs/ml_retrain.log 2>&1
```

### برای Windows Task Scheduler:

1. باز کردن Task Scheduler
2. Create Basic Task
3. Trigger: Weekly
4. Action: Start a program
   - Program: `python.exe`
   - Arguments: `scripts/retrain_model.py --schedule weekly`
   - Start in: `C:\workspace`

---

## ۱۲. عیب‌یابی

### مشکل: داده کافی نیست

**خطا:** `Insufficient data (X samples), skipping`

**راه‌حل:**
- افزایش `years_of_data` در تنظیمات
- استفاده از تایم‌فریم پایین‌تر
- دانلود داده‌های بیشتر از MT5

### مشکل: Overfitting

**نشانه:** Accuracy آموزش بالا (>90%) اما Accuracy تست پایین (<55%)

**راه‌حل:**
- کاهش `max_depth` در Random Forest
- افزایش `min_samples_split`
- استفاده از Cross-Validation با folds بیشتر
- کاهش تعداد ویژگی‌ها

### مشکل: Underfitting

**نشانه:** Accuracy آموزش و تست هر دو پایین (<55%)

**راه‌حل:**
- افزایش `n_estimators`
- افزایش `max_depth`
- اضافه کردن ویژگی‌های بیشتر
- استفاده از Triple Barrier با پارامترهای متفاوت

### مشکل: عدم تعادل کلاس‌ها

**نشانه:** مدل فقط یک کلاس را پیش‌بینی می‌کند

**راه‌حل مرحله C:**
- `class_weight_strategy=balanced` وزن‌ها را فقط از بخش train محاسبه می‌کند؛
  از انتقال اطلاعات validation/test به آموزش جلوگیری می‌شود.
- فایل `models/training_summary_*.json` بخش `label_diagnostics` را شامل
  توزیع SELL/HOLD/BUY، تحلیل ماهانه، و علت‌های HOLD ذخیره می‌کند.
- مقدار `hold_warning_threshold` برای هشدار سهم بالای HOLD قابل تنظیم است.
- در برخورد هم‌زمان دو barrier در یک کندل، چون OHLC ترتیب درون‌کندو را
  مشخص نمی‌کند، تصمیم به‌صورت deterministic بر اساس barrier نزدیک‌تر گرفته
  می‌شود و باید در بک‌تست حساسیت آن بررسی شود.

---

## ۱۳. بهترین روش‌ها

### ✅ انجام دهید:

1. **داده‌های با کیفیت**: حداقل ۲ سال داده با کیفیت
2. **Cross-Validation**: همیشه از CV استفاده کنید
3. **A/B Testing**: قبل از deployment بک‌تست بگیرید
4. **Retraining**: بازآموزی دوره‌ای فراموش نشود
5. **Logging**: تمام آموزش‌ها را لاگ کنید
6. **Version Control**: نسخه‌های مختلف مدل را نگه دارید

### ❌ انجام ندهید:

1. **Look-ahead Bias**: مطمئن شوید ویژگی‌ها از آینده نمی‌آیند
2. **Overfitting**: روی داده‌های تست بیش‌ازحد تنظیم نکنید
3. **Data Leakage**: داده‌های تست نباید در آموزش استفاده شوند
4. **Ignore Transaction Costs**: در بک‌تست اسپرد و کمیسیون را لحاظ کنید
5. **Blind Trust**: ML کمکی است، نه جایگزین استراتژی

---

## ۱۴. مثال کامل گردش کار

```bash
# ۱. آموزش اولیه
python scripts/train_model.py --symbol XAUUSD_l --timeframe M5 --years 2

# ۲. بررسی خروجی‌ها
ls -lh models/
cat models/training_summary_XAUUSD_l_M5.json

# ۳. بک‌تست مقایسه‌ای
python scripts/backtest_comparison.py \
  --symbol XAUUSD_l \
  --timeframe M5 \
  --model-path models/random_forest_latest.pkl

# ۴. بررسی نتایج بک‌تست
cat backtest_results/backtest_comparison_*.json

# ۵. اگر نتایج خوب بود، تنظیم cron برای بازآموزی
crontab -e
# اضافه کردن: 0 2 * * 1 cd /workspace && python scripts/retrain_model.py --schedule weekly

# ۶. مانیتورینگ مستمر
tail -f logs/ml_retrain.log
```

---

## ۱۵. منابع بیشتر

### مستندات داخلی:
- `src/python/ml/__init__.py`: کد مدل‌های ML
- `src/python/strategies/`: استراتژی‌های معاملاتی
- `docs/ML_INTEGRATION.md`: جزئیات ادغام ML

### منابع خارجی:
- [Scikit-learn Documentation](https://scikit-learn.org/)
- [XGBoost Documentation](https://xgboost.readthedocs.io/)
- [Triple Barrier Method by Marcos López de Prado](https://www.google.com/search?q=triple+barrier+method+lopez+de+prado)

---

## پشتیبانی

برای سؤالات یا مشکلات:
1. بررسی لاگ‌ها در `logs/`
2. مطالعه مستندات فوق
3. بررسی issueهای GitHub

---

**نسخه مستند:** 1.0  
**آخرین به‌روزرسانی:** 2024

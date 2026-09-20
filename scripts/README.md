# ML Training Scripts - راهنمای سریع

## 📁 فایل‌های موجود

| فایل | توضیحات | کاربرد |
|------|---------|--------|
| `train_model.py` | اسکریپت اصلی آموزش مدل | آموزش اولیه مدل‌های ML |
| `backtest_comparison.py` | مقایسه عملکرد با/بدون ML | A/B Testing |
| `retrain_model.py` | بازآموزی دوره‌ای | نگهداری و به‌روزرسانی مدل |
| `ml_training_config.json` | تنظیمات آموزش | پیکربندی سفارشی |
| `ML_TRAINING_GUIDE_fa.md` | مستندات کامل | راهنمای جامع فارسی |

---

## 🚀 شروع سریع

### ۱. آموزش اولیه مدل

```bash
# آموزش با تنظیمات پیش‌فرض
python scripts/train_model.py --symbol XAUUSD_l --timeframe M5 --years 2

# آموزش با تنظیمات سفارشی
python scripts/train_model.py --config scripts/ml_training_config.json
```

**خروجی:**
- مدل‌های ذخیره‌شده در `models/`
- گزارش آموزش در `models/training_summary_*.json`
- گزارش توزیع برچسب‌ها در همان summary شامل تفکیک ماهانه، سهم HOLD و علت‌های
  `time_barrier`/`invalid_atr`
- اهمیت ویژگی‌ها در `models/*_feature_importance_*.csv`

در مرحله C، وزن کلاس‌ها فقط از نمونه‌های بخش train محاسبه می‌شود. مقدار
`class_weight_strategy` به‌صورت پیش‌فرض `balanced` است و با مقدار `none` قابل
غیرفعال‌سازی است. اگر سهم HOLD از `hold_warning_threshold` عبور کند، آموزش
متوقف نمی‌شود اما هشدار صریح در لاگ و summary ثبت می‌شود.

---

### ۲. مقایسه عملکرد (A/B Testing)

```bash
python scripts/backtest_comparison.py \
  --symbol XAUUSD_l \
  --timeframe M5 \
  --model-path models/random_forest_latest.pkl
```

**خروجی:**
- معیارهای عملکرد با و بدون ML
- گزارش مقایسه در `backtest_results/`

---

### ۳. بازآموزی دوره‌ای

```bash
# بازآموزی دستی
python scripts/retrain_model.py --symbol XAUUSD_l --timeframe M5 --force

# بازآموزی هفتگی خودکار
python scripts/retrain_model.py --symbol XAUUSD_l --timeframe M5 --schedule weekly
```

**خروجی:**
- مدل جدید در صورت بهبود عملکرد
- لاگ بازآموزی در `models/retraining_log.json`

---

## 📊 پارامترهای مهم

### train_model.py

| پارامتر | پیش‌فرض | توضیحات |
|---------|---------|---------|
| `--symbol` | XAUUSD_l | نماد معاملاتی |
| `--timeframe` | M5 | تایم‌فریم |
| `--years` | 2 | سال‌های داده تاریخی |
| `--config` | - | فایل تنظیمات JSON |
| `--data-dir` | market_data | پوشه داده‌ها |
| `--output-dir` | models | پوشه خروجی |
| `--log-level` | INFO | سطح لاگ |

### backtest_comparison.py

| پارامتر | پیش‌فرض | توضیحات |
|---------|---------|---------|
| `--symbol` | XAUUSD_l | نماد معاملاتی |
| `--timeframe` | M5 | تایم‌فریم |
| `--model-path` | - | مسیر مدل ML |
| `--initial-capital` | 10000 | سرمایه اولیه |
| `--data-dir` | market_data | پوشه داده‌ها |

### retrain_model.py

| پارامتر | پیش‌فرض | توضیحات |
|---------|---------|---------|
| `--symbol` | XAUUSD_l | نماد معاملاتی |
| `--timeframe` | M5 | تایم‌فریم |
| `--schedule` | manual | daily/weekly/monthly/manual |
| `--force` | False | اجبار به بازآموزی |

---

## 🔧 تنظیمات سفارشی

ویرایش `scripts/ml_training_config.json`:

```json
{
  "symbols": ["XAUUSD_l", "EURUSD"],
  "timeframes": ["M15", "H1"],
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
    "model_type": "ensemble"
  }
}
```

---

## 📈 معیارهای ارزیابی

### معیارهای مدل ML:
- **Accuracy**: دقت کلی
- **Precision**: دقت پیش‌بینی‌های مثبت
- **Recall**: نرخ شناسایی موارد مثبت
- **F1 Score**: میانگین هارمونیک

### معیارهای بک‌تست:
- **Total Return**: بازده کل
- **Win Rate**: نرخ برد
- **Profit Factor**: نسبت سود به ضرر
- **Max Drawdown**: حداکثر افت سرمایه
- **Sharpe Ratio**: بازده تعدیل‌شده با ریسک

---

## ⚠️ نکات مهم

1. **داده‌های کافی**: حداقل ۱۰۰۰ نمونه داده نیاز است
2. **Cross-Validation**: به‌طور خودکار انجام می‌شود (۵-fold)
3. **Hyperparameter Tuning**: به‌طور پیش‌فرض فعال است
4. **Feature Importance**: گزارش اهمیت ویژگی‌ها ذخیره می‌شود

---

## 📚 مستندات کامل

برای راهنمای جامع فارسی، فایل `ML_TRAINING_GUIDE_fa.md` را مطالعه کنید.

---

## 🆘 عیب‌یابی سریع

### خطای "Insufficient data"
```bash
# افزایش داده‌های تاریخی
python scripts/train_model.py --years 3
```

### خطای "XGBoost not installed"
```bash
pip install xgboost
```

### خطای "No data found"
```bash
# بررسی وجود داده‌ها
ls market_data/*XAUUSD*M5*
```

---

**نسخه:** 1.0  
**تاریخ:** 2024

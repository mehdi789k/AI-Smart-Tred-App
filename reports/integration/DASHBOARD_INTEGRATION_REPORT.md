# راهنمای استفاده از Docker Compose

## 🐳 سرویس‌های موجود

| سرویس | پورت | توضیحات |
|-------|------|---------|
| **timescaledb** | 5432 | دیتابیس سری زمانی PostgreSQL + TimescaleDB |
| **pgadmin** | 5050 | رابط گرافیکی مدیریت دیتابیس |
| **redis** | 6379 | کش برای داده‌های لحظه‌ای |
| **dashboard** | 8501 | داشبورد Streamlit (با پروفایل) |
| **ml-training** | - | سرویس آموزش مدل (با پروفایل) |

---

## 🚀 راه‌اندازی سریع

### شروع تمام سرویس‌های پایه
```bash
docker-compose up -d
```

### مشاهده وضعیت سرویس‌ها
```bash
docker-compose ps
```

### مشاهده لاگ‌ها
```bash
# همه سرویس‌ها
docker-compose logs -f

# فقط دیتابیس
docker-compose logs -f timescaledb
```

---

## 📊 راه‌اندازی داشبورد

داشبورد به صورت پیش‌فرض غیرفعال است. برای فعال‌سازی:

```bash
# راه‌اندازی با پروفایل dashboard
docker-compose --profile dashboard up -d dashboard

# یا همراه با سایر سرویس‌ها
docker-compose --profile dashboard up -d
```

**دسترسی به داشبورد:** http://localhost:8501

---

## 🧠 آموزش مدل با Docker

برای اجرای اسکریپت‌های آموزش ML:

```bash
# نمایش راهنما
docker-compose --profile training run ml-training python scripts/train_model.py --help

# آموزش مدل
docker-compose --profile training run ml-training \
    python scripts/train_model.py \
    --symbol XAUUSD_l \
    --timeframe M5 \
    --years 2

# مقایسه عملکرد
docker-compose --profile training run ml-training \
    python scripts/backtest_comparison.py \
    --symbol XAUUSD_l \
    --timeframe M5 \
    --model-path models/random_forest_latest.pkl

# بازآموزی هفتگی
docker-compose --profile training run ml-training \
    python scripts/retrain_model.py \
    --symbol XAUUSD_l \
    --timeframe M5 \
    --schedule weekly
```

---

## 🔧 دستورات پرکاربرد

### توقف سرویس‌ها
```bash
# توقف موقت
docker-compose down

# توقف و حذف volumeها
docker-compose down -v
```

### ریست کامل
```bash
docker-compose down -v
docker-compose up -d
```

### ورود به shell دیتابیس
```bash
docker exec -it mt5-timescaledb psql -U Admin -d mt5_data
```

### ورود به container
```bash
# دیتابیس
docker exec -it mt5-timescaledb bash

# PgAdmin
docker exec -it mt5-pgadmin bash

# Redis
docker exec -it mt5-redis redis-cli
```

### بیلد مجدد imageها
```bash
docker-compose build

# بدون کش
docker-compose build --no-cache
```

---

## 🔐 اطلاعات ورود

> مقادیر واقعی اعتبارنامه در مستندات نگهداری نمی‌شوند. آن‌ها را فقط از فایل `.env`
> محلی یا secret manager بخوانید و در صورت افشای قبلی، فوراً تغییر دهید.

### دیتابیس (TimescaleDB)
- **Host:** localhost
- **Port:** 5432
- **Database:** mt5_data
- **Username:** Admin
- **Password:** از `.env` محلی

### PgAdmin
- **URL:** http://localhost:5050
- **Email:** از تنظیمات محلی
- **Password:** از `.env` محلی

### Redis
- **Host:** localhost
- **Port:** 6379

### Dashboard
- **URL:** http://localhost:8501

---

## 📁 Volumeها

| Volume | مسیر داخلی | توضیحات |
|--------|-----------|---------|
| `mt5_timescale_data` | `/var/lib/postgresql/data` | داده‌های دیتابیس |
| `pgadmin_data` | `/var/lib/pgadmin` | تنظیمات PgAdmin |
| `redis_data` | `/data` | داده‌های Redis |
| `models_cache` | - | کش مدل‌های ML |

---

## 🌐 شبکه

تمام سرویس‌ها در شبکه `trading-network` با محدوده IP `172.28.0.0/16` قرار دارند.

اتصال بین سرویس‌ها با استفاده از نام سرویس:
```python
# مثال اتصال از dashboard به دیتابیس
DATABASE_URL = "postgresql://Admin:Mehdi28810@timescaledb:5432/mt5_data"

# اتصال به Redis
REDIS_URL = "redis://redis:6379/0"
```

---

## ⚙️ متغیرهای محیطی Dashboard

| متغیر | مقدار پیش‌فرض | توضیحات |
|-------|--------------|---------|
| `DATABASE_URL` | postgresql://Admin:... | اتصال به دیتابیس |
| `REDIS_URL` | redis://redis:6379/0 | اتصال به Redis |
| `MT5_ENABLED` | false | فعال‌سازی MT5 (در Docker غیرفعال) |

---

## 🛠️ عیب‌یابی

### بررسی سلامت سرویس‌ها
```bash
docker-compose ps
```

### مشاهده لاگ خطاها
```bash
docker-compose logs <service_name>
```

### ریست سرویس خاص
```bash
docker-compose restart <service_name>
```

### حذف و ایجاد مجدد
```bash
docker-compose rm -f <service_name>
docker-compose up -d <service_name>
```

### بررسی فضای دیسک
```bash
docker system df
docker-compose exec timescaledb df -h
```

---

## 📝 نکات مهم

1. **پروفایل‌ها:** سرویس‌های `dashboard` و `ml-training` با پروفایل فعال می‌شوند تا منابع فقط هنگام نیاز مصرف شوند.

2. **Health Check:** تمام سرویس‌ها دارای health check هستند و سرویس‌های وابسته فقط پس از سالم بودن سرویس‌های پیش‌نیاز شروع می‌شوند.

3. **Persistence:** داده‌ها در volumeها ذخیره می‌شوند و با توقف containerها از بین نمی‌روند.

4. **Network Isolation:** سرویس‌ها در شبکه ایزوله قرار دارند و فقط از طریق پورت‌های تعریف‌شده قابل دسترسی هستند.

5. **Security:** رمزهای عبور پیش‌فرض را در محیط تولید تغییر دهید.

---

## 🔄 به‌روزرسانی

```bash
# Pull imageهای جدید
docker-compose pull

# بیلد و راه‌اندازی مجدد
docker-compose up -d --build
```

---

## 📊 مانیتورینگ

### مشاهده مصرف منابع
```bash
docker stats
```

### کوئری از دیتابیس
```bash
docker exec -it mt5-timescaledb psql -U Admin -d mt5_data -c "SELECT * FROM daily_performance LIMIT 10;"
```

### بررسی اندازه دیتابیس
```bash
docker exec -it mt5-timescaledb psql -U Admin -d mt5_data -c "\dt+"
```

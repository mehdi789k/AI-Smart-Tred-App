# راهنمای استقرار Observability

این فایل مسیر اجرای Prometheus، Grafana و Alertmanager را برای محیط داخلی
توضیح می‌دهد. این سرویس‌ها فقط برای شبکه داخلی bind شده‌اند و فعال‌سازی
notification خارجی عمداً نیازمند تنظیم Secret توسط اپراتور است.

## اجرای سرویس‌ها

```powershell
docker compose up -d --build
docker compose up -d prometheus alertmanager grafana
```

برای تست scale-out در Docker Compose:

```powershell
docker compose up -d --scale api=2 api prometheus
```

Prometheus با DNS service discovery سرویس `api` را scrape می‌کند و برای هر
replica label `instance` جداگانه نگه می‌دارد. Queryهای dashboard با `sum`,
`min` و `max` بین replicaها aggregate می‌شوند.

در حالت Compose، محدودسازی اصلی `/metrics` با bind شدن Prometheus و Grafana به
`127.0.0.1` و استفاده از شبکه داخلی Docker انجام می‌شود. اگر
`OBS_METRICS_TOKEN` را فعال کنید، باید header احراز هویت Prometheus را نیز در
پیکربندی scrape سازمانی خود اضافه کنید؛ در غیر این صورت scrapeها `401` خواهند
شد.

## آدرس‌های داخلی

| سرویس | آدرس |
|---|---|
| API liveness | `http://127.0.0.1:8000/health` |
| API readiness | `http://127.0.0.1:8000/ready` |
| Prometheus | `http://127.0.0.1:9090` |
| Alertmanager | `http://127.0.0.1:9093` |
| Grafana | `http://127.0.0.1:3000` |

## اجرای دائمی Collector در Windows

Collector باید روی همان Windows hostای اجرا شود که MT5 Terminal روی آن فعال
است؛ اجرای آن داخل API یا Dashboard باعث چندبار ingestion و رقابت برای اتصال
به terminal می‌شود. برای اجرای hidden از یک Scheduled Task با گزینهٔ
`Run whether user is logged on or not` و command زیر استفاده کنید:

```powershell
py -3.12 -m src.python.data.main --no-history
```

مسیرهای پیش‌فرض وضعیت collector عبارت‌اند از:

- `data\collector_health.json` برای heartbeat اتمیک و قابل خواندن توسط watchdog
- `data\collector.lock` برای جلوگیری از اجرای هم‌زمان دو process

برای health check مستقل:

```powershell
py -3.12 -m src.python.data.main --health
```

نمونهٔ watchdog برای Scheduled Task جداگانه در
[scripts/watch_collector.ps1](../scripts/watch_collector.ps1) قرار دارد. این
watchdog در صورت heartbeat stale یا وضعیت غیر `running`، task اصلی را با
`Start-ScheduledTask` دوباره اجرا می‌کند و exit code غیرصفر برمی‌گرداند.

متغیرهای `COLLECTOR_HEALTH_FILE` و `COLLECTOR_LOCK_FILE` برای تغییر این مسیرها
در محیط‌های مختلف قابل تنظیم هستند. watchdog باید در صورت نبودن فایل health،
وضعیت `stopped`، یا قدیمی‌شدن `updated_at` نسبت به timeout عملیاتی، task را
restart کند. lock باید حذف نشود؛ آزادشدن آن با بسته‌شدن process انجام می‌شود.

## Alert notification

## Retention and recovery evidence

The deployment policy retains order audit data, broker responses, operational
logs, and backup artifacts for 90 days by default. Configure the four
`*_RETENTION_DAYS` variables in the deployment environment and ensure the
corresponding database/log cleanup jobs are scoped to managed stores only.
Never emit database passwords or broker credentials in backup names, logs, or
recovery reports.

For a disposable staging database, create a backup and run
`scripts\validate_recovery.ps1 -ConfirmRestore`. The validator fails closed
when the backup is missing or older than the 15-minute RPO target, when
restore/schema verification fails, or when recovery exceeds the one-hour RTO
target. Its JSON result records the backup age, schema revision, and elapsed
recovery time. A local result is not production evidence until the managed
backup schedule, WAL/archive retention, encryption, and restore target are
validated.

فایل [ops/alertmanager/alertmanager.yml](../ops/alertmanager/alertmanager.yml)
فعلاً receiver خالی دارد تا Secret به Repository وارد نشود. پیش از production،
یک receiver Secret-backed برای Slack، PagerDuty یا Email اضافه کنید و سپس:

```powershell
docker compose restart alertmanager
```

Metricهای ایمنی که در [ops/prometheus/alerts.yml](../ops/prometheus/alerts.yml)
تعریف شده‌اند شامل رد سفارش، timeout در gateway، signal منقضی/کهنه،
درخواست idempotent تکراری و gap داده هستند. آستانه‌های پیش‌فرض یک رخداد در
بازهٔ پنجرهٔ alert است و در محیط‌های حساس می‌توانند با متغیرهای
`OBS_ALERT_*_THRESHOLD` در `.env` تنظیم شوند.

## Branch protection در GitHub

فعال‌سازی branch protection از داخل checkout محلی انجام نمی‌شود و باید توسط
مدیر repository در Settings > Branches انجام شود. برای شاخهٔ اصلی این موارد را
اجباری کنید:

1. Required status check: `test`.
2. Require branches to be up to date before merging.
3. Require a pull request before merging و حداقل یک reviewer.
4. Block force pushes و حذف شاخهٔ اصلی.
5. در صورت استفاده از چند workflow، فقط نام check پایدار job را required کنید،
   نه نام موقت stepها.

تا زمانی که این تنظیمات در GitHub ثبت نشوند، وجود workflow به‌تنهایی تضمین
نمی‌کند که کد بدون تست، lint، type-check، migration و Compose validation merge
نخواهد شد.

## MT5 Demo و Strategy Tester

فعال‌سازی معامله واقعی در این compose ممنوع است. مقادیر زیر باید در محیط Demo
به‌صورت صریح و جدا از production تنظیم شوند:

```env
MT5_DEMO_ENABLED=true
MT5_AUTO_TRADING_ENABLED=false
MT5_LEGACY_ORDER_PATH_ENABLED=false
```

در EA نیز `InpAllowLiveTrading=false` باقی بماند. تست Demo فقط پس از:

1. بررسی `/health` و `/ready`
2. تأیید `trading.allowed=true`
3. تأیید Circuit Breaker در وضعیت `armed`
4. اجرای Strategy Tester با داده تاریخی
5. اجرای Demo Shadow Mode بدون ارسال سفارش

مجاز است. هیچ کانتینر API نباید برای اجرای Strategy Tester به terminal
دسترسی write یا credential production داشته باشد.

برای بررسی خودکار gateهای بدون معامله:

```powershell
py -3.12 scripts/verify_demo_readiness.py --base-url http://127.0.0.1:8000
```

این اسکریپت فقط `/health` و `/ready` را می‌خواند و به endpoint سفارش دسترسی
ندارد. خروجی موفق آن جایگزین Strategy Tester نیست؛ Strategy Tester باید در
ترمینال MT5 با فایل set مخصوص حساب Demo، `InpAllowLiveTrading=false` و گزارش
نتایج ذخیره‌شده اجرا شود.

برای بررسی مستقیم terminal متصل، بدون ارسال سفارش:

```powershell
py -3.12 scripts/verify_mt5_demo.py
```

پیش از اجرای Strategy Tester، ورودی‌های fail-closed را بررسی کنید:

```powershell
py -3.12 scripts/verify_strategy_tester_inputs.py `
  src/mql5/SmartTraderEA.mq5 `
  ops/mt5/SmartTraderEA.demo.set
```

این بررسی تضمین می‌کند `InpAllowLiveTrading=false` و `InpEnableZmq=false`
هستند. اجرای Strategy Tester همچنان باید با MetaEditor/MT5 روی همان terminal
انجام شود؛ API لینوکسی داخل Docker نباید جایگزین Strategy Tester شود.

فایل آماده اجرای baseline در
[ops/mt5/strategy-tester.demo.ini](../ops/mt5/strategy-tester.demo.ini) قرار
دارد. پس از compile شدن EA، فایل‌های `SmartTraderEA.ex5` و
`SmartTraderEA.demo.set` را در پوشه `MQL5\Experts\SmartTraderEA` همان terminal
قرار دهید و Strategy Tester را با همین config اجرا کنید. این config فقط بازه
تاریخی را تست می‌کند، ZeroMQ را خاموش نگه می‌دارد و live trading را مجاز نمی‌کند.

اجرای واقعی baseline در تاریخ 2026-09-13 با موفقیت انجام شد و artifactهای آن در
[ops/mt5/artifacts/20260913_strategy_tester](../ops/mt5/artifacts/20260913_strategy_tester)
ذخیره شده‌اند. به‌دلیل تفاوت نام نماد در حساب Demo، اجرای دوم با نماد واقعی
بروکر `XAUUSD_l` و فایل
[SmartTraderEA.demo.broker-symbol.set](../ops/mt5/SmartTraderEA.demo.broker-symbol.set)
انجام شد. این اجرای دوم نیز با `Test passed`، موجودی نهایی `10000.00 USD` و
`Total Trades=0` و `Total Deals=0` پایان یافت؛ artifactهای آن در
[ops/mt5/artifacts/20260913_strategy_tester_broker_symbol](../ops/mt5/artifacts/20260913_strategy_tester_broker_symbol)
قرار دارند. فایل baseline اصلی عمداً با whitelist `XAUUSD` تغییر نکرده است.

# قرارداد ذخیره‌سازی داده

این قرارداد برای لایهٔ `src/python/data/` است. پیاده‌سازی با SQLAlchemy 2 و
PostgreSQL 15 + TimescaleDB انجام شده و برای تست‌های محلی از SQLite/aiosqlite
پشتیبانی می‌کند. همهٔ زمان‌ها باید UTC و timezone-aware باشند.

## جداول اصلی

| جدول | کاربرد | کلید/ایندکس مهم |
|---|---|---|
| `symbols` | فهرست نمادهای MT5 | `name` |
| `ohlcv_data` | کندل‌های OHLCV | unique روی `symbol + timeframe + timestamp`؛ hypertable |
| `market_ticks` | tick خام شامل bid/ask/last | `symbol + timestamp + bid + ask`؛ hypertable |
| `account_snapshots` | snapshot حساب MT5، موجودی، equity، margin و payload خام حساب | `account_login + timestamp`؛ hypertable |
| `indicator_calculations` | محاسبات اندیکاتور و مقادیر خام | `symbol + timeframe + category + timestamp`؛ unique با `indicator_name` |
| `filter_evaluations` | نتیجهٔ فیلتر، signal و دلیل | `symbol + timeframe + category + timestamp`؛ unique با `filter_name` |
| `orders` | دستورهای ایجاد/لغو/اصلاح سفارش | `order_id` و `symbol + created_at` |
| `positions` | چرخهٔ عمر position باز/بسته | `position_id` و `symbol + opened_at` |
| `order_transitions` | تاریخچهٔ immutable تغییر وضعیت سفارش | `order_id + timestamp` |
| `position_transitions` | تاریخچهٔ immutable تغییر وضعیت position | `position_id + timestamp` |
| `trade_executions` | fill/deal واقعی MT5 | `execution_id` و `order_id` |
| `execution_control_state` | وضعیت durable و fail-closed کنترل اجرای هر scope حساب/نماد | `scope`؛ version-checked |
| `project_logs` | لاگ ساختاریافتهٔ پروژه | `created_at`, `level`, `component` |
| `ai_predictions` | مدل، نسخه، پیش‌بینی، confidence و features | `prediction_id`, `symbol + timestamp` |
| `trading_decisions` | تصمیم نهایی strategy/AI/risk | `decision_id`, `symbol + timestamp` |
| `system_versions` | نسخهٔ migration schema و model مستقر | `component`؛ شامل `schema` و `model` |

تعریف typed مدل‌ها در [models.py](../src/python/data/models.py) قرار دارد. مقادیر
provider-specific یا featureهای متغیر در ستون‌های JSON/JSONB با نام `payload`,
`values`, `features` و `metadata` نگهداری می‌شوند؛ فیلدهای مورد query در ستون
مجزا باقی می‌مانند.

### State machine چرخهٔ سفارش و position

تغییر `status` فقط از طریق متدهای transition در repository مجاز است. هر
transition معتبر یک رکورد immutable در جدول تاریخچه ثبت می‌کند که شامل
`from_status`، `to_status`، `actor`، timestamp UTC، `correlation_id` و raw
payload است. transition نامعتبر با خطای صریح رد می‌شود و entity تغییر نمی‌کند.

گراف سفارش: `pending -> accepted|rejected|cancelled|expired|unknown`،
`accepted -> partial|filled|rejected|cancelled|unknown`،
`partial -> partial|filled|cancelled|unknown` و
`unknown -> accepted|partial|filled|rejected|cancelled|expired`.
وضعیت‌های `filled`، `rejected`، `cancelled` و `expired` پایانی هستند.

گراف position: `open -> partial|closed` و
`partial -> partial|open|closed`؛ `closed` پایانی است.

### وضعیت durable کنترل execution

جدول `execution_control_state` برای هر scope پایدار حساب/نماد یک رکورد دارد.
ستون‌های پیاده‌سازی‌شده عبارت‌اند از:

- `scope` کلید اصلی متنی و غیرخالی (حداکثر ۲۵۵ کاراکتر).
- `emergency_stop` و `emergency_stop_reason` برای توقف اضطراری.
- `demo_active`، `session_expires_at` و `demo_trade_count` برای کنترل session
  و سقف شمارش demo.
- `demo_owner_approval` و `demo_second_approval` شناسهٔ دو approver مستقل،
  `demo_selected_symbols` scope نمادهای Market Watch، `demo_limits` محدودیت‌های
  فعال‌سازی و `demo_configuration_hash` هش canonical همین scope و محدودیت‌ها
  هستند. فعال‌سازی بدون همهٔ این مقادیر یا پس از expiry معتبر نیست.
- `daily_loss` به‌عنوان snapshot نامنفی زیان روزانه.
- `version` برای optimistic concurrency و `actor` برای ثبت عامل تغییر.
- `updated_at` به‌صورت UTC و timezone-aware.

رکوردهای جدید فقط با درخواست صریح provision می‌شوند؛ خواندن معمولی state
رکوردی ایجاد نمی‌کند. writeها در transaction صریح انجام می‌شوند و update فقط
با `expected_version` معتبر است؛ version قدیمی `ConcurrencyConflict` ایجاد
می‌کند. scope، actor، شمارنده، زیان و version قبل از write اعتبارسنجی می‌شوند.
مقدار ناموجود یا غیرقابل‌خواندن state برای اجرای live مجوز پیش‌فرض نیست:
workflow سفارش را fail-closed رد می‌کند. این جدول وضعیت broker، fill یا
confirmation token را جایگزین نمی‌کند.

زمان‌های persisted در این جدول با `UTCDateTime` به UTC normalize می‌شوند.
هیچ credential یا token در این state ذخیره نمی‌شود؛ confirmation یک‌بارمصرف
در حافظهٔ workflow مصرف می‌شود و replay آن مجاز نیست.

## قرارداد ingestion

### همگام‌سازی Market Watch داشبورد

هنگام اجرای local demo، collector فقط نمادهای visible در MT5 Market Watch را
در نظر می‌گیرد. اگر کاربر در داشبورد انتخاب ذخیره‌شده‌ای در
`data/dashboard_settings.json` داشته باشد، اشتراک مؤثر برابر تقاطع آن انتخاب با
نمادهای visible است؛ در غیر این صورت همهٔ نمادهای visible استفاده می‌شوند.
برای هر نماد و همهٔ تایم‌فریم‌های معتبر تنظیم‌شده، collector ابتدا حداکثر
۱۰٬۰۰۰ کندل را در فایل canonical
`market_data/{symbol}_{timeframe}.json` با upsert و حذف تکرار ذخیره می‌کند و
سپس هر بار فقط کندل‌های جدید یا در حال تشکیل را به‌روزرسانی می‌کند. نبود MT5 یا
دادهٔ نماد خطای صریح در `logs/market_watch_windows.*.log` ایجاد می‌کند و هرگز
دادهٔ ساختگی تولید نمی‌شود.

### مرحله A: baseline واقعی XAUUSD/M5

مسیر رسمی آماده‌سازی baseline از ترمینال محلی MT5 به‌صورت زیر است:

```powershell
$env:PYTHONPATH = "src\python"
py -3 -m data.main --stage-a --bars 30000
```

این مسیر فقط `XAUUSD` در تایم‌فریم `M5` را می‌پذیرد و حداقل ۳۰٬۰۰۰ کندل
منحصربه‌فرد را مطالبه می‌کند. پیش از هر write به دیتابیس، timestampها باید
UTC و به‌ترتیب زمانی باشند، OHLC باید عددی و سازگار (`low <= open/close <= high`)
باشد و حداقل یکی از `tick_volume` یا `volume` موجود و نامنفی باشد. کندل‌های
تکراری حذف و gapها گزارش می‌شوند. تعطیلی آخر هفته و وقفهٔ maintenance روزانهٔ
رایج broker به‌عنوان gap مورد انتظار گزارش می‌شوند اما باعث رد شدن Stage A
نمی‌شوند؛ gapهای غیرمنتظره همچنان کل عملیات را رد می‌کنند. هیچ gapی با کندل
مصنوعی پر نمی‌شود و دادهٔ ناقص وارد آموزش یا backtest نمی‌شود.
وقفه‌های کوتاه حداکثر ۱۰ دقیقه نیز فقط گزارش می‌شوند؛ این موارد از داده حذف یا
با مقدار مصنوعی جایگزین نمی‌شوند. وقفهٔ طولانی در ساعات فعال بازار همچنان خطای
مسدودکننده است.

در training gate، session break اختصاصی broker باید صریحاً با گزینهٔ
`--expected-gap-window HH:MM-HH:MM` یا `expected_gap_windows` در بخش
`data_quality` تنظیم شود. این بازه‌ها بر مبنای UTC هستند و می‌توانند از نیمه‌شب
عبور کنند؛ برای نمونه:

```powershell
py scripts\train_model.py --stage-b --symbol XAUUSD --timeframe M5 `
  --expected-gap-window 23:55-01:05
```

این تنظیم فقط همان پنجرهٔ اعلام‌شده را expected می‌کند. فاصله‌های خارج از آن،
حتی اگر در نزدیکی session break رخ دهند، همچنان rejection ایجاد می‌کنند.

### Training Gate و data manifest

آموزش فقط زمانی مجاز است که حداقل `min_samples` (پیش‌فرض ۱۰٬۰۰۰ کندل)،
حداقل پوشش زمانی `min_coverage_hours` (پیش‌فرض ۷۲۰ ساعت)، timestampهای
timezone-aware در UTC، OHLC معتبر و gapهای غیرمنتظره در محدودهٔ مجاز باشند.
در صورت رد شدن هر کدام از این شروط، artifact جدید ساخته نمی‌شود. عدم‌تعادل
کلاس‌ها با `class_imbalance_warning_threshold` (پیش‌فرض ۱۰٪) به‌صورت هشدار
ثبت می‌شود و در summary آموزش باقی می‌ماند تا مدل کم‌نماینده بدون اطلاع
استفاده نشود.

هر artifact مدل علاوه بر schema و dataset version، فیلد `data_manifest` را
ذخیره می‌کند. این manifest شامل نماد، timeframe، منبع، تعداد کندل، پوشش
درخواستی و واقعی، قدیمی‌ترین/جدیدترین timestamp، gapها، duplicate/invalid
diagnostics، نسخهٔ dataset و توزیع labelها است. Dashboard و سرویس inference
باید این manifest را فقط برای نمایش و governance بخوانند و نبودن یا نامعتبر
بودن آن را مجوزی برای دور زدن gate تلقی نکنند.

### کندل

```json
{
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "timestamp": "2026-09-04T20:17:55Z",
  "open": 2500.1,
  "high": 2501.0,
  "low": 2499.8,
  "close": 2500.7,
  "tick_volume": 1234,
  "volume": 0,
  "spread": 12,
  "source": "mt5",
  "ingestion_metadata": {
    "ingestion_id": "uuid",
    "received_at": "2026-09-04T20:18:00Z",
    "collector_version": "string"
  },
  "payload": {}
}
```

`timestamp` و `received_at` همیشه timezone-aware و UTC هستند. `source` مقدار
غیرخالی و قابل ردیابی مانند `mt5`, `mt5_official` یا `replay` است. خروجی
رسمی CSV/Export ترمینال MT5 باید با adapter
`src/python/data/historical_loader.py` خوانده شود تا نام ستون‌ها، timestamp،
حجم‌ها و timezone به قرارداد canonical تبدیل شوند. این adapter بدون اتصال
زنده به ترمینال کار می‌کند و برای هر رکورد `ingestion_id` و زمان دریافت ثبت
می‌کند.

### Backfill رسمی MT5 و کنترل overlap

برای backfill تاریخی، منبع رسمی export ترمینال یا API همان بروکر با مقدار
`source="mt5_official"` ذخیره می‌شود و نباید به‌صورت بی‌قیدوشرط با داده‌های
provider دیگر ادغام شود. پیش از persistence، رکوردها بر اساس
`symbol + timeframe + timestamp` مرتب و deduplicate می‌شوند و OHLCV، timezone و
gapها اعتبارسنجی می‌گردند. اگر timestamp مشترک با دادهٔ موجود وجود داشته باشد،
`validate_overlap` مقادیر open/high/low/close را با tolerance مشخص مقایسه
می‌کند؛ هر اختلافی عملیات را fail-closed متوقف می‌کند و هیچ رکورد متناقضی
جایگزین نمی‌شود.

نمونهٔ ingestion آفلاین:

```python
result = await loader.load_export_file(
    r"exports\XAUUSD_M5.csv",
    symbol="XAUUSD",
    timeframe="M5",
    fail_on_gaps=True,
)
```

`mt5_official` فقط provenance است و به‌تنهایی مجوز آموزش یا live trading نیست.
آموزش باید source انتخاب‌شده را صریحاً در manifest ثبت کند، و live inference
باید همچنان از stream زندهٔ MT5 با قرارداد symbol/timeframe سازگار استفاده
کند. هیچ candle مصنوعی برای پر کردن gap ساخته نمی‌شود.
`ingestion_metadata` برای provenance و وضعیت دریافت است و جایگزین فیلدهای
قابل‌جست‌وجوی candle نمی‌شود. کلید طبیعی جدول در سطح دیتابیس enforce می‌شود؛
ورودی‌های تکراری باید پیش از write گزارش یا deduplicate شوند، نه اینکه با
کندل مصنوعی جایگزین شوند. validatorهای `validate_ohlcv_rows` و `validate_gaps`
در `src/python/data/validators.py` برای این کنترل‌ها استفاده می‌شوند.

### اندیکاتور و فیلتر

```json
{
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "timestamp": "2026-09-04T20:17:55Z",
  "indicator_name": "rsi",
  "category": "momentum",
  "value": 57.2,
  "values": {"period": 14}
}
```

```json
{
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "timestamp": "2026-09-04T20:17:55Z",
  "filter_name": "trend",
  "category": "trend",
  "passed": true,
  "signal": "buy",
  "reason": "strong_uptrend",
  "metadata": {}
}
```

### سفارش، اجرا و position

### Stage E: Demo Shadow Mode

در حالت `execution_mode=shadow`، هر signal پذیرفته‌شده با قیمت واقعی همان
چرخه به‌عنوان `would_be_filled` در ledger فایل‌محور append-only ثبت می‌شود.
این مسیر هیچ connector یا `order_send` مربوط به MT5 را فراخوانی نمی‌کند. هنگام
رسیدن قیمت واقعی بعدی که SL/TP یا خروج را فعال کند، یک رویداد `closed` به همان
`order_id` اضافه می‌شود و `pnl`, `exit_price`, `exit_reason` و commission را
ثبت می‌کند. مسیر ledger از `SHADOW_LEDGER_PATH` خوانده می‌شود و باید روی
storage پایدار و با دسترسی محدود اجرا شود. رکوردهای shadow، سفارش واقعی یا fill
MT5 محسوب نمی‌شوند و برای فعال‌سازی live نیازمند gateهای مستقل هستند.

- `orders` دستور intent و وضعیت چرخهٔ سفارش را نگه می‌دارد؛ `order_id` باید از
  سیستم تولیدکننده یکتا باشد. `idempotency_key` برای جلوگیری از ارسال تکراری
  و `broker_order_id`/`broker_deal_id` برای شواهد reconciliation تاریخچهٔ MT5
  ذخیره می‌شوند.
- `trade_executions` هر fill/deal را جدا ثبت می‌کند و برای audit حذف نمی‌شود.
- `positions` وضعیت تجمیعی position را نگه می‌دارد و می‌تواند به `order_id` و
  `execution_id` مرتبط باشد.
- وضعیت‌های سفارش: `pending`, `accepted`, `partial`, `filled`, `cancelled`,
  `rejected`, `expired`, `unknown`. وضعیت `unknown` فقط با شواهد یکتای MT5 یا
  تصمیم operator قابل تغییر است و هرگز با retry خودکار حل نمی‌شود.

### پیش‌بینی و تصمیم

`ai_predictions` باید `model_name`, `model_version`, `prediction`,
`confidence`, `features` و `metadata` را ذخیره کند. `trading_decisions` خروجی
نهایی را با `action` (`buy`, `sell`, `hold`, `close`, `no_action`)، منبع تصمیم،
امتیاز risk و دلایل رد/قبول نگه می‌دارد. پیش‌بینی به‌تنهایی مجوز ارسال سفارش
نیست؛ تصمیم risk باید جدا ثبت و قبل از execution بررسی شود.

جدول `system_versions` در هر initialization موفق، نسخهٔ migration جاری را با
`component=schema` و نسخهٔ مدل مستقر را با `component=model` ثبت می‌کند. این
رکوردها برای audit و تشخیص ناسازگاری deployment هستند و جایگزین checksum
artifact مدل یا migration history نمی‌شوند.

## قرارداد Stage D: Walk-Forward Backtest

موتور `src/python/backtest` همین قرارداد OHLCV را مصرف می‌کند و پیش از شبیه‌سازی
هر کندل را اعتبارسنجی می‌کند. برای هر `(symbol, timeframe)`، timestampها باید
منحصربه‌فرد و صعودی باشند و شرط `low <= open/close <= high` را رعایت کنند؛
دادهٔ نامعتبر نباید برای آموزش، ارزیابی یا گزارش عملکرد اصلاح خاموش شود.

`BacktestEngine.walk_forward` داده را به پنجرهٔ آموزش داخل‌نمونه و پنجرهٔ
آزمون خارج‌نمونه تقسیم می‌کند. مدل یا strategy هر fold فقط با پنجرهٔ آموزش
همان fold fit می‌شود و پنجرهٔ آزمون به‌صورت جداگانه اجرا می‌شود تا نشت آینده
جلوگیری شود. `train_window`، `test_window` و `step` می‌توانند تعداد کندل یا
`timedelta` باشند. خروجی شامل `folds`، بازه‌های زمانی آموزش/آزمون و
`out_of_sample` تجمیعی است.

پارامترهای اجرای واقع‌گرایانه در `BacktestConfig` تعریف می‌شوند:

- `spread` به‌صورت absolute یا rate با `spread_is_rate`
- `slippage` به‌صورت absolute یا rate با `slippage_is_rate`
- `commission` به‌عنوان fraction از notional هر fill
- `max_fill_ratio` و `Bar.volume` برای شبیه‌سازی partial fill

هزینه‌ها در ورود و خروج هر position اعمال می‌شوند و در `Trade.commission` و
`Fill.commission` قابل audit هستند. مقادیر پیش‌فرض هزینه‌ها فرض بازار واقعی
خاصی را تضمین نمی‌کنند؛ پیش از نتیجه‌گیری معاملاتی باید با spread، commission،
slippage و تقویم معاملاتی broker واقعی پیکربندی شوند.

## تراکنش و idempotency

- تمام متدهای write در `DatabaseRepository` داخل `session.begin()` اجرا می‌شوند.
- `claim_order_intent`، درج `IdempotencyRecord(status=in_progress)` و درج
  `TradingOrder(status=pending)` و transition اولیه را در یک transaction انجام
  می‌دهد. unique constraint روی idempotency key فقط یک مالک را برای درخواست‌های
  هم‌زمان باقی می‌گذارد؛ مالک دوم `IdempotencyInProgress` می‌گیرد و نباید به MT5
  برسد.
- نبودن نتیجه از `order_send` به‌عنوان rejection قطعی تفسیر نمی‌شود. order در
  وضعیت `unknown` و رکورد idempotency با وضعیت `unknown` ذخیره می‌شود تا
  reconciliation شواهد MT5 را بررسی کند؛ retry خودکار مجاز نیست.
- پاسخ accepted شناسه‌های broker order/deal و retcode را در intent ذخیره می‌کند؛
  پاسخ rejected نیز retcode و دلیل را به‌صورت durable ثبت می‌کند. هر دو وضعیت
  terminal هستند و replay همان idempotency key هرگز `order_send` جدیدی ایجاد
  نمی‌کند.
- در startup، `recover_pending_order_intents` intentهای `pending` باقی‌مانده از
  اجرای قطع‌شده را با transition audit از actor=`startup_recovery` به
  `unknown` می‌برد و پاسخ idempotency را با
  `reason=execution_interrupted` تکمیل می‌کند. این عملیات idempotent و
  بدون تماس یا retry در MT5 است.
- ingestion کندل و tick با کلید طبیعی upsert/deduplicate می‌شود.
- همگام‌سازی حساب فقط خواندنی است و snapshot حساب، پوزیشن‌های باز، سفارش‌های فعال
  و dealهای تاریخی را به‌ترتیب در `account_snapshots`، `positions`، `orders` و
  `trade_executions` ذخیره می‌کند؛ payload خام MT5 برای audit نگه‌داری می‌شود.
- خطای یک batch باید transaction همان write را rollback کند؛ نتیجهٔ موفقیت
  نباید بدون commit به caller برگردد.
- برای چرخهٔ execution، `record_order_execution(order, execution)` intent سفارش
  و fill را در یک transaction ثبت می‌کند. `order_id` باید در هر دو payload یکسان
  باشد و `execution_id` کلید idempotency برای retry/reconnect است؛ بنابراین fill
  بدون parent order قابل مشاهده نمی‌شود و تکرار همان fill رکورد دوم ایجاد نمی‌کند.
- جداول زمانی `ohlcv_data`, `market_ticks` و `account_snapshots` با
  `ensure_timescale_hypertables` به‌صورت idempotent به hypertable تبدیل می‌شوند.
  این عملیات فقط روی PostgreSQL/TimescaleDB معتبر است و روی SQLite باید خطای
  واضح بدهد.

## پیکربندی

### استقرار محلی PostgreSQL/TimescaleDB

فایل `.env` را از `.env.example` بسازید و مقدار `POSTGRES_PASSWORD` را تغییر
دهید. سپس دیتابیس را اجرا کنید:

```powershell
Copy-Item .env.example .env
docker compose up -d timescaledb
```

Collector روی Windows و در کنار ترمینال MT5 اجرا می‌شود و به
`localhost:5432` متصل خواهد شد. سرویس آموزش داخل Compose با profile جداگانه
اجرا می‌شود تا به‌صورت تصادفی با شروع دیتابیس، آموزش یا معامله آغاز نشود:

```powershell
docker compose --profile training run --rm ml-training
```

اجرای collector، دریافت داده و آموزش مدل هیچ سفارش معاملاتی ارسال نمی‌کند.

نمونهٔ URL:

```text
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/mt5_data
```

مقادیر login/password/server متاتریدر فقط از environment یا `.env` محلی خوانده
می‌شوند و نباید در source control قرار گیرند. تنظیمات در
[config.py](../src/python/data/config.py) تعریف شده‌اند.

## قرارداد feature vector برای ML

inference مدل در checkout فعلی فقط از artifactهای محلی آموزش‌دیده با دادهٔ واقعی
MT5 استفاده می‌کند. هر artifact باید علاوه بر `model` و `feature_names`، فیلدهای
`schema_version`, `symbol` و `timeframe` را داشته باشد؛ سرویس inference در صورت
ناهماهنگی نماد یا تایم‌فریم fail-closed می‌کند. دادهٔ ناقص، missing یا synthetic
نباید با zero-fill یا fallback وارد feature vector شود.

```json
{
  "schema_version": "1.0",
  "symbol": "XAUUSD",
  "timeframe": "M5",
  "timestamp": "2026-09-04T20:17:55Z",
  "feature_names": ["close", "rsi_14", "atr_14", "trend_passed"],
  "values": [2500.7, 57.2, 4.1, 1.0],
  "dtypes": ["float64", "float64", "float64", "float64"],
  "normalization": {"method": "none", "fit_id": null},
  "source": {"candle_revision": 1, "indicator_revision": 1}
}
```

قواعد: `feature_names[i]` با `values[i]` و `dtypes[i]` هم‌ردیف است؛ ترتیب
featureها بخشی از قرارداد است؛ مقدار missing باید صریحاً `null` باشد و با zero
جایگزین نشود؛ timestamp UTC و timezone-aware است؛ training و inference باید
`schema_version` و `fit_id` یکسان/قابل‌ردیابی داشته باشند.

## قرارداد ZeroMQ و خطاهای gateway

gateway فعلی Python پیام‌های JSON UTF-8 را با envelope یکنواخت زیر به EA
ارسال می‌کند و payload سفارش هرگز بدون gate ریسک قابل‌ارسال نیست:

```json
{
  "schema_version": "1.0",
  "message_id": "uuid",
  "message_type": "market_snapshot|signal|order_request|order_result|heartbeat",
  "sent_at": "2026-09-04T20:17:55.123Z",
  "correlation_id": "uuid",
  "source": "python|mt5",
  "payload": {}
}
```

`message_id` برای deduplication، `correlation_id` برای trace و
`schema_version` برای evolution اجباری‌اند. پاسخ خارج از schema reject می‌شود
و خطای آن بدون secret log می‌شود. خطاهای `zmq.error.Again` و `TimeoutError`
با code=`timeout`، خطای `zmq.error.ZMQError` با code=`transport` و پاسخ
نامعتبر با code=`protocol` به caller می‌رسند. فقط transport socket را reset
می‌کند؛ timeout، protocol و unexpected باعث retry یا ارسال دوباره نمی‌شوند.
unexpected پس از log دوباره پرتاب می‌شود. timeout نتیجهٔ سفارش را `unknown`
نگه می‌دارد تا reconciliation مستقل انجام شود.

## قاعدهٔ سازگاری مسیرها

در migration، نام جدول، ستون، JSON field و فایل داده تغییر نمی‌کند. wrapperهای
قدیمی فقط import را به packageٔ canonical ترجمه می‌کنند. هر تغییر آینده در
schema باید additive و versioned باشد و نمونهٔ قبل/بعد، migration و rollback
را هم‌زمان مستند کند.

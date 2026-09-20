# قرارداد API و مرزهای ارتباطی

> وضعیت فعلی: `GET /health`، `GET /api/v1/data/ohlc`،
> `POST /api/v1/signals/{signal_id}/execute`، `GET /api/v1/orders/unknown`،
> `POST /api/v1/orders/{order_id}/reconcile` و
> `POST /api/v1/orders/reconcile/automatic` در FastAPI فعال هستند.
> سایر endpointهای این سند قرارداد هدف/برنامه‌ریزی‌شده‌اند و تا زمان پیاده‌سازی
> نباید به‌عنوان قابلیت موجود مصرف شوند.
>
> endpointهای حفاظت‌شده در production بدون credential معتبر fail-closed هستند.
> `/health` عمومی است و `/ready` فقط وضعیت dependencyها را گزارش می‌کند و مجوز
> معامله صادر نمی‌کند.

## invariantهای lifecycle

تغییر وضعیت order و position فقط از طریق State Machine رسمی انجام می‌شود.
هر transition به actor غیرخالی، timestamp UTC، correlation ID و payload نیاز
دارد و در جدول تاریخچهٔ immutable ثبت می‌شود. transition خارج از گراف رسمی با
خطای صریح رد می‌شود. reconciliation بروکر نیز همین مسیر را استفاده می‌کند و
actor آن `broker_reconciliation` است.

## اصول عمومی

- base path: `/api/v1`
- JSON و UTF-8؛ زمان‌ها ISO-8601 UTC و timezone-aware.
- پاسخ موفق: `{ "data": ..., "request_id": "uuid" }`
- پاسخ‌ها و headerهای HTTP علاوه بر `request_id`، یک `correlation_id` برای
  ردیابی همان درخواست در لاگ‌ها و مرزهای ارتباطی ارائه می‌کنند.
- پاسخ خطا: `{ "error": { "code": "...", "message": "...", "details": {} }, "request_id": "uuid" }`
- `request_id` برای تمام پاسخ‌ها و لاگ‌ها اجباری است.
- endpointهای write باید idempotency key داشته باشند.
- credential، token و مقدار password در response یا log برگردانده نمی‌شود.

### `GET /metrics`

این endpoint داخلی، بدون ورود به schema عمومی OpenAPI، metrics عملیاتی را در
قالب Prometheus text exposition برمی‌گرداند. داده‌ها در هر process نگهداری
می‌شوند و برای aggregation چند replica باید از collector مستقل استفاده شود.
این endpoint نباید از شبکهٔ عمومی منتشر شود.
در صورت تنظیم `OBS_METRICS_TOKEN`، header `X-API-Key` نیز برای دسترسی لازم است.

### `GET /ready`

این endpoint readiness وابستگی دیتابیس را با اجرای query سبک `SELECT 1` بررسی
می‌کند. در صورت آماده نبودن دیتابیس، پاسخ `503` با `database_unavailable`
برگردانده می‌شود. `/health` فقط زنده بودن process را نشان می‌دهد و جایگزین
`/ready` نیست. در صورت تزریق/فعال بودن اتصال MT5، وضعیت آن با health check
بدون ارسال سفارش گزارش می‌شود. وضعیت Circuit Breaker نیز فقط به‌عنوان
وضعیت معامله‌پذیری گزارش می‌شود؛ tripped بودن breaker باعث اجرای هیچ سفارش
جدیدی نمی‌شود و به‌تنهایی به معنی ازکارافتادن process نیست.

Metricهای پایه:

- `http_requests_total{method,path}`
- `http_5xx_total`
- `http_request_duration_seconds_count`
- `http_request_duration_seconds_sum`

عبور `http_5xx_total` از `OBS_ALERT_HTTP_5XX_THRESHOLD` یک event عملیاتی
`alert_triggered` تولید می‌کند. این alert جایگزین circuit breaker معاملاتی
نیست و به‌تنهایی مجوز reset یا ارسال سفارش محسوب نمی‌شود.
- هیچ prediction یا signal به‌تنهایی order نیست؛ execution فقط بعد از risk gate
  و تأیید MT5 انجام می‌شود.

## Health

`GET /health` پاسخ وضعیت سرویس را همراه با `schema_version` و `model_version`
برمی‌گرداند. این نسخه‌ها برای تشخیص ناسازگاری deployment هستند و به‌معنای
اعتبارسنجی کیفیت مدل یا مجوز معامله نیستند.

## Data

### `GET /api/v1/data/ohlc`

پارامترهای query:

| نام | نوع | اجباری | توضیح |
|---|---|---:|---|
| `symbol` | string | بله | نماد معتبر |
| `timeframe` | enum | بله | `M1` تا `MN1` |
| `start`, `end` | datetime | خیر | بازهٔ UTC، حداکثر محدودیت server |
| `limit` | int | خیر | پیش‌فرض 1000، حداکثر 10000 |

`200`:

```json
{
  "data": {
    "symbol": "XAUUSD", "timeframe": "M5",
    "candles": [{"timestamp":"2026-09-04T20:15:00Z","open":2500.1,
      "high":2501.0,"low":2499.8,"close":2500.7,
      "tick_volume":1234,"volume":0,"spread":12}]
  },
  "request_id": "uuid"
}
```

### `POST /data/subscribe` — [برنامه‌ریزی‌شده]

برای subscription بازار، body شامل `symbols`, `timeframes` و `consumer_id` است.
پاسخ `202` شامل `subscription_id` و وضعیت `active|rejected` است. disconnect
باید subscription را به `degraded` ببرد و retry با backoff انجام شود.

## Indicators و filters

### `POST /indicators/calculate` — [برنامه‌ریزی‌شده]

```json
{
  "symbol": "XAUUSD", "timeframe": "M5",
  "indicators": [{"name":"rsi","parameters":{"period":14}}],
  "candles": []
}
```

پاسخ شامل `schema_version`, `timestamp`, و `values` است. محاسبه باید pure
باشد و side effect سفارش نداشته باشد. `400` برای پارامتر نامعتبر و `422` برای
دادهٔ ناقص استفاده می‌شود.

### `POST /filters/evaluate` — [برنامه‌ریزی‌شده]

ورودی شامل candleها و filterهای نام‌گذاری‌شده است؛ خروجی هر filter باید
`passed`, `signal`, `reason` و `metadata` داشته باشد. خروجی aggregate جایگزین
audit جزئیات filterها نمی‌شود.

## ML

### `POST /ml/predict` — [برنامه‌ریزی‌شده]

ورودی: `model_name`, `model_version`, و feature vector مطابق
[DATA_CONTRACT.md](DATA_CONTRACT.md). پاسخ شامل `prediction`, `confidence`,
`features_hash` و `model_version` است. confidence مجوز معامله نیست.

### `POST /ml/train` — [برنامه‌ریزی‌شده]

فقط job را ایجاد می‌کند (`202`) و `job_id` برمی‌گرداند؛ training طولانی در
request thread انجام نمی‌شود. dataset، time split، seed و artifact checksum
باید audit شوند.

## Backtest

### `POST /backtest/run` — [برنامه‌ریزی‌شده]

ورودی: symbol/timeframe، بازه، strategy version، هزینه/اسپرد، seed و
`dry_run=true`. اجرای backtest هرگز به terminal زنده یا `order_send` وصل
نمی‌شود. پاسخ `202` شامل `run_id` است.

### `GET /backtest/{run_id}/report` — [برنامه‌ریزی‌شده]

گزارش immutable شامل equity curve، trade list، drawdown، fees، assumptions و
code/model version است. نبود داده یا mismatch schema باید خطای قابل‌تشخیص
بدهد، نه نتیجهٔ صفر.

## Signal و execution

### احراز هویت و نقش‌ها

برای ارتباطات داخلی، روش ترجیحی `Authorization: Bearer <token>` است. توکن
با HMAC-SHA256 و `API_AUTH_JWT_SECRET` امضا می‌شود، `iss` آن باید برابر
`API_AUTH_JWT_ISSUER` باشد و عمر آن با `API_AUTH_TOKEN_TTL_SECONDS` (بین ۳۰
ثانیه و یک ساعت، مقدار پیش‌فرض ۵ دقیقه) محدود می‌شود. ادعاهای `sub`، `iat`,
`exp`، `jti` و `roles` باید وجود داشته باشند.

نقش‌های مجاز:

- `read_only`: خواندن OHLC
- `order_review`: مشاهدهٔ سفارش‌های با نتیجهٔ نامشخص
- `signal_execution`: اجرای signal پس از عبور از همهٔ gateهای ریسک
- `emergency_stop`: رزرو برای توقف/بازنشانی اضطراری
- `model_management`: رزرو برای مدیریت مدل

کلیدهای ثابت `API_AUTH_READ_TOKEN`، `API_AUTH_EXECUTION_TOKEN`,
`API_AUTH_OPERATOR_TOKEN` و `API_AUTH_MODEL_TOKEN` فقط برای compatibility
داخلی هستند و باید جداگانه rotate شوند. `API_AUTH_TOKEN` قدیمی تنها نقش‌های
`read_only` و `signal_execution` را دارد و برای نصب جدید توصیه نمی‌شود.
داشتن نقش `read_only` هرگز مجوز اجرای live نیست؛ endpoint اجرای زنده فقط
`signal_execution` را می‌پذیرد.

مجوز routeهای فعال:

| route | نقش لازم | رفتار بدون credential یا با نقش نامناسب |
|---|---|---|
| `GET /api/v1/data/ohlc` | `read_only` | `401` یا `403` |
| `GET /api/v1/orders/unknown` | `order_review` | `401` یا `403` |
| `POST /api/v1/signals/{signal_id}/execute` در حالت live | `signal_execution` | `401` یا `403` |
| routeهای reconciliation | `emergency_stop` | `401` یا `403` |

در production، credentialهای routeهای حفاظت‌شده باید پیکربندی شده باشند؛ نبود
پیکربندی به fallback ناشناس تبدیل نمی‌شود. گزینهٔ `allow_anonymous` فقط برای
factoryهای تست ایزوله و به‌صورت صریح قابل فعال‌سازی است.
`API_AUTH_ORDER_REVIEW_TOKEN` فقط نقش `order_review` دارد و مجوز execution یا
reconciliation نیست.

### `GET /signals/pending` — [برنامه‌ریزی‌شده]

فقط signalهایی را برمی‌گرداند که `status=pending` و expiration آن‌ها نگذشته
است. هر signal شامل `signal_id`, `symbol`, `action`, `entry`, `stop_loss`,
`take_profit`, `risk_score`, `created_at`, `expires_at` و `reason` است.

### `POST /api/v1/signals/{signal_id}/execute`

این endpoint در فاز اول باید `dry_run` پیش‌فرض داشته باشد. قبل از execution:

1. idempotency و freshness بررسی شود.
2. symbol whitelist، حجم، margin، magic number و حداقل فاصلهٔ SL/TP بررسی شود.
3. circuit breaker و daily-loss gate اجازه دهند.
4. intent در `orders` ثبت و سپس تنها مرز مجاز، یعنی `LiveOrderWorkflow`،
   برای فراخوانی adapter MT5 استفاده شود.
5. نتیجهٔ هر fill در `trade_executions` ثبت شود.
6. `X-Correlation-ID` درخواست باید بدون تغییر در intent، تمام transitionهای
   durable، audit eventهای workflow و envelope ارتباطی Python↔EA حفظ شود؛ اگر
   correlation از caller ارائه نشود، workflow باید یک مقدار جدید تولید کند.

در خطای هر gate، پاسخ `409` با `reason_code` برگردد و هیچ orderی ارسال نشود.

فعال‌سازی Demo route عمومی جدیدی ندارد. این transition فقط از طریق
`LiveOrderWorkflow.activate_demo` و لایهٔ کنترل durable انجام می‌شود و به
تأیید دستی owner و یک approver مستقل، expiry آینده، scope غیرخالیِ زیرمجموعهٔ
نمادهای مجاز، limits و configuration hash منطبق نیاز دارد. بنابراین API
فقط همان health/readiness، execution و reconciliation surface موجود را
ارائه می‌کند؛ هیچ مسیر فعال‌سازی ناقص یا Live execution اضافه نشده است.

در پیاده‌سازی فعلی، `dry_run` به‌صورت پیش‌فرض `true` است. برای ارسال زنده باید
`dry_run=false`، `Idempotency-Key` یکتا، کلید `X-API-Key` با
`API_AUTH_TOKEN` پیکربندی‌شده، و یک `confirmation_token` صادرشده توسط
`LiveOrderWorkflow` ارائه شود. اجرای زنده مستقیماً به gateway واگذار نمی‌شود و
باید از گردش‌کار تأییدشده و gateهای ریسک عبور کند؛ magic از تنظیمات سرور
گرفته می‌شود و مقدار کلاینت قابل اعتماد نیست. signalهای دارای `expires_at`
منقضی‌شده یا timestamp آینده رد می‌شوند. envelope ارتباط Python و EA دارای
`schema_version=1.0`، `source=python`، `message_id`, `correlation_id` و
`message_type=signal|order_request|heartbeat` است؛ پاسخ EA با
`schema_version=1.0` و `source=mt5` برمی‌گردد.

`mt5_trade_orders.py` فقط یک سطح compatibility/dry-run است و entry point عمومی
برای live order نیست؛ فراخوانی‌های non-dry-run آن پیش از هر تعامل با MT5 رد
می‌شوند. هیچ application code نباید مستقیماً `order_send` را فراخوانی کند.
تنها محل ارسال broker در `LiveOrderWorkflow` است و نتیجه‌های accepted، rejected،
order-check failure و broker failure با correlation ID audit می‌شوند.

### `GET /api/v1/orders/unknown`

فهرست سفارش‌هایی را برمی‌گرداند که نتیجهٔ ارسال خارجی آن‌ها قطعی نشده است.
این endpoint فقط خواندنی است و فقط با نقش `order_review` قابل استفاده است.
پارامتر
`limit` بین ۱ و ۱۰۰۰ است. هر مورد شامل `order_id`, `idempotency_key`, `symbol`,
`side`, `quantity`, `status=unknown` و `updated_at` است.

### `POST /api/v1/orders/{order_id}/reconcile`

این endpoint فقط برای نقش `emergency_stop` در نظر گرفته شده و هرگز سفارش جدیدی
به MT5 ارسال نمی‌کند. body باید یکی از تصمیم‌های operator را همراه با شواهد
ثبت‌شده ارائه کند:

```json
{"resolution":"accepted","evidence":{"broker_ticket":42}}
```

`resolution` فقط `accepted` یا `rejected` است. سفارش باید در وضعیت `unknown`
باشد؛ در غیر این صورت `409 order_not_reconcilable` برگردانده می‌شود. پس از
موفقیت، وضعیت سفارش و رکورد idempotency در یک تراکنش به‌روزرسانی می‌شوند و
پاسخ idempotent قابل replay خواهد بود.

### `POST /api/v1/orders/reconcile/automatic`

این endpoint فقط برای نقش `emergency_stop` فعال است و از `history_orders_get`
و `history_deals_get` در MT5 به‌صورت read-only استفاده می‌کند. برای هر سفارش
`unknown`، نماد، حجم، magic number، بازهٔ زمانی و hint موجود در comment بررسی
می‌شود. فقط یک تطبیق یکتا resolve می‌شود؛ نبود تطبیق یا چند تطبیق، سفارش را
همچنان `unknown` نگه می‌دارد. این endpoint هرگز `order_send` یا retry انجام
نمی‌دهد. شناسه‌های `broker_order_id` و `broker_deal_id` و شواهد تطبیق در
رکورد سفارش ذخیره می‌شوند.

### رفتار idempotency در اجرای سفارش

`Idempotency-Key` برای هر درخواست live در جدول `idempotency_records` با یک
fingerprint از کل payload ثبت می‌شود. claim idempotency و ثبت `pending` order
intent در یک transaction اتمیک انجام می‌شوند؛
ثبت claim با کلید primary key اتمیک است؛
بنابراین درخواست‌های هم‌زمان با یک کلید نمی‌توانند بیش از یک بار به
`LiveOrderWorkflow` برسند. استفادهٔ دوباره از کلید با payload متفاوت با
`409 idempotency_key_reuse` و درخواست هم‌زمان با `409 idempotency_in_progress`
رد می‌شود.

پاسخ موفق در دیتابیس با وضعیت `completed` ذخیره می‌شود و پس از restart یا
تعویض replica همان پاسخ قبلی برگردانده می‌شود. رد قطعی broker با وضعیت
`rejected`، retcode و شواهد broker ذخیره می‌شود و تکرار همان idempotency key
هرگز دوباره به MT5 ارسال نمی‌شود. خطاهای اعتبارسنجی قبل از ارتباط با MT5
claim را آزاد می‌کنند، اما نبودن پاسخ `order_send`، timeout، پاسخ malformed یا
خطای نامشخص در ارتباط با adapter با وضعیت `unknown` ثبت می‌شود تا از ارسال
تکراری و بالقوهٔ خطرناک جلوگیری شود. وضعیت `unknown` باید با reconciliation
مستقل MT5 بررسی شود و به‌صورت خودکار retry نشود.

### کنترل durable اجرای زنده

`LiveOrderWorkflow` پیش از هر مسیر سفارش زنده، وضعیت durable اجرای scope مربوط
به حساب/نماد را از repository بارگذاری می‌کند. نبودن رکورد، خطای database،
خطای خواندن یا ناسازگاری version همگی سفارش را رد می‌کنند و هیچ تعامل broker
انجام نمی‌شود. وضعیت emergency stop، فعال‌سازی demo، انقضای session، شمارندهٔ
معاملات demo و snapshot زیان روزانه در همین مسیر ذخیره و پیش از تصمیم بعدی
دوباره بارگذاری می‌شوند؛ restart نباید stop را پاک یا demo را خودکار فعال کند.
تغییرات با transaction و version check انجام می‌شوند و تعارض باقی‌مانده
fail-closed است.

### خطاهای gateway و ZeroMQ

مرز ZeroMQ خطاها را به `timeout`، `transport` و `protocol` طبقه‌بندی می‌کند.
timeout (`zmq.error.Again` یا `TimeoutError`) نتیجهٔ سفارش را نامشخص نگه
می‌دارد و retry خودکار ندارد. خطای transport از نوع `zmq.error.ZMQError`
socket را reset می‌کند، اما درخواست دوباره ارسال نمی‌شود. پاسخ نامعتبر یا
schema نادرست `protocol` است و socket را صرفاً به‌دلیل خطای protocol reset
نمی‌کند. خطای unexpected پس از log دوباره پرتاب می‌شود و به
`gateway_unavailable` یا خطای broker تبدیل نمی‌شود. هیچ‌یک از این مسیرها
نتیجهٔ accepted تولید نمی‌کند.

در startup، intentهایی که پس از crash در وضعیت `pending` و claim متناظر در
وضعیت `in_progress` باقی مانده‌اند، در یک transaction به `unknown` منتقل
می‌شوند و پاسخ durable با reason=`execution_interrupted` ثبت می‌شود. این
بازیابی عمداً سفارش را دوباره ارسال نمی‌کند؛ operator یا reconciliation
read-only باید نتیجهٔ MT5 را تعیین کند.

مسیرهای CLI قدیمی (`mt5_trade_orders.py` و `mt5_manage_orders.py`) در محیط
runtime غیرفعال و در حالت live fail-closed هستند؛ این فایل‌ها نقطهٔ مجاز ارسال
سفارش نیستند و هر مصرف‌کنندهٔ جدید باید مستقیماً از workflow مرکزی استفاده کند.

مقدار قراردادی پیش‌فرض magic number برابر `26090901` است و باید بین
`MT5_LIVE_MAGIC` در Python و `InpMagicNumber` در EA یکسان بماند. تغییر این مقدار
باید هم‌زمان در هر دو سمت و فقط پس از پاک‌سازی/بررسی positionهای قبلی انجام شود.

## Risk

### `GET /risk/portfolio` — [برنامه‌ریزی‌شده]

نمایش read-only از balance، equity، margin، exposure، open positions و
`risk_state` است؛ credential یا token حذف می‌شود.

### `POST /risk/circuit-breaker` — [برنامه‌ریزی‌شده]

body:

```json
{"action":"trip|reset","reason":"operator_reason","confirmation":"uuid"}
```

`reset` نیازمند احراز هویت operator، audit log و confirmation مستقل است.
`trip` باید fail-closed باشد. reset نباید limitهای پیکربندی‌شده را تغییر دهد.

## HTTP status و observability

| status | کاربرد |
|---:|---|
| 200/202 | read یا قبول asynchronous job |
| 400/422 | قرارداد یا validation نامعتبر |
| 401/403 | احراز هویت/مجوز |
| 404 | resource ناشناخته |
| 409 | state conflict یا risk gate |
| 429 | rate limit |
| 500/503 | خطای داخلی یا MT5/database unavailable |

## startup و migration

startup production ابتدا به migrationهای Alembic و سپس به verifier غیرمخرب
schema تکیه می‌کند. verifier باید migration head و جدول
`execution_control_state` با کلید و ستون‌های لازم را تأیید کند؛ در صورت mismatch
یا نبودن state لازم، startup یا execution fail-closed است. `create_all` فقط در
مسیر صریح test/local برای SQLite و setup موقت مجاز است و بخشی از lifespan
production نیست.

metrics حداقل شامل latency، error rate، reconnect، rejected risk gates،
duplicate message و order outcome است. logها structured و بدون credential
هستند.

# ارزیابی پروژه و اولویت‌های ایمنی اجرا

> تاریخ ارزیابی: 2026-09-18
>
> این ارزیابی رفتار پیاده‌سازی‌شده و gateهای محیط توسعهٔ کنترل‌شده را ثبت می‌کند.
> اعتبارسنجی محیط توسعه جایگزین deployment مستقل production یا MT5 واقعی نیست.

## وضعیت P0

موارد زیر در کد و تست‌های محلی تکمیل شده‌اند:

1. **احراز هویت و مجوز:** routeهای حساس به نقش محدود شده‌اند. OHLC نقش
   `read_only`، سفارش‌های نامشخص نقش `order_review`، اجرای live نقش
   `signal_execution` و reconciliation نقش `emergency_stop` می‌خواهد. نبود
   credential در production به anonymous fallback تبدیل نمی‌شود؛ `/health`
   عمومی و `/ready` غیر‌اجرایی است.
2. **کنترل durable اجرا:** `execution_control_state` وضعیت stop اضطراری، دلیل
   آن، فعال بودن demo، expiry session، شمارندهٔ demo، snapshot زیان روزانه،
   version، actor و `updated_at` را برای scope حساب/نماد نگه می‌دارد. writeها
   transaction صریح و version-checked هستند و state مفقود/غیرقابل‌خواندن live
   را fail-closed رد می‌کند.
3. **یکپارچه‌سازی workflow:** `LiveOrderWorkflow` repository کنترل را دریافت
   می‌کند، قبل از هر مسیر live state را refresh می‌کند و state transitionها را
   با کنترل هم‌زمانی ذخیره می‌کند. restart stop اضطراری را پاک یا demo را
   خودکار فعال نمی‌کند.
4. **gateway:** خطاهای ZeroMQ به timeout، transport، protocol و unexpected
   تفکیک شده‌اند. فقط transport socket را reset می‌کند؛ هیچ خطایی retry
   خودکار یا نتیجهٔ accepted ایجاد نمی‌کند و timeout برای reconciliation
   unknown باقی می‌ماند.
   پاسخ‌های broker اکنون علاوه بر envelope، status معتبر و سازگاری status/accepted
   را نیز الزام می‌کنند؛ پاسخ `partial` برای fill جزئی پذیرفته اما fail-closed
   قابل reconciliation است.
5. **startup production:** migrationهای Alembic و schema verifier مرز startup
   هستند. `Base.metadata.create_all` فقط در helper صریح test/local باقی مانده
   و production lifespan از آن استفاده نمی‌کند.

## اولویت‌های باقی‌مانده بر اساس ریسک

> آخرین بروزرسانی: 2026-09-18. وضعیت‌ها بر اساس شواهد checkout محلی و آخرین
> اجرای validation ثبت شده‌اند؛ هیچ موردی که فقط به‌صورت مستنداتی یا با stub
> بررسی شده باشد «تکمیل production» محسوب نمی‌شود.

- **P1 تکمیل‌شده در محیط فعلی — migration دیتابیس TimescaleDB:** در probe
  read-only اولیه، PostgreSQL 15.5 و extension `timescaledb` سالم بودند؛ اما
  revision دیتابیس `0006` بود و `execution_control_state` وجود نداشت. پس از
  تأیید مالک محیط، `scripts/migrate_db.py` تا `0009` اجرا شد و verifier، schema
  را سالم تأیید کرد. این نتیجه فقط همین محیط را پوشش می‌دهد و جایگزین
  production deployment مستقل نیست.
- **P1 تکمیل‌شده در محیط فعلی — PostgreSQL 15/TimescaleDB:** هر سه hypertable
  مورد انتظار (`ohlcv_data`، `market_ticks` و `account_snapshots`) در سرویس
  PostgreSQL 15.5/TimescaleDB شناسایی شدند، هرکدام یک بُعد زمانی و chunk interval
  هفت‌روزه دارند، و درج نمونهٔ معتبر در هر سه داخل تراکنش انجام و با rollback
  پاک شد. deployment مستقل production-like و recovery آن همچنان تأیید نشده است.
- **P1 — MT5/EA/ZeroMQ واقعی:** اتصال EA، broker response، transport واقعی و
  recovery/reconciliation با حساب کنترل‌شدهٔ demo هنوز gate نشده است. فقط یک
  REQ/REP dry-run با stub محلی انجام شده و جایگزین EA واقعی نیست.
- **P1 تکمیل‌شده برای baseline — Strategy Tester:** اجرای Strategy Tester با
  profileهای fail-closed و نماد واقعی broker (`XAUUSD_l`) با نتیجهٔ `Test passed`,
  `Total Trades=0` و `Total Deals=0` در artifactهای ثبت‌شده وجود دارد. این
  baseline فقط رفتار بدون معامله را پوشش می‌دهد و جایگزین Demo E2E signal-to-
  broker، broker response یا reconciliation نیست.

### اولویت‌های اجرایی جدید

### داشبورد اولویت و وضعیت

امتیاز ریسک از حاصل‌ضرب شدت مالی/عملیاتی، احتمال وقوع و ضعف کشف پیش از
incident به‌صورت کیفی محاسبه شده است. هر مورد تا زمانی که معیار خروج آن با
شواهد قابل بازتولید ثبت نشود، باز محسوب می‌شود.

| رتبه | کد | سطح | وضعیت شواهد | مالک اجرایی پیشنهادی | وابستگی مستقیم |
|---:|---|---|---|---|---|
| 1 | P0.1 | بحرانی | باز؛ login خواندنی Demo و transport واقعی REQ/REP با stub موفق، مسیر EA/broker/reconciliation هنوز اثبات‌نشده | مالک MT5/EA + Tech Lead | P0.2، P0.3، P0.4 |
| 2 | P0.2 | بحرانی | نیمه‌کامل؛ guard و AST test موجود، runtime واقعی باز | مالک execution | P0.1 |
| 3 | P0.3 | بحرانی | تکمیل‌شده در محیط توسعه؛ دو connection مستقل PostgreSQL سبز، staging مستقل هنوز باز | مالک data/platform | staging مستقل برای release |
| 4 | P0.4 | بحرانی | نیمه‌کامل؛ credential ناقص اکنون fail-closed است، اما rotation/scan/staging اثبات‌نشده | مالک platform/security | قبل از P0.1 |
| 5 | P1.5 | بالا | نیمه‌کامل؛ local staging backup/restore و RTO proxy سبز، managed RPO و rollback باز | مالک platform/DBA | P0ها و backup policy |
| 6 | P1.1 | بالا | تکمیل‌شده در checkout فعلی؛ `ruff check` و `ruff format --check` سبز | مالک Python | پیش‌نیاز P1.2 |
| 7 | P1.2 | بالا | تکمیل‌شده در checkout فعلی؛ Mypy سبز | مالک Python | پیش‌نیاز P1.3 |
| 8 | P1.3 | بالا | تکمیل‌شده محلی؛ Python 3.12 و lock کامل در clean validation سبز | مالک release engineering | P1.1/P1.2 |
| 9 | P1.4 | بالا | تأییدناپذیر محلی؛ Git metadata موجود نیست | repository owner | P1.1 تا P1.3 |
| 10 | P2.1–P2.5 | متوسط | باز؛ پس از عبور از safety gate | مالک domain مربوط | همهٔ P0 و P1 |

#### معیار توقف و ترتیب تصمیم

- تا بسته‌شدن رتبه‌های 1 تا 4، هر feature معاملاتی جدید **متوقف** و فقط
  hardening/validation مجاز است.
- P0.3 در محیط توسعه با PostgreSQL/TimescaleDB واقعی اجرا شده است؛ برای عبور
  production هنوز اجرای همان سناریو در staging مستقل و ثبت evidence لازم است.
- P0.4 باید پیش از دریافت هر credential Demo بسته شود؛ credential واقعی نباید
  برای جبران نقص محیطی در source، fixture یا گزارش وارد شود.
- P1.1 و P1.2 در checkout فعلی بسته شدند؛ اجرای همان commandها در CI و
  تثبیت required check هنوز به مالک repository وابسته است.
- P1.5 پس از ساخت staging مستقل اجرا می‌شود؛ migration محلی یا Compose توسعه‌ای
  جایگزین backup/restore و rollback نیست.

#### P0 — مسدودکنندهٔ فعال‌سازی live

| کد | ضعف | اقدام لازم | معیار خروج |
|---|---|---|---|
| P0.1 | Demo E2E از API تا EA، MT5، broker و reconciliation اثبات نشده است | login خواندنی به terminal موفق شد و transport واقعی ZeroMQ با REP stub، correlation و پاسخ dry-run/rejected تست شد؛ اجرای سفارش واقعی همچنان ممنوع است | بدون duplicate؛ timeout به `unknown`؛ همهٔ transitionها و evidence ثبت شوند |
| P0.2 | یکتا بودن مسیر ارسال سفارش در عمل ثابت نشده است | guard و تست برای عبور همهٔ live orderها از `LiveOrderWorkflow` و محدودسازی مسیرهای legacy | اسکن source و تست runtime مسیر موازی live نشان ندهد |
| P0.3 | race واقعی circuit breaker، idempotency و execution state باید در محیط release نیز اثبات شود | دو تست integration با دو connection مستقل روی PostgreSQL/TimescaleDB محلی اجرا شد: یک owner و یک `ConcurrencyConflict`؛ staging مستقل هنوز اجرا نشده است | اجرای همان integration در staging: یک winner و یک `ConcurrencyConflict`؛ هیچ عبور هم‌زمان از limit و هیچ resend پس از restart |
| P0.4 | مدیریت secret و تنظیمات حساس هنوز به‌طور عملیاتی اثبات نشده است | `DataConfig` اکنون وجود ناقص `MT5_LOGIN/MT5_PASSWORD/MT5_SERVER` را رد می‌کند؛ چهار تست redaction، متن key/value، exception و handler متنی/ساختاریافته را پوشش می‌دهند؛ secret scan، rotation و staging injection هنوز باید اثبات شود | هیچ credential در source/log/artifact نباشد؛ production بدون credential کامل fail-closed شود؛ rotation و redaction با تست و evidence ثبت شود |

#### P1 — گیت کیفیت و تحویل

| کد | ضعف | اقدام لازم | معیار خروج |
|---|---|---|---|
| P1.1 | Ruff در وضعیت قبلی fail بود | import/order، خطاهای format و یافته‌های قطعی lint در `src/` و `tests/` اصلاح شد؛ منطق معاملاتی تغییر نکرد | `py -3 -m ruff check src tests` و `py -3 -m ruff format --check src tests` سبز |
| P1.2 | خطاهای type در چند boundary باقی مانده بود | اصلاح narrowing اختیاری‌ها، تبدیل صریح scalarهای NumPy، قرارداد نتایج backtest، guardهای MT5/ZeroMQ و fallbackهای compatibility؛ بدون ignore گسترده | `py -3 -m mypy --follow-imports=skip src/python` سبز |
| P1.3 | drift بین runtime و lockfile وجود داشت | Python 3.12 canonical شد؛ `full.in` و `full.txt` با dependencyهای observability و TestClient هم‌تراز شدند | نصب clean، compile، Ruff، Mypy، non-MT5 pytest، Alembic و Compose سبز؛ `413 passed, 2 skipped` |
| P1.4 | required بودن CI قابل تأیید نیست | مالک repository required status check را فعال و ثبت کند؛ checkout فعلی Git repository نیست | merge بدون گیت سبز ممکن نباشد |
| P1.5 | دیتابیس فقط در محیط توسعه اثبات شده است | staging مستقل، backup/restore و rollback اجرا شود | restore موفق و schema verifier موفق |

#### P2 — بدهی ساختاری و مدل

| کد | ضعف | اقدام و معیار خروج |
|---|---|---|
| P2.1 | package و import دوگانه | حذف تدریجی `sys.path.insert` و نگه‌داشتن shim فقط برای سازگاری |
| P2.2 | data contract اندیکاتور/filter کامل enforce نمی‌شود | schema version، provenance و رد دادهٔ ناقص پیش از محاسبه |
| P2.3 | assumptions بک‌تست کامل قابل ممیزی نیست | golden test برای spread، slippage، commission، partial fill، gap و leakage |
| P2.4 | registry فایل‌محور برای چند process محدود است | lock، checksum هنگام load، promotion و rollback مدل |
| P2.5 | observability چندپردازه‌ای و alert unknown ناکافی است | metrics/alert برای freshness، exposure، daily P&L و order state |

## ترتیب اجرای به‌روزشده

1. **فاز 0A — P0.3 concurrency:** اجرای PostgreSQL با `TEST_POSTGRES_URL`،
   سپس آزمون restart و unknown؛ live خاموش بماند.
2. **فاز 0B — P0.4 secrets/config:** secret scan، redaction، rotation و
   fail-closed production؛ هیچ credential واقعی در تست یا سند ثبت نشود.
3. **فاز 0C — P0.1/P0.2 Demo E2E و order-path:** فقط روی حساب Demo کنترل‌شده،
   با EA و MT5 واقعی و بدون live trading.
4. **فاز 1 — کیفیت و CI:** P1.2 تا P1.4؛ feature مالی جدید متوقف شود تا gateها سبز
   و required بودن CI به‌صورت دستی تأیید شود.
5. **فاز 2 — staging و recovery:** P1.5 در PostgreSQL/TimescaleDB مستقل، همراه
   backup/restore و rollback.
6. **فاز 3 — package و data contract:** P2.1 و P2.2، بدون rewrite پرریسک.
7. **فاز 4 — ML/backtest governance:** P2.3 و P2.4.
8. **فاز 5 — Demo readiness:** فقط پس از بسته‌شدن همهٔ P0ها و اجرای مجدد suite/CI.

تا عبور از این gateها، نتیجهٔ validation محلی مجوز اجرای live یا ادعای آمادگی
production نیست.

## شواهد validation

جزئیات commandها و محدودیت‌های محیط در
[گزارش validation ایمنی اجرا](../reports/security/execution-safety-auth-validation.md)
ثبت شده است. اجرای تازهٔ `py -3 -m pytest -q --import-mode=importlib`
برابر **419 passed, 2 skipped** و `compileall` موفق بود. علاوه بر آن، اجرای
`py -3 -m ruff check src tests` و
`py -3 -m ruff format --check src tests` سبز شد و اجرای
`tests\\python\\data\\test_postgres_idempotency.py` با دو connection مستقل
PostgreSQL برابر **2 passed** شد. همچنین اجرای
`py -3 -m mypy --follow-imports=skip src/python` روی **82 فایل بدون خطا**
سبز شد؛ بنابراین P1.2 در محیط توسعه بسته است و گیت‌های عملیاتی مستقل همچنان
پیش از ادعای آمادگی production باید تکمیل شوند.
Migration Alembic، lifecycle hypertable و syntax Compose توسعه‌ای جایگزین
gateهای MT5/EA، Demo E2E و deployment مستقل production نیستند. artifactهای
Strategy Tester فقط baseline fail-closed بدون معامله هستند.
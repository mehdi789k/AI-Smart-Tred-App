# بررسی فنی و اولویت‌بندی پروژه Smart MT5 Trading System

تاریخ بررسی: 2026-09-19  
مبنای بررسی: checkout محلی موجود در `D:\Nojom mali\robat metatreder5\AI Smart Tred App`

این گزارش بر اساس کد، قراردادها، تست‌ها، Compose و گزارش‌های validation موجود
تهیه شده است. این checkout پوشهٔ Git نیست؛ بنابراین remote، branch و commit قابل
تأیید نیستند. همچنین نتیجهٔ این بررسی مجوز اجرای live trading یا آمادگی
production مستقل محسوب نمی‌شود.

## خلاصهٔ مدیریتی

پروژه از نظر طراحی ایمنی execution، کنترل fail-closed، ثبت audit، تفکیک محیط
Demo/Shadow و وجود تست‌های قابل‌توجه، پایهٔ خوبی دارد. اجرای فعلی تست‌ها در سطح
Python نشان می‌دهد که لایهٔ قرارداد و gateway در مرز ZMQ با رفتار ایمن، نزدیک به
baseline قابل‌اعتماد رسیده‌اند. با این حال، مهم‌ترین شکاف پروژه همچنان در «اثبات
رفتار انتهابه‌انتها در مرز MT5/EA/ZeroMQ و محیط production» است، نه در کمبود
قابلیت‌های معاملاتی. مانع واقعی هنوز اثبات واقعی Demo/Strategy Tester و replay
سفارش‌ها با correlation و reconciliation است؛ تا زمانی که این اثبات انجام نشود،
توسعهٔ استراتژی و فعال‌سازی خودکار live باید متوقف بماند.

### بروزرسانی وضعیت (2026-09-17)

در این بازبینی، بخش مهم قرارداد و validator در لایهٔ Python به‌صورت هدفمند
تکمیل شد: Golden fixture، checksum، validation envelope، و invariantهای
fail-closed در `src/python/api/zmq_contract.py` و تست‌های مربوطه اعمال شدند.
در مرحلهٔ بعد، replay MT5 به صورت fail-closed و بدون live order execution
در `ops/mt5/SmartTraderEA.demo.set` و `scripts/verify_strategy_tester_inputs.py`
ثبت شد. همچنین یک fixture نسخه‌دار برای guardrails replay در
`tests/fixtures/mt5_replay_guardrails_v1.json` اضافه شد تا رفتار `InpAllowLiveTrading=false`
و `InpEnableZmq=false` به‌صورت مکرر تحت آزمون قرار گیرد.

این تغییرات بخشی از P0 را کاهش می‌دهند، اما همچنان اثبات end-to-end MT5/EA
در broker demo واقعی یا پارامترهای signed execution state به‌صورت مستقل باید
در validation بعدی انجام شود. بازماندهٔ اصلی هنوز روی replay واقعی MT5/Strategy
Tester و Demo E2E با broker محدود و signed execution state است.

در Task 6، دروازهٔ promotion مدل اکنون fail-closed شده است. registry فقط مدلی
را promote می‌کند که نسخهٔ dataset، hash شمای feature، checksum artifact و
شواهد کامل walk-forward خارج از نمونه را داشته باشد. ارزیابی ترکیبی شامل
risk-adjusted return، drawdown، Sharpe و stability است و policy version و
rollback version را در تصمیم ثبت می‌کند. آستانه‌ها جایگزین سود خام هستند و
ردشدن هر معیار مانع promotion می‌شود. وضعیت runtime registry و artifactهای
تولیدی نیز از source control جدا شده‌اند؛ این تغییر به‌تنهایی مجوز اجرای Live
Trading نیست.

در Task 5، workflow اکنون علاوه بر گیت پایدار `test`، job مستقل
`runtime-lock-alignment` را اجرا می‌کند که validator canonical را در محیط
موقت Python 3.12 فراخوانی می‌کند. جزئیات required status check و handoff
دستی branch protection در [`CI_ENFORCEMENT.md`](CI_ENFORCEMENT.md) ثبت شده
است؛ تنظیمات GitHub از این checkout قابل اثبات نیست. اجرای دو worker PostgreSQL
در staging و شواهد secret injection/rotation همچنان gate خارجی پیش از Demo
activation هستند.

**وضعیت validation فعلی:** `py -3 -m pytest -q --import-mode=importlib` برابر
**419 passed, 2 skipped** و `compileall` موفق است. همچنین
`py -3 -m ruff check src tests` و
`py -3 -m ruff format --check src tests` و
`py -3 -m mypy --follow-imports=skip src/python` (۸۲ فایل) سبز هستند؛
گیت type-safety محلی بسته است. دو skip مربوط به integrationهایی هستند که
`TEST_POSTGRES_URL` در محیط حاضر ندارند.

ردگیری امن secrets در logging نیز با چهار تست متمرکز تأیید شده است: مقدارهای
key/value مانند `MT5_PASSWORD=...`، متن exception و هر دو formatter ساختاریافته
و plain-text پیش از خروجی‌سازی redaction می‌شوند. این evidence جایگزین secret
rotation، secret scan مجاز و injection در staging نیست.

در این مرحله سه مرز type-safety در `risk.manager`، `execution.shadow` و اتصال
`execution.mt5_connector` اصلاح شد؛ mypy و Ruff برای همین scope سبز هستند و
۶۵ تست مرتبط موفق شدند. اجرای کامل mypy هنوز به‌دلیل خطاهای باقی‌مانده در
ماژول‌های متعدد سبز نیست و باید در batchهای جداگانه ادامه یابد.

در batch بعدی، مسیر `execution` کامل type-check شد: خطای scope متغیر در حلقه
partial-close اصلاح شد، نبود Shadow Ledger اکنون fail-closed است، ورودی nullable
امتیازدهی مدیریت می‌شود و ایجاد position بدون قیمت fill مسدود است. نتیجه:
`mypy src/python/execution` و `ruff check src/python/execution` هر دو سبز و
رگرسیون کامل **419 passed, 2 skipped** است. در batch کیفیت بعدی،
تمام یافته‌های Ruff در `src/` و `tests/` رفع شد؛ تغییرات شامل import/order،
format و خطاهای قطعی lint بود و رفتار معاملاتی را تغییر نداد.

## نقاط قوت

| اولویت | نقطهٔ قوت | شواهد | ارزش عملی |
|---|---|---|---|
| قوی | اجرای سفارش fail-closed و کنترل‌های چندلایه | [`live_order_workflow.py`](../src/python/execution/live_order_workflow.py)، [`SmartTraderEA.mq5`](../src/mql5/SmartTraderEA.mq5) | نماد whitelist، magic number، سقف حجم، زیان روزانه، confirmation، demo limit و circuit breaker قبل از ارسال بررسی می‌شوند. |
| قوی | وضعیت durable برای توقف اضطراری و کنترل هم‌زمانی | [`DATA_CONTRACT.md`](DATA_CONTRACT.md)، مدل `execution_control_state` در `src/python/data/` | restart نباید emergency stop را پاک کند یا Demo را خودکار فعال کند؛ optimistic concurrency از overwrite خطرناک جلوگیری می‌کند. |
| قوی | تفکیک صحیح نتیجهٔ نامشخص از رد سفارش | [`zmq_gateway.py`](../src/python/api/zmq_gateway.py)، [`broker_reconciliation.py`](../src/python/execution/broker_reconciliation.py) | timeout به‌عنوان accepted تفسیر نمی‌شود و برای reconciliation باقی می‌ماند؛ این برای جلوگیری از سفارش تکراری حیاتی است. |
| قوی | قراردادهای API و دادهٔ نسبتاً صریح | [`API_CONTRACT.md`](API_CONTRACT.md)، [`DATA_CONTRACT.md`](DATA_CONTRACT.md) | schema version، correlation/request ID، idempotency، state machine و قواعد UTC مستند شده‌اند. |
| قوی | تست‌پذیری مناسب منطق حساس | [`tests/`](../tests/)، [`tests/README.md`](../tests/README.md) | تست‌های ریسک، auth، idempotency، کنترل execution، ML fail-closed و Strategy Tester وجود دارند. |
| قوی | جداسازی training/runtime و artifactهای versioned | [`requirements/README.md`](../requirements/README.md)، `src/python/ml/` | lockهای جدا، checksum مدل و golden fixture از آلودگی محیط‌ها و مدل‌های ناشناس جلوگیری می‌کند. |
| خوب | observability و عملیات داخلی | [`OBSERVABILITY_DEPLOYMENT_FA.md`](OBSERVABILITY_DEPLOYMENT_FA.md)، `ops/prometheus/` | metric، alert، health/readiness، Grafana و watchdog برای collector وجود دارد. |
| خوب | وجود Shadow Mode و Strategy Tester baseline | `src/python/execution/shadow.py`، `ops/mt5/artifacts/` | امکان مشاهدهٔ رفتار بدون ارسال سفارش واقعی فراهم شده است. |

## ضعف‌ها و ریسک‌ها به ترتیب اولویت

### وضعیت اجرایی به‌روزشده

| رتبه | کد | وضعیت | تفسیر عملیاتی |
|---:|---|---|---|
| 1 | P0.1 | مسدودِ پس از preflight | اتصال read-only، اصلاح گیت‌های غیرمحرمانه، readiness API و dry-run محدود موفق شد؛ E2E واقعی EA تا broker، پذیرش broker و reconciliation هنوز اجرا نشده و circuit breaker runtime کنترل‌شده هنوز evidence ندارد؛ ارسال سفارش ممنوع است |
| 2 | P0.2 | نیمه‌کامل | مسیر canonical با AST/contract محافظت می‌شود، اما اثبات runtime و نبود bypass در محیط واقعی باقی است |
| 3 | P0.3 | تکمیل‌شده در محیط توسعه | PostgreSQL integration با دو connection مستقل اجرا شد؛ staging مستقل هنوز لازم است |
| 4 | P0.4 | نیمه‌کامل | credential ناقص در `DataConfig` fail-closed شده و redaction تست دارد؛ secret scan، rotation و staging injection evidence ندارد |
| 5 | P1.5 | نیمه‌کامل | local staging backup/restore و schema verification سبز؛ managed RPO و rollback هنوز باز |
| 6 | P1.1 | تکمیل‌شده محلی | `ruff check src tests` و `ruff format --check src tests` سبز هستند |
| 7 | P1.2 | تکمیل‌شده محلی/تعریف‌شده در CI | Mypy روی ۸۲ فایل بدون خطا؛ job پایدار `test` و validator runtime/lock در workflow تعریف شده‌اند، اما اجرای موفق و required بودن آن‌ها باید در GitHub تأیید شود |
| 7 | P1.3 | تکمیل‌شده محلی | Python 3.12 و lock کامل در clean disposable validation سبز شدند |
| 8 | P1.4 | تأییدناپذیر، handoff آماده | required بودن `test` و `runtime-lock-alignment`، review و up-to-date branch باید دستی توسط repository administrator اعمال و ثبت شود |
| 9 | P2 | deferred | تا بسته‌شدن P0/P1 از refactor گسترده و feature جدید جلوگیری شود |

**تصمیم اجرایی:** ترتیب اصلاحات از این پس بر اساس «ریسک فعال‌سازی معامله» است،
نه تعداد تست یا سهولت تغییر. بنابراین اصلاحات زیبایی/ساختاری P2 عمداً بعد از
Demo E2E، concurrency PostgreSQL، secrets و recovery قرار دارند.

### P0 — مسدودکنندهٔ live و production

| ریسک | وضعیت فعلی | چرا بحرانی است | اقدام لازم | معیار خروج |
|---|---|---|---|---|
| نبود gate واقعی MT5/EA/ZeroMQ با broker demo | جزئی کاهش یافته (Python contract/gateway تأیید شد) | تست‌های Python و stub محلی صحت اتصال terminal، پاسخ broker، timeout واقعی و reconciliation را ثابت نمی‌کنند. | یک محیط Demo کنترل‌شده بسازید؛ EA compile شود؛ سناریوهای heartbeat، accepted، rejected، timeout، duplicate و unknown outcome با correlation ID ضبط و replay شوند. | گزارش قابل بازتولید از حداقل یک مسیر signal→risk→ZeroMQ→EA→broker→reconciliation، بدون credential production و بدون live trading. |
| فعال‌سازی احتمالی execution بدون approval عملیاتی | باز | وجود endpoint و تنظیمات کافی نیست؛ خطای تنظیم env یا set file می‌تواند ریسک مالی ایجاد کند. | یک activation checklist اجباری و دو نفره اضافه کنید: `/health`، `/ready`، breaker، symbol/magic، سقف حجم، session expiry، dry-run، Demo و manual confirmation. | هیچ مسیر live بدون checklist ثبت‌شده و approval دوم فعال نشود؛ defaultها همچنان deny باشند. |
| نبود اثبات disaster/recovery و rollback | باز | database، broker و ledger stateful هستند و rollback سادهٔ image کافی نیست. | backup/restore واقعی PostgreSQL/TimescaleDB، replay سفارش نامشخص، restart API/EA و بازگشت circuit breaker را در staging اجرا کنید. | RTO/RPO ثبت‌شده، restore موفق، و عدم duplicate order بعد از restart/replay. |

### P1 — ریسک بالا در قابلیت اتکا و تحویل

| ریسک | شواهد | پیشنهاد حل |
|---|---|---|
| گیت Mypy در وضعیت فعلی fail است | اجرای تازهٔ `mypy --follow-imports=skip src/python` روی ۸۲ فایل بدون خطا شد. | همین command را در CI required کنید و drift نسخهٔ Python را جداگانه رفع کنید. |
| concurrency در PostgreSQL باید در staging نیز اثبات شود | تست integration مستقل برای دو worker با دو connection روی PostgreSQL محلی اجرا شد و winner/conflict را ثبت کرد؛ staging مستقل هنوز اجرا نشده است. | طبق [`CI_ENFORCEMENT.md`](CI_ENFORCEMENT.md) همان تست را با دو worker و دو connection در staging، همراه secret injection، اجرا و خروجی winner/conflict و نبود duplicate را ثبت کنید. |
| CI وجود دارد ولی enforcement آن اثبات نشده است | [`python-validation.yml`](../.github/workflows/python-validation.yml) job پایدار `test` و validator مستقل `runtime-lock-alignment` را زیر Python 3.12 اجرا می‌کند؛ branch protection از checkout قابل مشاهده نیست. | طبق [`CI_ENFORCEMENT.md`](CI_ENFORCEMENT.md) هر دو job را در GitHub required کنید، PR/reviewer و up-to-date branch را الزام کنید و وضعیت را ثبت کنید. |
| drift بین runtimeهای build و type-check | Python 3.12 اکنون در `.python-version`، CI، Docker و تنظیمات Ruff/Mypy canonical است؛ clean disposable validation نیز سبز شد. | همین check را در CI required نگه دارید و پس از تغییر inputها lock متناظر را بازتولید کنید. |
| lock surfaceها به‌صورت مستقل ممکن است ناسازگار شوند | `requirements/api.in` و `requirements/lock/api.txt` با `full` سطح متفاوتی دارند؛ نسخهٔ Alembic در lockهای مختلف باید عمداً مدیریت شود. | برای هر runtime یک policy روشن بنویسید، lock را با `pip-compile` بازتولید کنید و یک CI check برای drift ورودی/lock اضافه کنید. |
| coverage و mutation/safety-net سنجش نمی‌شود | pytest اجرا می‌شود اما threshold coverage در workflow دیده نمی‌شود. | برای حوزه‌های پرریسک (execution، risk، reconciliation، auth) coverage threshold جدا تعریف کنید؛ ابتدا گزارش‌گیری، سپس gate تدریجی. |
| خطای عملیاتی می‌تواند به صورت محلی پنهان بماند | گزارش‌ها عمدتاً local/development هستند و receiver Alertmanager خالی است. | receiver secret-backed، retention، alert routing و runbook پاسخ به alertهای timeout/rejection/stale signal را در staging فعال کنید. |
| checkout شامل داده، log، مدل و cache عملیاتی است | `data/`، `logs/`، `models/` و cacheها در workspace حاضرند؛ بخشی از آن‌ها برای reproducibility مفید و بخشی runtime artifact هستند. | source، fixture، artifact و runtime state را تفکیک کنید؛ artifactهای لازم manifest و checksum داشته باشند؛ log/cache/database در deployment volume یا storage جدا نگهداری شوند. |

### P2 — بهبود معماری و نگهداشت

| ریسک | اثر | پیشنهاد |
|---|---|---|
| README ریشه خالی است | onboarding و اجرای امن به حافظهٔ افراد وابسته می‌شود. | README را به runbook کوتاه تبدیل کنید: معماری، modeها، commandهای canonical، safety boundary و لینک قراردادها. |
| بخشی از API مستندشده هنوز برنامه‌ریزی‌شده است | مصرف‌کننده ممکن است endpoint هدف را قابلیت موجود فرض کند. | در قراردادها برای implemented/planned جدول وضعیت نگه دارید و OpenAPI را با همین مرز هم‌تراز کنید. |
| مرزهای فایل‌محور `indicators/` و `filters/` با مسیر سرویس یکدست نیست | reproducibility، versioning و integration سخت‌تر می‌شود. | فعلاً rewrite نکنید؛ یک adapter/contract version برای ورودی و خروجی بسازید و سپس migration تدریجی انجام دهید. |
| MQL5 و Python قرارداد مشترک دارند اما contract test واقعی محدود است | تغییر نام فیلد یا enum می‌تواند silently reject یا mis-handle شود. | یک corpus پیام versioned بسازید و parser هر دو طرف را با golden message، invalid message و backward-compatibility تست کنید. |
| مسیرهای طولانی execution نیازمند observability عمیق‌ترند | root-cause در incident کند می‌شود. | correlation ID را در DB transition، ZeroMQ envelope، EA log و broker result به‌صورت اجباری propagate کنید و metric latency هر مرز را اضافه کنید. |

## پیشنهاد معماری و راه‌حل‌های منطقی

### اصل تصمیم

پیشنهاد این است که پروژه فعلاً به modular monolith + workerهای جدا برای data,
training و backtest باقی بماند. شکستن زودهنگام به microservice، ریسک consistency
در order state و reconciliation را بیشتر می‌کند. ابتدا مرز قراردادها را تست و
قفل کنید؛ بعد فقط workloadهایی را جدا کنید که واقعاً cadence یا resource متفاوت
دارند.

### مسیر پیشنهادی

1. **Safety freeze (P0):** فعال‌سازی live ممنوع؛ Shadow و Demo-only باقی بماند.
2. **MT5 contract harness (P0):** پیام‌های ZeroMQ، response codeها، duplicate،
   timeout و unknown outcome را golden/replay کنید.
3. **Production-like staging (P0/P1):** PostgreSQL/TimescaleDB، API، Prometheus،
   Alertmanager و terminal Demo را با backup/restore اجرا کنید.
4. **CI enforcement و runtime alignment (P1):** Python canonical، lock policy،
   required check و artifact retention را تثبیت کنید.
5. **Operational hardening (P1):** alert receiver، SLO، runbook، secret injection،
   audit retention و incident drill.
6. **Contract-first consolidation (P2):** indicators/filters فایل‌محور را پشت
   interface versioned قرار دهید؛ فقط پس از parity، endpoint یا worker جدید اضافه
   کنید.
7. **Strategy/ML expansion (آخر):** هر مدل جدید باید data manifest، time split،
   checksum، walk-forward و shadow comparison داشته باشد؛ confidence هرگز به
   تنهایی مجوز سفارش نباشد.

## نقشهٔ اجرای اولویت‌دار

### فاز 1 — بستن مرز live و ساخت harness (اندازه: L)

**هدف:** اثبات رفتاری مرز Python–ZeroMQ–EA بدون ارسال سفارش واقعی.  
**وضعیت ایمنی:** pre-production، safety rung هدف L2/L3؛ residual risk: broker
Demo واقعی تا پایان این فاز مرجع کامل نیست.

- envelope و responseهای versioned را در fixtureهای JSON ثبت کنید.
- سناریوهای accepted/rejected/duplicate/timeout/protocol error/unknown را replay کنید.
- parser MQL5 و gateway Python را با همان corpus تست کنید.
- هر timeout را non-accepted نگه دارید و reconciliation را اجباری assert کنید.
- `InpAllowLiveTrading=false` و `InpEnableZmq=false` را برای Strategy Tester baseline حفظ کنید.

**خروجی قابل قبول:** parity پیام‌ها، عدم ارسال duplicate، و گزارش شکست عمدی
safety-net. هیچ تغییر رفتاری در live path بدون approval انجام نشود.

### فاز 2 — Demo E2E و recovery (اندازه: L)

**هدف:** عبور execution از testability milestone در یک حساب Demo محدود.  
**وضعیت ایمنی:** post-testability برای مسیر Demo؛ safety rung L3، residual risk:
تفاوت broker/production و محدودیت scope حساب.

- terminal و EA را روی Windows host کنترل‌شده مستقر کنید.
- signal تا broker response و reconciliation را end-to-end ثبت کنید.
- restart در هر دو سمت، stale session، emergency stop، daily loss و version conflict را اجرا کنید.
- backup/restore و replay order unknown را مستند کنید.

**CI milestone:** در همین فاز یا بلافاصله پس از اولین تست قابل اجرای Demo،
workflow فعلی به gate معتبر execution تبدیل شود. فعال‌کردن required status check و
branch protection کار دستی مدیر repository است و از checkout قابل انجام/تأیید نیست.

### فاز 3 — production-like operations (اندازه: M)

- receiverهای Alertmanager را با secret manager وصل کنید.
- SLO برای API، data freshness، gateway timeout، reconciliation lag و collector heartbeat تعریف کنید.
- deployment rollback و database restore را drill کنید.
- log و model/data artifactها را از source checkout جدا کنید.

### فاز 4 — هم‌ترازی runtime و کیفیت (اندازه: M)

- Python 3.12 را به‌عنوان runtime مرجع تثبیت کنید.
- lockهای هر image را بازتولید و drift را در CI بررسی کنید.
- coverage حوزه‌های execution/risk/reconciliation/auth را report و سپس gate کنید.
- README ریشه و runbookها را تکمیل کنید.

### فاز 5 — توسعهٔ کنترل‌شدهٔ strategy/ML (اندازه: L)

- هر strategy/model ابتدا در backtest، سپس walk-forward، سپس Shadow، سپس Demo محدود.
- data quality gate باید gap غیرمنتظره، leakage، timezone و schema mismatch را رد کند.
- rollout با feature flag و امکان rollback artifact انجام شود.
- هیچ تغییر مدل بدون `model_version`، checksum، dataset version و audit قابل قبول نیست.

## معیارهای تصمیم برای کارهای آینده

### انجام دهید

- contract test و replay در مرزهای بیرونی؛
- fail-closed و idempotency را حفظ و تقویت کنید؛
- تغییرات را کوچک، reversible و branch-per-phase نگه دارید؛
- هر feature را از Shadow به Demo منتقل کنید، نه مستقیماً به live؛
- همهٔ transactionهای DB و transitionهای state machine را audit کنید.

### فعلاً انجام ندهید

- فعال‌سازی live trading برای «تست»؛
- افزودن strategy یا مدل جدید پیش از E2E و recovery؛
- microservice کردن زودهنگام state/order؛
- حذف reconciliation یا تبدیل timeout به rejection/accepted؛
- نگه‌داشتن credential، database، log یا model mutable در source checkout.

## موارد نیازمند تصمیم مالک پروژه

1. محیط رسمی staging و حساب Demo کنترل‌شده کدام است و چه کسی approval اجرای آن را می‌دهد؟
2. آیا Git repository باید از این checkout ساخته/متصل شود و trunk رسمی آن چیست؟
3. runtime canonical Python، 3.12 است یا 3.11؟ این تصمیم باید در همهٔ manifestها یکسان شود.
4. retention قانونی/عملیاتی برای order audit، broker response و logها چقدر است؟
5. SLO و RTO/RPO مورد انتظار برای API، collector و reconciliation چیست؟
6. کدام endpointهای `[برنامه‌ریزی‌شده]` واقعاً در roadmap هستند و کدام باید حذف یا archive شوند؟

## جمع‌بندی

این پروژه از نظر اصول ایمنی طراحی‌شده بهتر از یک prototype معمولی است؛ ضعف اصلی
در نبود اثبات مستقل و تکرارپذیر در مرز واقعی MT5/EA/broker و در governance تحویل
است. اولویت صحیح «قابلیت معاملهٔ بیشتر» نیست؛ ابتدا باید Demo E2E، recovery،
contract replay، CI enforcement و production-like observability تکمیل شود. پس از
عبور از این گیت‌ها، توسعهٔ ML و strategy با ریسک بسیار قابل‌کنترل‌تری انجام
خواهد شد.

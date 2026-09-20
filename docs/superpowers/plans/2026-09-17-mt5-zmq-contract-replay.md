# MT5 ZeroMQ Contract Replay Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ایجاد یک corpus قراردادی versioned و replay قابل‌تکرار برای مرز Python–ZeroMQ–EA تا سناریوهای accepted، rejected، duplicate، timeout و unknown بدون اتصال live قابل اعتبارسنجی باشند.

**Architecture:** قرارداد پیام در fixtureهای JSON نگهداری می‌شود و یک validator/replay کوچک Python آن را برای تست gateway مصرف می‌کند. تست‌های gateway رفتار فعلی fail-closed را تثبیت می‌کنند؛ EA/MQL5 فقط از همان نمونه‌ها برای parser/result replay در Strategy Tester استفاده می‌کند. هیچ retry سفارش یا تغییر در policy اجرای live اضافه نمی‌شود.

**Tech Stack:** Python 3.12، pytest، JSON fixture، MQL5 strict mode، Strategy Tester

**Spec:** طراحی تأییدشده در گفتگو؛ مبنای معماری [`docs/PROJECT_REVIEW_FA.md`](../../PROJECT_REVIEW_FA.md)

## Global Constraints

- `MT5_AUTO_TRADING_ENABLED=false` و `InpAllowLiveTrading=false` در تمام تست‌ها باقی بماند.
- تست‌ها نباید credential یا حساب واقعی مصرف کنند.
- timeout و unknown هرگز به accepted تبدیل نشوند و retry خودکار سفارش اضافه نشود.
- هر envelope باید `schema_version`, `message_id`, `message_type`, `sent_at`, `correlation_id`, `source` و `payload` داشته باشد.
- فایل‌های [`src/mql5/`](../../../src/mql5/) و [`tests/mql5/`](../../../tests/mql5/) فقط توسط Order Executor تغییر داده شوند.
- فایل‌های موقت، log، database و cache در ریشهٔ پروژه ایجاد نشوند.
- پس از هر تغییر، تست هدفمند و در پایان suite موجود اجرا شود.
- چون checkout فعلی Git repository نیست، commit در این محیط قابل انجام نیست؛ تغییرات باید قبل از ادغام در repository اصلی commit شوند.

---

### Task 1: ثبت corpus قراردادی versioned

**Files:**
- Create: `tests/fixtures/zmq_contract_v1.json`
- Create: `tests/fixtures/zmq_contract_v1.manifest.json`
- Test: `tests/python/api/test_zmq_contract_fixtures.py`

**Interfaces:**
- Produces fixture document با کلیدهای `schema_version`, `cases`.
- هر case شامل `name`, `request`, `expected` و `safety_invariant` است.
- `expected.status` فقط یکی از `accepted`, `rejected`, `unknown`, `timeout` است.

- [x] **Step 1: نوشتن تست شکست‌خورده برای schema corpus**

```python
def test_contract_corpus_contains_required_cases():
    document = load_contract_document()
    names = {case["name"] for case in document["cases"]}
    assert {
        "heartbeat",
        "accepted_order",
        "rejected_order",
        "duplicate_message",
        "timeout",
        "unknown_outcome",
        "invalid_envelope",
        "unsupported_schema",
        "dry_run",
    } <= names
```

- [x] **Step 2: اجرای تست و مشاهدهٔ شکست**

Run: `py -3.12 -m pytest -q tests/python/api/test_zmq_contract_fixtures.py`

Expected: FAIL چون loader و fixture وجود ندارند.

- [x] **Step 3: ایجاد fixture با پیام‌های واقعی قرارداد**

نمونهٔ معتبر باید دارای envelope زیر باشد:

```json
{
  "schema_version": "1.0",
  "message_id": "case-accepted-001",
  "message_type": "order_request",
  "sent_at": "2026-09-17T00:00:00Z",
  "correlation_id": "corr-accepted-001",
  "source": "python",
  "payload": {
    "action": "buy",
    "symbol": "XAUUSD",
    "volume": 0.01,
    "stop_loss": 2490.0,
    "take_profit": 2520.0,
    "magic": 26090901
  }
}
```

برای `timeout` و `unknown_outcome` در `expected` تصریح شود که `accepted=false`
و `requires_reconciliation=true` است. برای `duplicate_message` تصریح شود که
دریافت مجدد همان `message_id` باید rejected شود.

- [x] **Step 4: اضافه‌کردن manifest با SHA-256**

Manifest باید نام fixture، schema نسخه، تعداد caseها و checksum فایل را ثبت کند؛
تست checksum را قبل از مصرف fixture بررسی کند.

- [x] **Step 5: اجرای تست هدفمند**

Run: `py -3.12 -m pytest -q tests/python/api/test_zmq_contract_fixtures.py`

Expected: PASS و همهٔ caseهای الزامی موجود باشند.

- [x] **Step 6: اجرای format و lint روی فایل Python**

Run: `py -3.12 -m ruff format --check tests/python/api/test_zmq_contract_fixtures.py; py -3.12 -m ruff check tests/python/api/test_zmq_contract_fixtures.py`

Expected: PASS.

---

### Task 2: ساخت validator و replay loader پایتون

**Files:**
- Create: `src/python/api/zmq_contract.py`
- Modify: `tests/python/api/test_zmq_contract_fixtures.py`

**Interfaces:**
- `load_contract_document(path: Path) -> ContractDocument`
- `validate_envelope(envelope: Mapping[str, Any], *, source: str) -> None`
- `iter_contract_cases(path: Path) -> Iterator[ContractCase]`
- `ContractValidationError(ValueError)`

- [x] **Step 1: نوشتن تست‌های validator**

```python
def test_validate_envelope_accepts_python_order_request():
    case = get_case("accepted_order")
    validate_envelope(case["request"], source="python")


def test_validate_envelope_rejects_unsupported_schema():
    envelope = dict(get_case("accepted_order")["request"])
    envelope["schema_version"] = "2.0"
    with pytest.raises(ContractValidationError, match="schema_version"):
        validate_envelope(envelope, source="python")


def test_unknown_case_requires_reconciliation():
    case = get_case("unknown_outcome")
    assert case["expected"]["requires_reconciliation"] is True
    assert case["expected"]["accepted"] is False
```

- [x] **Step 2: اجرای تست برای تأیید failure**

Run: `py -3.12 -m pytest -q tests/python/api/test_zmq_contract_fixtures.py`

Expected: FAIL تا validator و مدل‌های قراردادی اضافه شوند.

- [x] **Step 3: پیاده‌سازی typeهای کوچک و validator**

از `dataclass(frozen=True)` و guardهای صریح استفاده شود. validator باید:

1. نوع mapping را بررسی کند.
2. تمام فیلدهای envelope را بررسی کند.
3. `schema_version == "1.0"`، source مورد انتظار و رشته‌های غیرخالی را enforce کند.
4. payload را dictionary یا JSON object معتبر بخواهد.
5. خطای قابل تشخیص با نام فیلد صادر کند.

- [x] **Step 4: اتصال loader به manifest**

loader باید مسیر را resolve کند، manifest را کنار fixture بخواند، checksum را
مقایسه کند و در صورت mismatch با `ContractValidationError` متوقف شود. fallback
ساکت یا نادیده‌گرفتن checksum مجاز نیست.

- [x] **Step 5: اجرای تست‌های هدفمند و lint**

Run: `py -3.12 -m pytest -q tests/python/api/test_zmq_contract_fixtures.py; py -3.12 -m ruff format --check src/python/api/zmq_contract.py tests/python/api/test_zmq_contract_fixtures.py; py -3.12 -m ruff check src/python/api/zmq_contract.py tests/python/api/test_zmq_contract_fixtures.py`

Expected: PASS.

---

### Task 3: تثبیت replay رفتار gateway و unknown outcome

**Files:**
- Modify: `tests/api/test_zmq_gateway.py`
- Modify: `src/python/api/zmq_gateway.py` فقط در صورت نیاز به استخراج validator مشترک
- Test: `tests/python/api/test_zmq_contract_fixtures.py`

**Interfaces:**
- `MQL5ExecutionGateway.request(...)` همان signature فعلی را حفظ می‌کند.
- هیچ API جدیدی برای retry سفارش اضافه نمی‌شود.

- [ ] **Step 1: افزودن تست‌های مبتنی بر corpus**

تست‌ها باید برای caseهای `heartbeat`, `accepted_order`, `rejected_order`,
`duplicate_message`, `timeout` و `unsupported_schema` اجرا شوند و invariantهای
زیر را assert کنند:

```python
def test_timeout_is_not_accepted_and_is_sent_once(...):
    with pytest.raises(GatewayTimeoutError):
        gateway.request("order_request", payload)
    assert socket.send_count == 1
    assert socket.closed is False
```

- [ ] **Step 2: اجرای تست و بررسی regression**

Run: `py -3.12 -m pytest -q tests/api/test_zmq_gateway.py tests/python/api/test_zmq_contract_fixtures.py`

Expected: تست‌های جدید ابتدا در صورت نبود fixture adapter یا invariant لازم fail
کنند؛ تست‌های قبلی نباید حذف یا تضعیف شوند.

- [ ] **Step 3: اضافه‌کردن کمترین adapter لازم**

اگر gateway به validator مشترک نیاز دارد، فقط validation ساختاری envelope پاسخ
و correlation را به helper منتقل کنید. taxonomy خطاهای فعلی
`timeout/transport/protocol/unexpected` و رفتار reset فقط برای transport حفظ شود.

- [ ] **Step 4: تست سناریوی پاسخ unknown**

پاسخ broker با `status=unknown` نباید در gateway به accepted تبدیل شود و باید
در لایهٔ workflow/reconciliation به‌عنوان نتیجهٔ نامشخص باقی بماند. اگر این
رفتار در workflow موجود است، فقط regression test اضافه شود و refactor انجام نشود.

- [ ] **Step 5: اجرای تست و quality gate**

Run: `py -3.12 -m pytest -q tests/api/test_zmq_gateway.py tests/api/test_app.py tests/python/execution/test_live_order_workflow.py tests/python/api/test_zmq_contract_fixtures.py`

Expected: PASS.

---

### Task 4: مصرف corpus در تست MQL5/Strategy Tester

**Files:**
- Modify: `src/mql5/ZmqClient.mqh`
- Modify: `src/mql5/SmartTraderEA.mq5`
- Modify: `tests/mql5/TestEA.mq5`
- Create: `tests/fixtures/mql5/zmq_contract_replay.json`

**Interfaces:**
- Order Executor باید parser و result builder فعلی را حفظ کند.
- `ParseEnvelope`, `MakeResult` و `MakeProtocolError` باید schema `1.0` و correlation را حفظ کنند.

- [ ] **Step 1: تعیین fixture سازگار با parser MQL5**

نسخهٔ MQL5 fixture باید payload JSON object را به‌شکل escaped string نگه دارد تا
با `SEnvelope.payload` فعلی سازگار باشد. caseها حداقل شامل heartbeat، invalid
envelope، unsupported schema، duplicate message و dry-run باشند.

- [ ] **Step 2: افزودن تست compile-time در TestEA**

تست‌ها باید بررسی کنند:

```mql5
Check(parsed.schema_version=="1.0","schema version accepted");
Check(parsed.correlation_id=="corr-accepted-001","correlation preserved");
Check(!client.ParseEnvelope(unsupported_schema,parsed),"unsupported schema rejected");
```

- [ ] **Step 3: اجرای Strategy Tester با live خاموش**

Run: `py -3.12 scripts/verify_strategy_tester_inputs.py src/mql5/SmartTraderEA.mq5 ops/mt5/SmartTraderEA.demo.set`

سپس Strategy Tester را با `InpAllowLiveTrading=false` و `InpEnableZmq=false`
اجرا و report را در `ops/mt5/artifacts/` ذخیره کنید. به terminal واقعی یا order
send متصل نشوید.

- [ ] **Step 4: بررسی نتیجهٔ Strategy Tester**

Expected: compile موفق، همهٔ replay assertionها pass، `Total Trades=0` و
`Total Deals=0`.

---

### Task 5: مستندسازی قرارداد و گیت فاز

**Files:**
- Modify: `docs/API_CONTRACT.md`
- Modify: `docs/DATA_CONTRACT.md`
- Modify: `docs/PROJECT_REVIEW_FA.md`
- Modify: `tests/README.md`

- [ ] **Step 1: ثبت وضعیت corpus**

در API contract، corpus v1، checksum، caseهای پشتیبانی‌شده و تفاوت `timeout`،
`unknown` و `rejected` را ثبت کنید.

- [ ] **Step 2: ثبت گیت خروج فاز**

در گزارش ارزیابی، P0 مربوط به contract harness را از «باز» به
«تکمیل‌شده در محیط تست» تغییر دهید، اما P0 مربوط به Demo E2E را باز نگه دارید.
این دو مورد نباید با هم ادغام شوند.

- [ ] **Step 3: ثبت commandهای قابل اجرا**

در `tests/README.md` commandهای زیر را اضافه کنید:

```powershell
py -3.12 -m pytest -q tests/python/api/test_zmq_contract_fixtures.py
py -3.12 -m pytest -q tests/api/test_zmq_gateway.py tests/api/test_app.py
py -3.12 scripts/verify_strategy_tester_inputs.py src/mql5/SmartTraderEA.mq5 ops/mt5/SmartTraderEA.demo.set
```

---

### Task 6: اجرای verification نهایی

**Files:**
- No source changes; inspect changed files and artifacts.

- [ ] **Step 1: اجرای تست هدفمند**

Run: `py -3.12 -m pytest -q tests/api/test_zmq_gateway.py tests/api/test_app.py tests/python/api/test_zmq_contract_fixtures.py tests/python/execution/test_live_order_workflow.py`

- [ ] **Step 2: اجرای suite کامل**

Run: `py -3.12 -m pytest -q --import-mode=importlib`

Expected: حداقل baseline فعلی حفظ شود: `375 passed, 1 skipped` به‌علاوهٔ تست‌های
جدید؛ هر تغییر عدد باید بررسی شود و با توضیح ثبت گردد.

- [ ] **Step 3: اجرای compile و quality checks**

Run: `py -3.12 -m compileall -q src tests; py -3.12 -m ruff format --check src tests; py -3.12 -m ruff check src tests; py -3.12 -m mypy --follow-imports=skip src/python/api src/python/execution`

- [ ] **Step 4: بررسی Compose و root hygiene**

Run: `docker compose --profile dashboard config --quiet`

سپس بررسی کنید artifact جدید فقط در `tests/fixtures/`, `tests/mql5/`,
`ops/mt5/artifacts/` یا `docs/` قرار گرفته باشد و فایل credential، log، cache یا
database جدیدی در ریشه ایجاد نشده باشد.

## Exit Criteria

- [ ] corpus v1 با manifest و checksum وجود دارد.
- [ ] validator روی envelopeهای معتبر/نامعتبر fail-closed عمل می‌کند.
- [ ] timeout، unknown و duplicate invariantهای ایمنی دارند.
- [ ] تست‌های موجود حذف یا تضعیف نشده‌اند.
- [ ] Strategy Tester بدون live trading و بدون deal موفق است.
- [ ] API/DATA contract و README تست‌ها به‌روز هستند.
- [ ] Demo E2E واقعی هنوز به‌عنوان گیت جدا و باز ثبت شده است.

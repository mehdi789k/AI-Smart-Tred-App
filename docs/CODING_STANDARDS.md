# استانداردهای کدنویسی و migration

این استانداردها برای حفظ رفتار فعلی و جلوگیری از تغییر ناخواسته در سیستم
مالی نوشته شده‌اند. داده‌های `market_data/` و
`mt5_account/account_history/` fixture یا artifact محسوب می‌شوند و formatter،
rename یا migration روی آن‌ها ممنوع است.

## Python

- نسخهٔ canonical پروژه Python 3.12 است و باید در CI، Docker و محیط توسعه
  یکسان بماند؛ سازگاری با Python 3.11 فقط زمانی قابل قبول است که جداگانه
  اعتبارسنجی شود.
- PEP 8، نام‌گذاری `snake_case` برای module/function/variable و `PascalCase`
  برای class.
- type hint برای API عمومی و `from __future__ import annotations`.
- formatter: Black؛ importها: isort؛ lint: ruff. اجرای این ابزارها فقط روی
  source/test انجام شود، نه JSONهای داده.
- public function/class docstring داشته باشد و docstring دلیل تصمیم‌های
  غیر بدیهی را توضیح دهد.
- به‌جای `print` در serviceها از `logging` استفاده شود؛ secret، password و
  token هرگز log نشود.
- exceptionها محدود و معنادار باشند؛ `except Exception` فقط در مرز process/
  adapter و همراه با log و cleanup مجاز است.
- timezone برای timestampهای بازار UTC و aware باشد.
- import canonical جدید باید از `src.python.<domain>` باشد. shimهای قدیمی فقط
  re-export/launcher هستند و business logic ندارند.

## MQL5 و adapterهای MT5

- `#property strict` و indentation چهار فاصله.
- تغییر در order path فقط با تست demo، review و تأیید صریح مجاز است.
- قبل از هر `order_send`: symbol whitelist، حجم/step، SL/TP، magic number،
  margin و circuit breaker بررسی شود.
- shutdown و disconnect در مسیر موفق و خطا تضمین شود؛ retry باید bounded و
  با backoff باشد.
- credential از environment یا secret store؛ هرگز literal در source، test یا
  docs نمونه قرار نگیرد.

## قرارداد JSON و database

- تغییر fieldها additive و versioned باشد؛ حذف یا rename نیازمند migration و
  دورهٔ deprecation است.
- کلیدهای طبیعی ingestion idempotent باشند.
- writeهای database داخل transaction باشند؛ خطای batch باید rollback شود.
- فایل‌های دادهٔ موجود byte-for-byte حفظ شوند مگر کاربر جداگانه درخواست کند.

## تست

- تست‌های unit برای منطق pure و boundaryهای ورودی.
- تست compatibility برای هر shim: import قدیمی و canonical باید به همان
  callable و همان خروجی برسند.
- تست CLI فقط parser/help و رفتار خروجی را بررسی کند؛ در CI به حساب واقعی MT5
  یا order زنده متصل نشود.
- برای تغییرات migration حداقل این‌ها اجرا شوند:

```powershell
py -m pytest -q
py -m unittest discover -s tests -p "test_*.py" -v
```

- قبل از جابه‌جایی baseline ثبت شود؛ بعد از هر domain، suite کامل و smoke
  import اجرا شود. تستی که به‌دلیل نبود optional dependency skip می‌شود باید
  در گزارش صریحاً ثبت شود.
- fixtureهای مالی مصنوعی باشند؛ credential واقعی در تست مجاز نیست.

## سازمان‌دهی source و tests

```text
src/python/{indicators,filters,mt5_account,data,risk}/
tests/{indicators,filters,mt5_account,data,risk}/
```

تست هر domain کنار همان domain قرار می‌گیرد، اما import مسیر قدیمی تا حذف shim
باید پوشش داده شود. `__pycache__/`, `.pytest_cache/` و `.playwright-mcp/`
artifactهای ignored هستند و نباید داخل source یا commit قرار گیرند.

## مستندسازی و review

- هر تغییر API، payload، schema یا import boundary باید سند مربوطه را در
  `docs/` به‌روزرسانی کند.
- PR migration باید فهرست دقیق فایل‌های moved، shimها، entry pointهای حفظ‌شده،
  تست‌های قبل/بعد و فایل‌های دادهٔ عمداً untouched را داشته باشد.
- هیچ refactor هم‌زمان با تغییر منطق مالی انجام نشود؛ migration structural
  باید از تغییر behavior جدا باشد.
- موارد مشکوک به credential، رفتار order یا تغییر خروجی با برچسب blocking
  review شوند.

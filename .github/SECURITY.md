# Security policy

این پروژه یک سیستم معاملاتی متصل به MetaTrader 5 است؛ آسیب‌پذیری در آن می‌تواند
معادل زیان مالی مستقیم باشد، بنابراین قواعد زیر الزامی است.

## گزارش آسیب‌پذیری

- **هرگز** آسیب‌پذیری امنیتی را در Issue عمومی گزارش نکنید.
- از **Private Vulnerability Reporting** مخزن
  (Security → Report a vulnerability) استفاده کنید.
- در صورت غیرفعال بودن این قابلیت، گزارش به نگهدار اصلی مخزن (بر اساس
  `CODEOWNERS`) ارسال شود؛ عنوان گزارش باید با `[SECURITY]` شروع شود.
- حداقل شامل: نسخه/کامیت آسیب‌پذیر، مسیر کد یا سرویس متأثر، گام‌های بازتولید،
  و سطح اثر (API، دیتابیس، gateway صفرتاصفری، EA مسیر سفارش، ریسک/کلیدها).

## محدوده دامنه (Scope)

در دامنه امنیت این پروژه قرار دارد:

- اپلیکیشن FastAPI (`src/python/api/`) و احراز هویت توکن آن
- gateway ZeroMQ و قرارداد پیام‌های آن (`src/python/api/zmq_*.py`, `src/mql5/ZmqClient.mqh`)
- اکشنر MQL5 و گاردریل‌های ExecutionPolicy/OrderManager (`src/mql5/`)
- گیت‌های ریسک، circuit breaker و workflow سفارش زنده (`src/python/risk/`, `src/python/execution/`)
- پیکربندی Docker Compose، migrationها و تنظیمات Prometheus/Grafana (`ops/`, `migrations/`)
- جریان اعتبارنامه: `.env`، secret injection و لاگ‌ها

خارج از دامنه: سرویس‌های ابری ارائه‌دهنده بروکر، ترمینال MT5 و بسته‌های
بالادستی که باید به پروژه مربوطه گزارش شوند (Dependabot برای وابستگی‌ها فعال است).

## تعهدات فنی

- هیچ credential، API key یا رمز عبور حساب واقعی نباید در مخزن، fixture،
  لاگ CI یا artifact کامیت شود؛ فقط placeholderهای غیرحساس در workflowها مجاز است.
- رفتار fail-closed مسیرهای Live بخشی از سیاست امنیتی است؛ هر تغییر کاهش‌دهنده
  آن نیازمند بازبینی امنیتی صریح در PR است.
- retry خودکار پس از timeout/نتیجه نامشخص broker ممنوع است و باید از مسیر
  reconciliation عبور کند.
- بررسی‌های خودکار: CodeQL (python)، Dependency review (fail-on-severity: moderate)
  و Dependabot در `.github/workflows/` و `.github/dependabot.yml`.

## پاسخ‌دهی

- تأیید دریافت گزارش: حداکثر ۵ روز کاری
- ارزیابی اولیه و تصمیم دامنه: حداکثر ۱۴ روز کاری
- انتشار وصله یا راهنمای mitigation برای مشکلات تأییدشده در اولویت منتشر می‌شود؛
  هماهنگی افشای عمومی با گزارش‌دهنده انجام می‌گیرد.

## الزامات محیط عملیاتی

شواهد rotation/injection اسرار و تست همزمانی staging قبل از فعال‌سازی Demo
باید طبق `docs/CI_ENFORCEMENT.md` ثبت و بایگانی شود. موفقیت CI محلی جایگزین
این شواهد نیست.

# تست‌ها

تست‌های پروژه در این پوشه نگهداری می‌شوند تا از کدهای اجرایی جدا باشند.

- `filters\`: تست‌های فیلترهای روند، نوسان و زمان

اجرای همه تست‌ها:

```powershell
py -m pytest -q --import-mode=importlib
```

کامپایل قبل از اجرای CI:

```powershell
py -m compileall -q src tests
```

workflow متناظر در `.github/workflows/python-validation.yml` اجرا می‌شود.
پوشش کد در CI جمع‌آوری و به‌صورت artifact نگهداری می‌شود؛ تا زمان تثبیت
مجموعهٔ تست‌ها، حداقل سراسری ۸۰٪ به‌عنوان شرط شکست فعال نیست.

برای جلوگیری از خواندن تنظیمات واقعی توسعه‌دهنده یا بازیابی scheduler در زمان
collection، `tests/conftest.py` پیش از import برنامه، تنظیمات داشبورد، لاگ‌های
اپلیکیشن/دیتاکالکتور و audit مربوط به Demo را به یک مسیر runtime یکتای موقت
هدایت می‌کند و پس از آزمون، فایل‌هندل‌ها را می‌بندد و همان مسیر را پاک می‌کند.
همچنین MT5 و اجرای خودکار در محیط تست غیرفعال‌اند؛ تست‌های ارتباط/اجرا باید از
fake یا replay محلی استفاده کنند و نباید terminal یا broker واقعی را راه‌اندازی کنند.
در اجرای عادی، مسیرهای پیش‌فرض برنامه تغییری نمی‌کنند؛ `APP_LOG_DIR` و
`MT5_DEMO_AUDIT_LOG_PATH` فقط برای override صریح به‌کار می‌روند.

## replay ایمن MT5 / Strategy Tester

برای اجرای replay بدون live trading، فایل‌های زیر به‌عنوان guardrail رسمی استفاده می‌شوند:

- `ops/mt5/SmartTraderEA.demo.set`
- `scripts/verify_strategy_tester_inputs.py`
- `tests/fixtures/mt5_replay_guardrails_v1.json`

این سناریوها تضمین می‌کنند که:

- `InpAllowLiveTrading=false`
- `InpEnableZmq=false`
- تعداد trades/deals صفر باقی می‌ماند
- هیچ deal واقعی در replay ایجاد نمی‌شود

```powershell
py -3.12 scripts\verify_strategy_tester_inputs.py src\mql5\SmartTraderEA.mq5 ops\mt5\SmartTraderEA.demo.set
```

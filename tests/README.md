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

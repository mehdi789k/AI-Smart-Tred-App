# Runbook قطع MT5 و Circuit Breaker

## هدف

این runbook برای قطع ارتباط MT5، خطای ZeroMQ، تشخیص رفتار غیرعادی سفارش و فعال
کردن circuit breaker است. در تمام موارد، اولویت با جلوگیری از سفارش جدید و
حفظ audit است؛ بستن positionهای باز فقط با تصمیم operator و بررسی وضعیت broker
انجام می‌شود.

## شاخص‌ها و alertهای مورد انتظار

- `http_requests_total{method,path}`: حجم درخواست‌ها
- `http_5xx_total`: تعداد خطاهای سرور؛ عبور از آستانهٔ `OBS_ALERT_HTTP_5XX_THRESHOLD`
  باید alert عملیاتی ایجاد کند.
- `http_request_duration_seconds_count` و `_sum`: latency تجمیعی
- eventهای `mt5_disconnected`, `positions_unavailable`,
  `automation_authorization_expired` در `logs/smart_events.json`
- رویدادهای audit فعال‌سازی/غیرفعال‌سازی Demo در
  `logs/trading_audit.jsonl`

## قطع MT5 یا ZeroMQ

برای اجرای کنترل‌شدهٔ LiteFinance Demo، ابتدا
[`ops/mt5/litefinance-demo-runbook.md`](../ops/mt5/litefinance-demo-runbook.md)
را اجرا کنید. replay محلی یا این runbook به‌تنهایی اعتبارسنجی broker را ثابت
نمی‌کند؛ timeout و unknown همچنان نیازمند reconciliation هستند.

1. ابتدا `/health` و سپس `/ready` را بررسی کنید؛ `/health` فقط process و
   `/ready` وابستگی دیتابیس را پوشش می‌دهد. بعد `/metrics` را با token داخلی
   در صورت تنظیم `OBS_METRICS_TOKEN` بخوانید.
   در پاسخ `/ready`، بخش `dependencies.mt5` و
   `dependencies.circuit_breaker` را نیز بررسی کنید؛ `trading.allowed=false`
   یعنی نباید سفارش جدید ارسال شود.
2. وجود eventهای `mt5_disconnected` یا `positions_unavailable` را در لاگ
   عملیاتی بررسی کنید.
3. تا روشن شدن علت، `MT5_AUTO_TRADING_ENABLED=false` تنظیم کنید و سرویس API یا
   profile اجرای خودکار را restart کنید.
4. EA را بررسی کنید: `InpEnableZmq`، `InpAllowLiveTrading`، endpoint و وضعیت
   اتصال. live باید خاموش بماند.
5. وضعیت positionهای broker را مستقل از API بررسی کنید؛ عدم دسترسی API به معنی
   نبود position نیست.
6. پس از رفع مشکل، ابتدا Shadow Mode را اجرا کنید، سپس فقط در demo با
   confirmation جدید فعال کنید.

## فعال‌کردن Circuit Breaker

Circuit breaker باید در اولین نشانهٔ daily loss، خطای تکرارشوندهٔ execution،
spread غیرعادی، mismatch magic/policy یا دادهٔ ناقص فعال شود.

1. سفارش جدید را متوقف کنید؛ reset خودکار انجام ندهید.
2. علت، زمان UTC، operator، symbol، positionها و آخرین correlation ID را ثبت
   کنید.
3. audit و operational log را جداگانه نگه دارید؛ فایل audit را حذف یا truncate
   نکنید.
4. positionهای باز را با broker تطبیق دهید.
5. فقط پس از تأیید علت ریشه‌ای، تأیید operator و بررسی daily loss، circuit
   breaker را reset کنید.

### Emergency Stop در Demo

برای توقف فوری سفارش‌های جدید Demo، متد `emergency_stop` باید با دلیل مشخص
فراخوانی شود. این عملیات:

- Demo را غیرفعال می‌کند؛
- مجوز automated trading را منقضی می‌کند؛
- همهٔ درخواست‌های جدید را با `emergency_stop_active` رد می‌کند؛
- رویداد append-only در audit ثبت می‌کند.

بازنشانی فقط با `reset_emergency_stop(manual_confirmation=True, reason=...)`
مجاز است. reset بدون تأیید دستی یا بدون دلیل رد می‌شود و فعال‌سازی مجدد Demo
پس از آن همچنان به `activate_demo` و confirmation صریح نیاز دارد.

در Controlled Demo، `allowed_symbols` باید دقیقاً شامل یک نماد باشد. سقف حجم،
تعداد معاملات جلسه و زیان روزانه نیز پیش از هر ارسال کنترل می‌شوند.

## گزارش Shadow و معیار عبور

با اجرای `ShadowOrderLedger.summary()` شاخص‌های زیر برای بازبینی روزانه تولید
می‌شوند:

- تعداد کل، ارزیابی‌شده و باز؛
- تعداد معاملات سودده/زیان‌ده و win rate؛
- total PnL، profit factor و max drawdown.

تا زمانی که سفارش باز یا audit ناقص وجود دارد، عبور به Controlled Demo مجاز
نیست. Shadow باید فقط با قیمت بازار مشاهده‌شده اجرا شود و هیچ مسیر MT5 یا
`order_send` را فراخوانی نکند.

### اجرای گیت خودکار

قبل از فعال‌سازی Demo کنترل‌شده، فقط گیت خواندنی زیر را اجرا کنید؛ این
دستور هیچ endpoint سفارش را فراخوانی نمی‌کند:

```powershell
py scripts\verify_demo_readiness.py `
  --demo-symbol EURUSD `
  --shadow-ledger data\shadow_orders.jsonl `
  --audit-log logs\audit.jsonl `
  --max-shadow-drawdown 0
```

گیت در صورت خالی بودن یا خراب بودن ledger، وجود سفارش باز، audit نامعتبر،
عبور drawdown از حد مجاز، چند symbol یا daily-loss ناامن، fail-closed متوقف
می‌شود.

## معیار بازگشت به سرویس

- MT5 و ZeroMQ پایدار و قابل healthcheck هستند.
- هیچ message duplicate یا request در حالت نامعلوم باقی نمانده است.
- policy version، magic، whitelist و limits در Python و EA یکسان است.
- Shadow replay موفق است.
- Demo canary با حجم حداقلی و confirmation کوتاه‌عمر موفق است.
- operator reset را در audit ثبت کرده است.

## نکات امنیتی

- token، password و credential را در log یا ticket درج نکنید.
- audit حاوی دادهٔ حساس نیست، اما باید دسترسی فایل محدود و retention مشخص داشته
  باشد.
- خاموش‌کردن circuit breaker بدون بررسی broker و audit مجاز نیست.

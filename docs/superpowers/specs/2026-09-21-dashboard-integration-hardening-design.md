# Dashboard Integration Hardening Design

## هدف

پایداری و قابلیت مشاهدهٔ اتصال داشبورد Streamlit به FastAPI و MetaTrader 5 در
محیط Windows و مرورگر، فقط در حالت Demo/Shadow، بدون فعال‌سازی معاملهٔ واقعی.
داشبورد باید برای وضعیت سرویس و دادهٔ بازار از مرز رسمی FastAPI استفاده کند و
اتصال مستقیم MT5 را فقط برای داده‌هایی نگه دارد که API فعلی آن‌ها را ارائه
نمی‌کند.

## شواهد و مسئلهٔ فعلی

- `src/python/dashboard/app.py` و `data_manager.py` مستقیماً به API بومی
  MetaTrader5 متصل می‌شوند.
- قرارداد FastAPI در `docs/API_CONTRACT.md` health/readiness و OHLC را مرز
  رسمی سرویس تعریف می‌کند، اما داشبورد client مشخصی برای مصرف آن ندارد.
- `scripts/start_local_demo.ps1` سرویس‌ها را بالا می‌آورد و dashboard را
  جداگانه اجرا می‌کند، ولی readiness فعلی باید وضعیت API، داشبورد و مسیر داده
  را یکپارچه و قابل تشخیص گزارش کند.
- لاگ داشبورد هشدار حذف `use_container_width` را نشان می‌دهد و باید پیش از
  حذف پشتیبانی نسخه‌ای، با API جدید Streamlit سازگار شود.

## معماری و جریان داده

1. داشبورد یک client کوچک و typed برای `GET /health`، `GET /ready` و
   `GET /api/v1/data/ohlc` ایجاد می‌کند.
2. base URL، timeout و token فقط از environment خوانده می‌شوند؛ token هرگز
   در UI یا log نمایش داده نمی‌شود.
3. صفحهٔ وضعیت، `health` و `ready` را مستقل نمایش می‌دهد:
   `healthy` به‌معنای زنده بودن process و `ready` به‌معنای آماده بودن
   وابستگی‌هاست. شکست هر درخواست با وضعیت `degraded`، زمان آخرین موفقیت و
   خطای قابل‌تشخیص نمایش داده می‌شود.
4. پنل‌های account/positions/history که API رسمی فعلی برایشان endpoint ندارد،
   موقتاً از connector مستقیم MT5 استفاده می‌کنند؛ این مسیر با lock/cache
   موجود ادامه می‌یابد و نباید به‌عنوان جایگزین readiness API تفسیر شود.
5. اجرای سفارش در فاز اول فقط از کنترل‌های Demo/Shadow و workflow فعلی عبور
   می‌کند؛ client جدید هیچ endpoint write یا مسیر live ایجاد نمی‌کند.

## ویندوز و مرورگر

- اسکریپت startup باید بعد از بالا آمدن Compose، `/health` و `/ready` را
  بررسی کند و failure را با پیام دقیق و exit code غیرصفر گزارش دهد.
- health داشبورد Streamlit باید با همان host/port بررسی شود و browser فقط پس
  از موفقیت health باز شود.
- هشدارهای API منسوخ‌شدهٔ Streamlit با استفاده از `width="stretch"` یا
  `width="content"` در تمام call-siteهای touched حذف می‌شوند.
- دسترسی مرورگر به localhost محدود به loopback باقی می‌ماند؛ هیچ bind عمومی
  یا bypass احراز هویت اضافه نمی‌شود.

## مدیریت خطا و ایمنی

- timeout، پاسخ غیر JSON، status code غیرموفق و schema ناسازگار باید به خطای
  typed تبدیل شوند؛ fallback موفق‌نما مجاز نیست.
- نبود API نباید به‌صورت خودکار به اتصال MT5 یا اجرای سفارش تبدیل شود.
- دادهٔ stale با timestamp و وضعیت degraded مشخص می‌شود.
- حالت پیش‌فرض dry-run/Shadow حفظ می‌شود و هیچ credential در پاسخ یا log
  ثبت نمی‌شود.

## تست و پذیرش

- تست واحد برای client: health/ready موفق، timeout، status غیرموفق، JSON
  ناسازگار و OHLC معتبر/نامعتبر.
- تست dashboard برای نمایش وضعیت degraded بدون دسترسی MT5.
- تست PowerShell برای readiness API و dashboard و رفتار port اشغال‌شده.
- اجرای pytest هدفمند، ruff، compileall و smoke مرورگر محلی در Firefox/Chromium
  در صورت در دسترس بودن.
- معیار پذیرش: startup در Demo/Shadow فقط پس از health و readiness موفق
  ادامه دهد؛ داشبورد خطای اتصال را صریح نشان دهد؛ و هیچ سفارش واقعی در
  تست‌ها ارسال نشود.

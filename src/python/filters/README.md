# فیلترها

فیلترهای طراحی‌شده برای تحلیل و انتخاب سیگنال‌ها در این پوشه قرار می‌گیرند.

هر فیلتر باید در یک فایل پایتون مستقل قرار داشته باشد و پارامترهای قابل تنظیم آن در ابتدای همان فایل تعریف شود.

## فیلتر روند

`trend_filters.py` از MA (پیش‌فرض EMA 200) برای جهت و ADX (پیش‌فرض 14، آستانه قوی 25 و ضعیف 20) برای قدرت روند استفاده می‌کند.

```powershell
python filters\trend_filters.py market_data\XAUUSD_l_M5_20260904_11.50.48.json --once --ma-method ema --ma-period 200 --adx-period 14
```

در صورت قدرت روند کمتر از آستانه قوی، مقدار `trend_filter` برابر `avoid` است. همه تنظیمات با گزینه‌های `--ma-period`، `--ma-method`، `--price-field`، `--adx-period`، `--adx-strong-threshold` و `--adx-weak-threshold` قابل تغییر هستند.

در اجرای بدون `--once`، فایل هر یک ثانیه دوباره محاسبه و ذخیره می‌شود. مانند اندیکاتورها، خروجی جدید با نام زمان‌دار نوشته شده و خروجی قبلی حذف می‌شود؛ بنابراین همیشه فقط آخرین خروجی باقی می‌ماند.

## فیلتر نوسان و زمان

`volatility_time_filters.py` سه کنترل را ترکیب می‌کند:

- سشن معاملاتی (پیش‌فرض همپوشانی لندن و نیویورک، `15:30` تا `19:30` تهران)
- توقف `30` دقیقه قبل و بعد از اخبار با شدت `high` یا `red` در فایل JSON
- تشخیص فشردگی ATR در پایین‌ترین صدک تاریخی (پیش‌فرض صدک ۲۰)

قالب فایل اخبار:

```json
[
  {"time_iso": "2026-09-04T16:00:00+00:00", "impact": "high", "title": "NFP"}
]
```

اجرا:

```powershell
py filters\volatility_time_filters.py market_data\XAUUSD_l_M5_20260904_11.50.48.json --once --news-file news.json
```

تمام تنظیمات با گزینه‌های `--atr-period`، `--atr-lookback`، `--atr-low-percentile`، `--news-window-minutes`، `--session-start`، `--session-end` و `--timezone-offset-hours` قابل تغییر هستند.

## فیلتر ساختار و مولتی‌تایم‌فریم

`structure_mtf_filters.py` روند تایم‌فریم بالاتر را با EMA مشخص می‌کند و فقط الگوهای `pinbar` یا `engulfing` هم‌جهت را داخل ناحیه‌ی عرضه/تقاضا معتبر می‌داند. سیگنال خارج از ناحیه (`no_mans_land`) یا خلاف روند HTF با `avoid` فیلتر می‌شود.

قالب نواحی:

```json
[
  {"type": "demand", "lower": 4400, "upper": 4420},
  {"type": "supply", "lower": 4500, "upper": 4520}
]
```

اجرا:

```powershell
py filters\structure_mtf_filters.py market_data\XAUUSD_l_M15_latest.json --htf-input market_data\XAUUSD_l_H4_latest.json --zones-file zones.json --once --ma-period 50
```

با اجرای بدون `--once`، خروجی هر ثانیه به‌روزرسانی می‌شود و فقط آخرین فایل باقی می‌ماند. تنظیمات `--ma-period` و `--zone-tolerance` قابل تغییر هستند.

## فیلتر بین‌بازاری و فاندامنتال

`intermarket_macro_filters.py` شامل دو ابزار است:

- `dxy_filter`: برای جفت‌های اصلی دلاری، رشد DXY خرید را مسدود و فروش را مجاز می‌کند؛ افت DXY برعکس است.
- `filter_correlated_positions`: بازدهی‌ها را با ضریب Pearson مقایسه می‌کند و پوزیشن هم‌جهت بعدی را پس از عبور از حد همبستگی مسدود می‌کند.

پارامترهای `--ema-period` و `--slope-lookback` برای DXY و `--correlation-window`، `--correlation-threshold` و `--max-correlated-positions` برای کنترل ریسک قابل تنظیم هستند. ورودی سیگنال‌ها باید فهرستی مانند زیر باشد:

```json
[
  {"symbol": "EURUSD", "direction": "buy"},
  {"symbol": "GBPUSD", "direction": "buy"}
]
```

اطلاعات DXY با تابع `dxy_filter` و داده‌ی کندلی آن مصرف می‌شود؛ فیلتر همبستگی نیز با `save_correlated_positions` یا CLI روی فایل سیگنال‌ها و داده‌های بازار اجرا می‌شود.

## فیلترهای تأییدیه

`confirmation_filters.py` واگرایی RSI/MACD را روی پیوت‌های تأییدشده بررسی می‌کند و کاهش Tick Volume را هنگام نزدیک‌شدن قیمت به مقاومت تشخیص می‌دهد. خروجی `confirmation_filter` می‌تواند `buy`، `sell`، `none` یا `unknown` باشد.

سطوح مقاومت اختیاری در فایل JSON:

```json
{"resistance": [4450, 4500]}
```

پارامترهای `--rsi-period`، `--volume-period`، `--volume-decline-ratio` و `--level-tolerance` قابل سفارشی‌سازی هستند. مانند سایر فیلترها، اجرای بدون `--once` هر ثانیه خروجی را به‌روزرسانی و فایل قدیمی را حذف می‌کند.

## فیلترهای SMC و شکار نقدینگی

`smc_filters.py` شکست کاذب حمایت/مقاومت را تشخیص می‌دهد. برای Buy، کف بازه‌ی قبلی باید شکسته و قیمت با کندل صعودیِ قدرتمند به بالای حمایت برگردد؛ برای Sell منطق به‌صورت متقارن روی مقاومت اعمال می‌شود. تا قبل از کامل‌شدن `lookback` وضعیت `unknown` است.

پارامترهای `--lookback`، `--min-body-ratio` و `--reclaim-buffer` قابل تنظیم هستند:

```powershell
py filters\smc_filters.py --once --lookback 20 --min-body-ratio 0.5 --reclaim-buffer 0.0
```

## فیلتر FVG

`fvg_filters.py` شکاف سه‌ کندلی صعودی و نزولی را شناسایی می‌کند. تا زمانی که قیمت ناحیه را لمس نکرده باشد، FVG پرنشده باقی می‌ماند و سیگنال هم‌جهت (`buy` برای FVG صعودی و `sell` برای FVG نزولی) با وضعیت `wait` متوقف می‌شود.

سیگنال‌های اختیاری در فایل JSON:

```json
{"signals": ["none", "none", "buy"]}
```

پارامترهای `--min-gap-size` و `--max-path-distance` قابل تنظیم هستند. اجرای بدون `--once` هر ثانیه خروجی را به‌روزرسانی می‌کند و فقط آخرین فایل را نگه می‌دارد.

## فیلتر تغییر ساختار (ChoCh / BOS)

`structure_break_filters.py` قبل از اجازه‌ی ورود در تایم‌فریم پایین، شکست آخرین سقف/کف پیوت‌شده را بررسی می‌کند. برای Buy فقط پس از BOS صعودی و برای Sell فقط پس از BOS نزولی اجازه صادر می‌شود؛ شکست خلاف ساختار قبلی به‌عنوان `ChoCh` ثبت می‌شود. جهت تایم‌فریم بالاتر با `--htf-direction` یا به‌صورت خودکار با مقایسه‌ی قیمت و EMA فایل H4 تعیین می‌شود.

پارامترهای `--pivot-left`، `--pivot-right`، `--min-break-distance` و `--htf-ma-period` قابل تنظیم هستند. خروجی هر ثانیه به‌صورت اتمی جایگزین می‌شود و فقط آخرین فایل باقی می‌ماند.

## فیلتر FOMO

`fomo_filters.py` مسیر طی‌شده از قیمت ورود تا Take Profit را محاسبه می‌کند. اگر این نسبت از آستانه‌ی پیش‌فرض ۵۰٪ بیشتر شود، ورود با وضعیت `avoid` متوقف می‌شود تا پولبک شکل بگیرد. قیمت ورود و هدف باید در فایل `entries` هم‌طول با کندل‌ها ارائه شوند:

```json
{"signal": "buy", "entry_price": 100, "take_profit": 200}
```

یک شیء واحد مانند نمونه‌ی بالا برای تمام کندل‌ها اعمال می‌شود؛ در صورت نیاز می‌توان به‌جای آن از `{"entries": [...]}` با یک رکورد برای هر کندل استفاده کرد.

پارامتر `--progress-threshold` قابل تنظیم است. در نبود قیمت ورود یا TP، خروجی `unknown` است و سیگنال جعلی تولید نمی‌شود. خروجی هر ثانیه جایگزین شده و فقط آخرین فایل باقی می‌ماند.

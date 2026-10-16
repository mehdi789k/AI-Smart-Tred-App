# -*- coding: utf-8 -*-
"""داشبورد بک‌تست و مدیریت استراتژی (Streamlit) — بخشی از سامانه معاملاتی هوشمند MT5.

ساختار:
    app.py           داشبورد Streamlit (اجرا: streamlit run src/python/dashboard/app.py)
    engine.py        موتور بک‌تست/بهینه‌سازی (بدون وابستگی به Streamlit — قابل تست)
    data_source.py   لایه داده: yfinance + CSV آپلودی + فید زنده + کش CSV خروجی مخرط MT5
    trend_sniper.py  منطق اندیکاتور Trend Sniper X (بازپیاده‌سازی MQL5)
    strategies/      سیستم پلاگین: یک فایل = یک استراتژی
"""

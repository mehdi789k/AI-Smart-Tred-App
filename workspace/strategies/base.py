# -*- coding: utf-8 -*-
"""
پایه مشترک همه استراتژی‌ها + زیرساخت «یک فایل برای هر استراتژی».

هر استراتژی یک فایل *.py مستقل در پوشه strategies/ است که باید این‌ها را داشته باشد:
    class MyStrategy(RiskManagedStrategy):   ← منطق trading (نام دلخواه)
        params = BASE_PARAMS + (("fast_period", 10), ...)
        def _entry_logic(self): ...

    SPEC = { "key": "...", "title": "...", "desc": "...", "fields": [ ... ] }
    get_strategy_class() → همان کلاس بالا

داشبورد به‌صورت خودکار همه فایل‌های این پوشه را اسکن می‌کند؛ بنابراین
«آپلود»، «ویرایش» و «افزودن» استراتژی جدید فقط یعنی گذاشتن/ویرایش یک فایل .py.
"""
import glob
import importlib.util
import inspect
import os
import sys

import backtrader as bt

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# رجیستری ماژول‌های پلاگین: نام‌ماژول → مسیر فایل. با این کار بارگذاری مجدد
# (مثلاً بعد از ویرایش کاربر) همان مسیر تازه را استفاده می‌کند و خطای
# KeyError در find_spec (ناشی از کش sys.modules با spec=None) رخ نمی‌دهد.
_PLUGIN_REGISTRY = {}

# تعریف درون‌خطی (inline) پارامترهای پایه — چون متاکلس بک‌تریدر بعد از
# ساخت کلاس مقدار `params` را به شیء MetaParams تبدیل می‌کند، نمی‌توان مستقیماً
# روی RiskManagedStrategy.params عمل جمع انجام داد.
BASE_PARAMS = (
    # ---- مدیریت ریسک (همه قابل ویرایش از داشبورد) ----
    ("risk_per_trade", 0.02),   # درصد ریسک از سرمایه در هر معامله
    ("stop_loss_pct", 0.05),    # حد ضرر (%) — صفر یعنی غیرفعال
    ("take_profit_pct", 0.15),  # حد سود (%) — صفر یعنی غیرفعال
    ("trailing_stop_pct", 0.0), # حد ضرر متحرک (%) — صفر یعنی غیرفعال
    ("allow_short", False),     # اجازه فروش تعهدی (پوزیشن منفی)
    # ---- شناسه برای لاگ ----
    ("strategy_name", "Base"),
)


class RiskManagedStrategy(bt.Strategy):
    params = BASE_PARAMS

    def __init__(self):
        self.order = None            # سفارش در انتظار اجرا
        self.entry_price = None      # قیمت ورود
        self.highest_since_entry = None  # برای تریلینگ استاپ
        self.lowest_since_entry = None   # برای شورت
        self.trade_log = []          # لاگ معاملات بسته‌شده
        self.equity_curve = []       # منحنی ارزش پرتفو (برای QuantStats)
        self.bar_times = []          # زمان هر اسلاید (موازی equity_curve)

    # ------------------------------------------------------------------
    # ابزارهای کمکی
    # ------------------------------------------------------------------
    def _position_size(self, price):
        """محاسبه حجم بر اساس ریسک مجاز هر معامله."""
        if price <= 0:
            return 0
        risk_amount = self.broker.getvalue() * self.p.risk_per_trade
        sl = self.p.stop_loss_pct if self.p.stop_loss_pct > 0 else 0.10
        size = int(risk_amount / (price * sl))
        # سقف حجم: حداکثر کل سرمایه
        max_size = int(self.broker.getcash() / price)
        return max(0, min(size, max_size))

    def _place_entry(self, signal):
        """signal = +1 خرید، -1 فروش/شورت."""
        if self.order:
            return
        price = self.data.close[0]
        size = self._position_size(price)
        if size <= 0:
            return
        if signal > 0:
            self.order = self.buy(size=size)
        elif signal < 0:
            if self.p.allow_short:
                self.order = self.sell(size=size)
            else:
                self.order = self.close()

    def _manage_exit(self):
        """مدیریت پوزیشن باز: حد ضرر/حد سود/تریلینگ."""
        pos = self.position
        if not pos:
            return False
        price = self.data.close[0]
        entry = self.entry_price or pos.price
        if pos.size > 0:
            self.highest_since_entry = max(
                self.highest_since_entry or price, price)
            ref_high = self.highest_since_entry
            if self.p.trailing_stop_pct > 0 and \
               price <= ref_high * (1 - self.p.trailing_stop_pct):
                self.order = self.close()
                return True
            if self.p.stop_loss_pct > 0 and price <= entry * (1 - self.p.stop_loss_pct):
                self.order = self.close()
                return True
            if self.p.take_profit_pct > 0 and price >= entry * (1 + self.p.take_profit_pct):
                self.order = self.close()
                return True
        else:  # پوزیشن شورت
            self.lowest_since_entry = min(
                self.lowest_since_entry or price, price)
            ref_low = self.lowest_since_entry
            if self.p.trailing_stop_pct > 0 and \
               price >= ref_low * (1 + self.p.trailing_stop_pct):
                self.order = self.close()
                return True
            if self.p.stop_loss_pct > 0 and price >= entry * (1 + self.p.stop_loss_pct):
                self.order = self.close()
                return True
            if self.p.take_profit_pct > 0 and price <= entry * (1 - self.p.take_profit_pct):
                self.order = self.close()
                return True
        return False

    # ------------------------------------------------------------------
    # چرخه عمر
    # ------------------------------------------------------------------
    def next(self):
        # ثبت منحنی ارزش پرتفو در هر اسلاید
        self.equity_curve.append(self.broker.getvalue())
        self.bar_times.append(self.data.datetime.datetime(0))
        # مدیریت خروج اولویت دارد
        if self._manage_exit():
            return
        if self.order:
            return
        self._entry_logic()

    def _entry_logic(self):
        """در کلاس فرزند بازنویسی می‌شود."""
        raise NotImplementedError

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return
        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price = order.executed.price
                self.highest_since_entry = order.executed.price
                self.lowest_since_entry = None
            else:
                self.entry_price = order.executed.price
                self.lowest_since_entry = order.executed.price
                self.highest_since_entry = None
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            pass
        self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            # تبدیل datetime بک‌تریدر به متن (API نسخه 1.9.x)
            def _dt(x):
                try:
                    return str(bt.num2date(x))
                except Exception:
                    return ""
            self.trade_log.append(dict(
                strategy=self.p.strategy_name,
                data=str(self._dataname()),
                pnl=trade.pnl,
                pnl_net=trade.pnlcomm,
                bars=trade.barlen,
                open_dt=str(trade.open_datetime()),
                close_dt=str(trade.close_datetime()),
            ))

    def _dataname(self):
        try:
            return self.data._name
        except Exception:
            return "?"


# ======================================================================
# پارامترهای مشترک مدیریت ریسک (فرم داشبورد — کنار فرم اختصاصی هر فایل)
# ======================================================================
RISK_FIELDS = [
    {"key": "risk_per_trade", "label": "ریسک هر معامله (٪ سرمایه)", "type": "float",
     "min": 0.001, "max": 0.5, "step": 0.005, "default": 0.02},
    {"key": "stop_loss_pct", "label": "حد ضرر (٪) — صفر = غیرفعال", "type": "float",
     "min": 0.0, "max": 0.5, "step": 0.005, "default": 0.05},
    {"key": "take_profit_pct", "label": "حد سود (٪) — صفر = غیرفعال", "type": "float",
     "min": 0.0, "max": 2.0, "step": 0.01, "default": 0.15},
    {"key": "trailing_stop_pct", "label": "حد ضرر متحرک (٪) — صفر = غیرفعال", "type": "float",
     "min": 0.0, "max": 0.5, "step": 0.005, "default": 0.0},
    {"key": "allow_short", "label": "اجازه فروش تعهدی (Short)", "type": "bool", "default": False},
]


def build_params(spec_key, user_values):
    """ترکیب مقادیر کاربر + نام استراتژی برای ارسال به cerebro."""
    params = dict(user_values)
    params["strategy_name"] = spec_key
    return params


# ======================================================================
# سیستم پلاگین — بارگذاری خودکار فایل‌های استراتژی
# ======================================================================
_FORBIDDEN_CALLS = {
    "exec", "eval", "compile", "__import__", "open", "input",
    "breakpoint", "exit", "quit",
}
_ALLOWED_IMPORT_ROOTS = {
    "backtrader", "pandas", "numpy", "math", "statistics", "datetime",
    "dataclasses", "typing", "itertools", "functools", "collections",
    "trend_sniper", "strategies", "os.path",
}


def validate_source(source, filename="<upload>"):
    """اعتبارسنجی ایمن + ساختاری سورس یک فایل استراتژی.
    لیست خطاها را برمی‌گرداند (خالی = سالم)."""
    import ast
    errors = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [f"خطای نگارشی (SyntaxError) در خط {e.lineno}: {e.msg}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in _ALLOWED_IMPORT_ROOTS:
                    errors.append(f"وارد کردن ماژول «{alias.name}» مجاز نیست "
                                  f"(خط {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod not in _ALLOWED_IMPORT_ROOTS:
                errors.append(f"وارد کردن از «{node.module}» مجاز نیست "
                              f"(خط {node.lineno})")
        elif isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name in _FORBIDDEN_CALLS:
                errors.append(f"فراخوانی تابع «{name}» در فایل استراتژی مجاز نیست "
                              f"(خط {node.lineno})")

    has_spec = any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "SPEC" for t in n.targets)
        for n in tree.body)
    has_getter = any(isinstance(n, ast.FunctionDef) and n.name == "get_strategy_class"
                     for n in tree.body)
    strat_cls = [n.name for n in tree.body
                 if isinstance(n, ast.ClassDef) and
                 any(getattr(b, "id", "") == "RiskManagedStrategy" or
                     getattr(b, "attr", "") == "RiskManagedStrategy"
                     for b in n.bases)]
    if not strat_cls:
        errors.append("کلاسی که از RiskManagedStrategy ارث ببرد یافت نشد.")
    if not has_spec:
        errors.append("متغیر SPEC (دیکشنری توضیحات و فیلدهای فرم) تعریف نشده است.")
    if not has_getter:
        errors.append("تابع get_strategy_class() تعریف نشده است.")
    return errors


def load_strategy_file(path):
    """یک فایل استراتژی را بارگذاری و اعتبارسنجی می‌کند.
    موفق → dict مشخصات؛ خطا → None (پیام در stderr چاپ می‌شود)."""
    fname = os.path.basename(path)
    modname = f"_user_strategy_{fname[:-3]}"
    try:
        # اگر قبلاً با مسیر *دیگر* (مثلاً نسخه قدیمی قبل از ویرایش) ثبت شده،
        # آن را از کش sys.modules پاک کن تا نسخه تازه روی دیسک خوانده شود.
        prev = _PLUGIN_REGISTRY.get(modname)
        if prev is not None and os.path.abspath(prev) != os.path.abspath(path):
            sys.modules.pop(modname, None)
        cached = sys.modules.get(modname)
        if cached is not None and getattr(cached, "__file__", None) == os.path.abspath(path):
            module = cached   # ری‌استارت سرور لازم نیست؛ همان ماژول معتبر است
        else:
            spec_ = importlib.util.spec_from_file_location(modname, path)
            module = importlib.util.module_from_spec(spec_)
            sys.modules[modname] = module   # ثبت زودهنگام برای resolve حلقه‌ای
            spec_.loader.exec_module(module)
        _PLUGIN_REGISTRY[modname] = os.path.abspath(path)
    except Exception as e:
        sys.modules.pop(modname, None)
        print(f"[strategies] خطای بارگذاری {fname}: {e}", file=sys.stderr)
        return None

    getter = getattr(module, "get_strategy_class", None)
    spec_dict = getattr(module, "SPEC", None)
    if getter is None or not isinstance(spec_dict, dict):
        print(f"[strategies] {fname}: فاقد SPEC یا get_strategy_class — نادیده گرفته شد.",
              file=sys.stderr)
        return None
    try:
        cls = getter()
    except Exception as e:
        print(f"[strategies] {fname}: get_strategy_class خطا داد: {e}", file=sys.stderr)
        return None
    if not (isinstance(cls, type) and issubclass(cls, RiskManagedStrategy)):
        print(f"[strategies] {fname}: کلاس بازگشتی از RiskManagedStrategy نیست.",
              file=sys.stderr)
        return None

    key = spec_dict.get("key") or os.path.splitext(fname)[0]
    entry = dict(spec_dict)
    entry["class"] = cls
    entry["_file"] = fname
    entry["_path"] = path
    return key, entry


def discover_strategies():
    """اسکن کامل پوشه strategies/*.py (به‌جز base.py) و ساخت STRATEGY_SPECS.
    نتیجه در st.session_state کش نمی‌شود — اینجا فقط منبع حقیقت است."""
    specs = {}
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        fname = os.path.basename(path)
        if fname in ("base.py", "__init__.py"):
            continue
        loaded = load_strategy_file(path)
        if loaded:
            key, entry = loaded
            specs[key] = entry
    return specs


def get_strategy_source(key):
    """متن سورس فعلی یک استراتژی (برای نمایش در ویرایشگر داشبورد)."""
    specs = discover_strategies()
    entry = specs.get(key)
    if not entry:
        return None
    with open(entry["_path"], encoding="utf-8") as fh:
        return fh.read()


def save_strategy_source(filename, source):
    """ذخیره سورس در strategies/<filename> پس از اعتبارسنجی.
    موفق → (True, مسیر)؛ خطا → (False, پیام)."""
    if filename.endswith(".py"):
        filename = filename[:-3]
    safe = "".join(c for c in filename if c.isalnum() or c in ("_", "-")).strip("_")
    if not safe or safe in ("base", "__init__"):
        return False, "نام فایل معتبر نیست."
    errors = validate_source(source, safe)
    if errors:
        return False, " | ".join(errors)
    path = os.path.join(HERE, safe + ".py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(source)
    # حذف کش ماژول تا نسخه تازه در اسکن بعدی از دیسک خوانده شود
    modname = f"_user_strategy_{safe}"
    sys.modules.pop(modname, None)
    _PLUGIN_REGISTRY.pop(modname, None)
    return True, path


def delete_strategy_file(key):
    """حذف نرم (move به strategies/_archive) — برگرداندن با جابه‌جایی دستی ممکن است."""
    specs = discover_strategies()
    entry = specs.get(key)
    if not entry:
        return False, "استراتژی یافت نشد."
    archive = os.path.join(HERE, "_archive")
    os.makedirs(archive, exist_ok=True)
    src = entry["_path"]
    dst = os.path.join(archive, os.path.basename(src))
    os.replace(src, dst)
    modname = f"_user_strategy_{os.path.basename(src)[:-3]}"
    sys.modules.pop(modname, None)
    _PLUGIN_REGISTRY.pop(modname, None)
    return True, dst

"""LightGBM factory."""
def LightGBMModel(**kwargs):
    try:
        from lightgbm import LGBMClassifier
    except ImportError as exc: raise RuntimeError("lightgbm is required") from exc
    return LGBMClassifier(objective="multiclass", **kwargs)

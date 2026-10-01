"""Family trainers, imported lazily (a job loads only its model's libraries). Keys match ModelSpec.adapter."""
import importlib

MODULES = {
    "julia": "julia", "laya": "laya", "gliner": "gliner", "kev": "kev", "intern": "intern", "lev": "lev",
    "clm": "clm", "jev_omni": "jev_omni",
}


def get(family: str):
    if family not in MODULES:
        raise KeyError(f"no trainer for the '{family}' model family")
    mod = importlib.import_module(f"{__name__}.{MODULES[family]}")
    return mod.TRAINER


def available(family: str) -> bool:
    """A trainer exists for this family (its module file is present)."""
    import importlib.util
    return family in MODULES and importlib.util.find_spec(f"{__name__}.{MODULES[family]}") is not None

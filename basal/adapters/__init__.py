"""Model adapters, imported lazily so a worker only loads the libraries its model needs."""
import importlib

from .base import Adapter

MODULES = {
    "kev": "kev_adapter", "laya": "laya_adapter", "julia": "julia_adapter", "gliner": "gliner_adapter",
    "lev": "lev_adapter", "intern": "intern_adapter", "jev_omni": "jev_omni_adapter", "clm": "clm_adapter",
    "fake": "fake_adapter",
}


def get(adapter: str) -> type[Adapter]:
    mod = importlib.import_module(f"{__name__}.{MODULES[adapter]}")
    return mod.ADAPTER

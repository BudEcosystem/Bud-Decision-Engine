"""Fine-tuned models: the registry the catalog reads, and the one place that loads a fine-tune for serving.

A fine-tune lives in DATA/finetunes/<id>/ (written by basal/training/engine.py): a small delta (LoRA and head
tensors), a manifest and nothing else. Its catalog entry is the released model's entry with a new id and name, the
delta folder and the fitted temperatures, so download status, load options and limits all follow the released model.

Light module: the server imports it; PyTorch is imported only when a fine-tune is actually loaded (in a worker).
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import tempfile
import time
import zipfile
from dataclasses import replace
from pathlib import Path

from .paths import DATA

FT_DIR = DATA / "finetunes"
# The files a fine-tune may contain: anything else in an imported archive is refused.
FILES = ("manifest.json", "lora.json", "lora.safetensors", "head.safetensors")
MAX_FILE = 2 * 1024 ** 3


def manifests() -> list[dict]:
    out = []
    if not FT_DIR.exists():
        return out
    for d in sorted(FT_DIR.iterdir()):
        m = d / "manifest.json"
        if d.is_dir() and m.exists() and not d.name.startswith("."):      # ".import-*": an import in progress
            try:
                out.append({**json.loads(m.read_text()), "dir": str(d)})
            except ValueError:
                continue
    return out


def _spec_for(man: dict, catalog):
    base = next((s for s in catalog if s.id == man.get("base_model") and not s.finetune_dir), None)
    if base is None:
        return None
    temps = man.get("temperature") or {}
    acc_b, acc_a = man.get("accuracy_before"), man.get("accuracy_after")
    metric = (f"{round(100 * acc_b)}% → {round(100 * acc_a)}% on held-out examples"
              if isinstance(acc_b, (int, float)) and isinstance(acc_a, (int, float)) else base.headline_metric)
    return replace(base, id=man["id"], name=man.get("name") or f"{base.name} (fine-tuned)",
                   tagline=man.get("tagline") or f"Trained on your examples from {base.name}.",
                   summary=man.get("description") or f"{base.name} fine-tuned on this computer "
                           f"({man.get('created', '')[:10]}). It keeps everything {base.name} could do and adds what "
                           "your examples taught it.",
                   badge="Fine-tuned", headline_metric=metric, base_id=base.id, finetune_dir=man["dir"],
                   temperature=tuple(sorted((k, float(v)) for k, v in temps.items())))


def derived_specs(catalog) -> list:
    out = []
    for man in manifests():
        s = _spec_for(man, catalog)
        if s is not None:
            out.append(s)
    return out


def refresh() -> list[str]:
    """Bring the running process's catalog up to date after a job saved or a user deleted a fine-tune."""
    from . import catalog
    released = [s for s in catalog.CATALOG if not s.finetune_dir]
    now = derived_specs(released)
    catalog.CATALOG[:] = released + now
    catalog.BY_ID.clear()
    catalog.BY_ID.update({m.id: m for m in catalog.CATALOG})
    return [s.id for s in now]


def spec_from_dir(d: Path):
    from .catalog import CATALOG
    man = {**json.loads((Path(d) / "manifest.json").read_text()), "dir": str(d)}
    spec = _spec_for(man, CATALOG)
    if spec is None:
        raise RuntimeError(f"the model this fine-tune was trained from ({man.get('base_model')}) is not in the catalog")
    return spec


def delete(ft_id: str) -> bool:
    for man in manifests():
        if man["id"] == ft_id:
            shutil.rmtree(man["dir"], ignore_errors=True)
            refresh()
            return True
    return False


# ----------------------------------------------------------------------------------------------------------------
# Moving a fine-tune between computers: one .zip holding the delta and its manifest (a few to a few hundred MB; the
# released model itself is downloaded on the other computer as usual).


def export_zip(ft_id: str) -> tuple[Path, str]:
    """Writes the fine-tune to a temporary .zip and returns (path, suggested file name)."""
    man = next((m for m in manifests() if m["id"] == ft_id), None)
    if man is None:
        raise KeyError(ft_id)
    d = Path(man["dir"])
    out_dir = DATA / "cache" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.zip"):                      # one at a time is enough; don't let them pile up
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    path = out_dir / f"{ft_id}.zip"
    portable = {k: v for k, v in man.items() if k not in ("dir", "job")}   # local paths mean nothing elsewhere
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:              # safetensors don't compress
        z.writestr("manifest.json", json.dumps(portable, indent=1))
        for name in FILES[1:]:
            if (d / name).exists():
                z.write(d / name, name)
    slug = re.sub(r"[^A-Za-z0-9.]+", "-", man.get("name") or ft_id).strip("-")[:80] or ft_id
    return path, f"{slug}.zip"


def _check_safetensors(path: Path) -> None:
    """The header of a .safetensors file is a length and a JSON table of tensors: check it is one."""
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) != 8:
            raise ValueError(f"{path.name} is empty")
        n = struct.unpack("<Q", head)[0]
        if not 2 <= n <= 100 * 1024 ** 2:
            raise ValueError(f"{path.name} is not a tensor file")
        table = json.loads(f.read(n))
    if not isinstance(table, dict) or not any(k != "__metadata__" for k in table):
        raise ValueError(f"{path.name} holds no tensors")


def import_zip(path: Path) -> dict:
    """Adds a fine-tune exported from any studio. Checks it holds only a fine-tune's files, that its tensor files are
    tensor files, and that the model it was trained from is one this studio knows. Returns its manifest."""
    from .catalog import CATALOG
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("That file isn't a .zip exported from Bud Decision Studio.")
    with z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        unknown = [n for n in names if n not in FILES]
        if "manifest.json" not in names or unknown:
            raise ValueError("That .zip doesn't hold a fine-tuned model exported from Bud Decision Studio"
                             + (f" (unexpected: {', '.join(unknown[:3])})." if unknown else "."))
        if any(i.file_size > MAX_FILE for i in z.infolist()):
            raise ValueError("A file in that .zip is too large to be a fine-tune.")
        if ("lora.safetensors" in names) != ("lora.json" in names) or not ({"lora.safetensors", "head.safetensors"} & set(names)):
            raise ValueError("That fine-tune is incomplete.")
        man = json.loads(z.read("manifest.json"))
        base = next((s for s in CATALOG if s.id == man.get("base_model") and not s.finetune_dir), None)
        if base is None:
            raise ValueError(f"It was trained from {man.get('base_model')!r}, which this studio doesn't have. "
                             "Update the studio, then import it again.")
        if man.get("family") and man["family"] != base.adapter:
            raise ValueError("Its files don't match the model it says it was trained from.")
        taken = {m["id"] for m in manifests()}
        ft_id = str(man.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{0,100}", ft_id) or ft_id in taken or not ft_id.startswith(base.id):
            ft_id = f"{base.id}-ft-{time.strftime('%Y%m%d-%H%M%S')}"
            while ft_id in taken:
                ft_id += "-1"
        FT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=FT_DIR, prefix=".import-"))
        try:
            for name in names:
                if name != "manifest.json":
                    with z.open(name) as src, open(tmp / name, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                    if name.endswith(".safetensors"):
                        _check_safetensors(tmp / name)
            if (tmp / "lora.json").exists():
                cfg = json.loads((tmp / "lora.json").read_text())
                if not isinstance(cfg, dict) or not {"r", "alpha", "targets"} <= set(cfg):
                    raise ValueError("Its LoRA description is incomplete.")
            man = {k: v for k, v in man.items() if k not in ("dir", "job")}
            man.update({"id": ft_id, "base_model": base.id, "family": base.adapter,
                        "imported": time.strftime("%Y-%m-%dT%H:%M:%S")})
            (tmp / "manifest.json").write_text(json.dumps(man, indent=1))
            tmp.rename(FT_DIR / ft_id)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    refresh()
    return {**man, "dir": str(FT_DIR / ft_id)}


def attach(adapter, spec) -> None:
    """Put a fine-tune's delta onto an adapter whose released model is already loaded."""
    if not spec.finetune_dir:
        return
    from .training.families import get
    get(spec.adapter).attach(adapter, Path(spec.finetune_dir))


def load_adapter(spec, options: dict, stage=lambda *a: None):
    """Load a model exactly as the studio's worker does: the released model, then the fine-tune's delta."""
    from . import adapters
    cls = adapters.get(spec.adapter)
    opts = {o["key"]: o["default"] for o in spec.options()}
    opts.update({k: v for k, v in (options or {}).items() if v is not None})
    a = cls(spec, opts, stage)
    a.load()
    attach(a, spec)
    return a


def calibrate(spec, qs, probs: list[list[float]]) -> list[list[float]]:
    """Apply the fine-tune's fitted temperature (per question type, else the shared one) to the model's probabilities.
    Composes with any per-request temperature, which build_answers applies afterwards."""
    if not spec.temperature:
        return probs
    from .contract import apply_temperature
    temps = dict(spec.temperature)
    out = []
    for q, p in zip(qs, probs):
        t = temps.get(q.type, temps.get("all"))
        out.append(apply_temperature(p, t) if t and abs(t - 1) > 1e-6 else p)
    return out

"""The studio side of training: datasets, the model recommendation, and the job processes. Light (no PyTorch).

Layout under DATA/training:
    datasets/<id>/source.<ext>, meta.json    an uploaded file and its import report
    jobs/<id>/job.json, events.jsonl, status.json, result.json    one training run (basal/training/job.py)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from ..paths import DATA, DETACHED, ROOT
from . import dataformat

TRAIN = DATA / "training"
DATASETS = TRAIN / "datasets"
JOBS = TRAIN / "jobs"

# Peak GPU memory to train each model (GB) as measured by the trainer's own verification runs on the GB10
# (docs/trainer/RESULTS.md and the family reports), rounded up. Laya picks activation
# checkpointing by itself when the fast path doesn't fit (3-4 GB instead of 14-24).
TRAIN_GB = {"julia": 8, "laya": 6, "gliner": 15, "kev-0.5b": 8, "kev-4b": 17, "intern": 14, "lev": 16, "clm": 18,
            "jev_omni": 30}
# Which model to suggest when several could learn the task (higher first). Laya leads: on the verification tasks it
# learned the most (59% -> 80% on political topics, general answers unchanged) in under half an hour. The 4B models
# start higher but train for an hour or more on a few hundred examples; candidates() also subtracts for long runs.
PREFERENCE = {"laya": 60, "intern-decision-4b": 55, "kev-4b": 50, "lev": 45, "laya-multilingual": 44, "julia-1": 40,
              "gliner2.5-decide": 35, "clm-v0.1-8b": 30, "kev-0.5b": 20, "laya-typed-decisions": 10, "jev-omni": 5}
SMALL = {"julia", "laya", "gliner", "kev-0.5b"}     # encoders and Kev 0.5B: the models every GPU class may train
# Seconds of training per answered question per pass, replay included, from the verification runs' step times on a
# busy GB10 (other programs were using it), so a quiet machine is somewhat faster; Laya's is from its runs alone
# (420 support tickets in 6 minutes). CLM's figure folds in embedding
# every text once. Other GPUs scale by DEVICE_SLOWER. Used only for the "about N minutes" estimate.
SEC_PER_Q = {"julia": 0.13, "laya": 0.15, "gliner": 0.3, "kev-0.5b": 0.5, "kev-4b": 2.0, "intern": 2.5, "lev": 2.5,
             "clm": 0.05, "jev_omni": 4.0}
MAX_EPOCHS = {"kev": 2.0, "intern": 1.0, "lev": 1.0, "clm": 20.0, "jev_omni": 2.0}   # each family's recipe; else 4
DEVICE_SLOWER = {"cuda": 1.0, "mps": 4.0, "xpu": 6.0}


def estimate_minutes(spec, n_questions: int, kind: str | None) -> int:
    from .engine import epochs_for
    key = spec.id if spec.id in SEC_PER_Q else spec.adapter
    rate = SEC_PER_Q.get(key, 0.3) * DEVICE_SLOWER.get(kind or "cuda", 3.0)
    train_q = n_questions * 0.7
    passes = epochs_for(int(train_q), MAX_EPOCHS.get(spec.adapter, 4.0))
    secs = train_q * passes * rate * 1.35 + (60 if spec.adapter in SMALL or spec.id in SMALL else 240)
    return max(2, round(secs / 60))


def train_gb(spec) -> float:
    return TRAIN_GB.get(spec.id if spec.id in TRAIN_GB else spec.adapter, spec.memory_gb * 1.6 + 2)


# ----------------------------------------------------------------------------------------------------------------
# Device capability (probed once in a subprocess; the server never imports PyTorch)

_probe: dict = {}
_probe_lock = threading.Lock()


def probe(force: bool = False) -> dict:
    with _probe_lock:
        if _probe and not force and time.time() - _probe.get("_at", 0) < 600:
            return _probe
        from .. import config
        backend = (config.load().get("backend") or config.load().get("device") or "cpu")
        if backend == "cpu":
            res = {"ok": False, "tier": "off", "reason": "Training needs a GPU. This computer runs models on the processor."}
        else:
            try:
                out = subprocess.run([sys.executable, "-m", "basal.training.probe"], capture_output=True, text=True,
                                     timeout=120, cwd=ROOT, env={**os.environ, "HF_HUB_OFFLINE": "1"})
                res = json.loads(out.stdout.strip().splitlines()[-1])
            except Exception:  # noqa: BLE001
                res = {"ok": False, "tier": "off", "reason": "The GPU could not be checked for training."}
        _probe.clear()
        _probe.update({**res, "_at": time.time()})
        return _probe


def enabled(settings: dict) -> tuple[bool, str, dict]:
    p = probe()
    if not p.get("ok") or p.get("tier") == "off":
        return False, p.get("reason") or "Training is not available on this computer.", p
    if p.get("tier") == "experimental" and not settings.get("experimental_training"):
        return False, p.get("reason") or "Training on this GPU is experimental.", p
    return True, "", p


# ----------------------------------------------------------------------------------------------------------------
# Datasets


def _meta_path(ds_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", ds_id or ""):
        raise KeyError(ds_id)
    return DATASETS / ds_id / "meta.json"


def import_dataset(data: bytes, filename: str, questions: dict | None = None) -> dict:
    ds_id = uuid.uuid4().hex[:12]
    d = DATASETS / ds_id
    d.mkdir(parents=True, exist_ok=True)
    ext = (Path(filename).suffix or ".txt").lower()[:8]
    (d / f"source{ext}").write_bytes(data)
    return _analyse(ds_id, d / f"source{ext}", filename, questions)


def _analyse(ds_id: str, src: Path, filename: str, questions: dict | None) -> dict:
    imp = dataformat.import_examples(src.read_bytes(), filename, questions)
    rep = imp.report()
    media = sorted({m.type for ex in imp.examples for m in ex.request.media})
    texts = [ex.request.state if isinstance(ex.request.state, str) else json.dumps(ex.request.state, ensure_ascii=False)
             for ex in imp.examples[:300]]
    letters = "".join(texts)
    non_ascii = sum(1 for c in letters if ord(c) > 127 and c.isalpha()) / max(1, sum(c.isalpha() for c in letters))
    longest = max((len(t) for t in texts), default=0)
    meta = {"id": ds_id, "filename": filename, "source": src.name, "created": time.time(), "questions": imp.questions,
            "report": rep, "media": media, "non_english": non_ascii > 0.15, "longest_chars": longest,
            "max_options": max((len(q.keys) for ex in imp.examples for q in ex.qs), default=0),
            "types": sorted({q.type for ex in imp.examples for q in ex.qs}),
            "preview": [{"state": ex.request.state if isinstance(ex.request.state, str) else ex.record["state"],
                         "answers": ex.record.get("answers")} for ex in imp.examples[:5]]}
    _meta_path(ds_id).write_text(json.dumps(meta, indent=1, default=str))
    return meta


def dataset(ds_id: str) -> dict:
    return json.loads(_meta_path(ds_id).read_text())


def update_questions(ds_id: str, questions: dict) -> dict:
    meta = dataset(ds_id)
    merged = {**meta["questions"]}
    for qid, q in (questions or {}).items():
        if qid in merged:
            merged[qid] = {**merged[qid], **{k: v for k, v in q.items() if k in ("type", "instructions", "criteria")}}
    return _analyse(ds_id, DATASETS / ds_id / meta["source"], meta["filename"], merged)


# ----------------------------------------------------------------------------------------------------------------
# Which model to train


def candidates(meta: dict | None, settings: dict) -> list[dict]:
    """Every released model, with whether it can learn this data on this computer and why not."""
    from ..catalog import CATALOG
    from ..hub import model_status
    from .families import available as family_trainable
    ok, why, p = enabled(settings)
    avail = p.get("available_gb") or 0
    kind = p.get("kind")
    out = []
    for spec in CATALOG:
        if spec.finetune_dir or spec.adapter == "fake":
            continue
        reason = ""
        small = spec.adapter in SMALL or spec.id in SMALL
        need = train_gb(spec)
        if not family_trainable(spec.adapter):
            reason = "This model can't be trained yet."
        elif not ok:
            reason = why
        elif not model_status(spec)["complete"]:
            reason = "Download this model first (Models page)."
        elif kind in ("mps", "xpu") and not small and not settings.get("experimental_training"):
            reason = "Too large to train on this computer's GPU."
        elif spec.adapter == "jev_omni" and kind != "cuda":
            reason = "Needs an NVIDIA GPU to train."
        elif avail and need > avail + 2:
            reason = f"Needs about {need:.0f} GB free to train; {avail:.0f} GB is free now."
        elif meta:
            if meta.get("media") and not set(meta["media"]) <= set(spec.modalities):
                reason = f"Can't read {', '.join(m for m in meta['media'] if m not in spec.modalities)}."
            elif not set(meta.get("types") or []) <= set(spec.types):
                reason = "Doesn't answer this kind of question."
            elif meta.get("max_options", 0) > spec.max_options:
                reason = f"Takes at most {spec.max_options} options; your questions have {meta['max_options']}."
            elif meta.get("non_english") and spec.languages == "English":
                reason = "Your examples aren't in English, and this model reads English only."
        score = PREFERENCE.get(spec.id, 0)
        if meta and meta.get("media"):
            score += 50 if set(meta["media"]) <= set(spec.modalities) else 0
        if meta and meta.get("non_english") and spec.languages != "English":
            score += 30
        if meta and meta.get("longest_chars", 0) > 2000 and spec.context_tokens >= 4096:
            score += 10
        eta = estimate_minutes(spec, (meta or {}).get("report", {}).get("labelled_answers", 0), kind) if meta else None
        if eta and eta > 30:
            score -= (eta - 30) / 3        # people new to this shouldn't wait hours by default: a point per 3 minutes
        out.append({"id": spec.id, "name": spec.name, "params": spec.params, "trainable": not reason, "reason": reason,
                    "train_gb": need, "score": score, "tagline": spec.tagline, "eta_minutes": eta})
    out.sort(key=lambda c: (not c["trainable"], -c["score"]))
    return out


def recommend(meta: dict | None, settings: dict) -> dict | None:
    c = candidates(meta, settings)
    return c[0] if c and c[0]["trainable"] else None


# ----------------------------------------------------------------------------------------------------------------
# Jobs

_procs: dict[str, subprocess.Popen] = {}


def start(model_id: str, ds_id: str, name: str = "") -> dict:
    from ..catalog import BY_ID
    meta = dataset(ds_id)
    if not meta["report"]["usable"]:
        raise ValueError(next((p["message"] for p in meta["report"]["problems"] if p["level"] == "error"),
                              "These examples can't be used for training."))
    spec = BY_ID[model_id]
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = JOBS / job_id
    d.mkdir(parents=True)
    job = {"model_id": model_id, "data": str(DATASETS / ds_id / meta["source"]), "out": str(d),
           "questions": meta["questions"], "name": name or f"{spec.name}, trained on {meta['filename']}", "seed": 0,
           "publish_dir": str(DATA / "finetunes"), "dataset_id": ds_id, "created": time.time()}
    (d / "job.json").write_text(json.dumps(job, indent=1))
    (d / "status.json").write_text(json.dumps({"state": "queued", "updated": time.time(), "text": "Starting"}))
    _launch(job_id)
    return job_summary(job_id)


def _launch(job_id: str) -> None:
    d = JOBS / job_id
    log = open(d / "job.log", "a")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "BASAL_DATA": str(DATA),
           "PYTORCH_ENABLE_MPS_FALLBACK": "1"}
    _procs[job_id] = subprocess.Popen([sys.executable, "-m", "basal.training.job", str(d)], stdout=log,
                                      stderr=subprocess.STDOUT, env=env, cwd=ROOT, **DETACHED)


def _end_process(job_id: str) -> None:
    """Ends a job's process and waits for it (it frees its GPU memory on exit)."""
    import psutil
    pids = set()
    proc = _procs.pop(job_id, None)
    if proc is not None and proc.poll() is None:
        pids.add(proc.pid)
    try:
        pids.add(int(json.loads((JOBS / job_id / "status.json").read_text()).get("pid") or 0))
    except (OSError, ValueError, TypeError):
        pass
    for pid in pids - {0}:
        try:
            p = psutil.Process(pid)
            if "basal.training.job" not in " ".join(p.cmdline()):
                continue                    # the id was reused by an unrelated process
            p.terminate()
            try:
                p.wait(15)
            except psutil.TimeoutExpired:
                p.kill()
        except psutil.Error:
            pass


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except Exception:  # noqa: BLE001
        return False


def job_summary(job_id: str, events: bool = False) -> dict:
    d = JOBS / job_id
    if not re.fullmatch(r"[0-9-]+[a-f0-9]{6}", job_id) or not d.exists():
        raise KeyError(job_id)
    job = json.loads((d / "job.json").read_text())
    st = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {"state": "queued"}
    proc = _procs.get(job_id)
    if proc is not None and proc.poll() is not None:
        _procs.pop(job_id, None)
    if st.get("state") in ("running", "queued", "waiting") and not _alive(st.get("pid")) and \
            (proc is None or proc.poll() is not None) and time.time() - st.get("updated", 0) > 20:
        st = {"state": "failed", "message": "The training process stopped unexpectedly. Its log is in the job folder.",
              "updated": time.time()}
        (d / "status.json").write_text(json.dumps(st))
    out = {"id": job_id, "model_id": job["model_id"], "name": job.get("name"), "dataset_id": job.get("dataset_id"),
           "created": job.get("created"), **st}
    if (d / "result.json").exists():
        r = json.loads((d / "result.json").read_text())
        v = r.get("verdict", {})
        out["result"] = {"outcome": v.get("outcome"), "accepted": v.get("accepted"), "message": v.get("message"),
                         "accuracy_before": (v.get("before") or {}).get("accuracy"),
                         "accuracy_after": (v.get("after") or {}).get("accuracy"),
                         "guard_before": (v.get("guard_before") or {}).get("accuracy"),
                         "guard_after": (v.get("guard_after") or {}).get("accuracy"),
                         "test_questions": (v.get("before") or {}).get("n"),
                         "finetune_id": Path(r["finetune_dir"]).name if r.get("finetune_dir") else None,
                         "seconds": r.get("seconds"), "data": r.get("data"), "history": r.get("history"),
                         "showcase": r.get("showcase"), "attempts": r.get("attempts", 1),
                         "evaluate": r.get("evaluate")}
    if events and (d / "events.jsonl").exists():
        evs = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
        restart = max((i for i, e in enumerate(evs) if e.get("type") == "attempt"), default=-1)
        if restart >= 0:                  # practising again from the start: show the new attempt's curve and pace
            evs = [e for i, e in enumerate(evs) if i > restart or e.get("type") in ("baseline", "note", "attempt")]
        out["curve"] = [{"step": e["step"], "accuracy": e["cal_accuracy"]} for e in evs if e.get("type") == "eval"]
        out["notes"] = [e["text"] for e in evs if e.get("type") in ("note", "attempt")][-5:]
        steps = [e for e in evs if e.get("type") == "step"]
        if steps:
            out["step"], out["total_steps"] = steps[-1]["step"], steps[-1]["total"]
            if out.get("state") == "running" and out.get("stage") == "train":
                out["progress"] = max(out.get("progress") or 0, steps[-1].get("progress") or 0)
            recent = steps[-30:]           # recent steps only: a pause or a resume would distort the rate
            if len(recent) > 3:
                rate = (recent[-1]["t"] - recent[0]["t"]) / max(1, recent[-1]["step"] - recent[0]["step"])
                out["eta_seconds"] = round(rate * (steps[-1]["total"] - steps[-1]["step"]))
        base = next((e for e in evs if e.get("type") == "baseline"), None)
        if base:
            out["baseline_accuracy"] = base["test"]["accuracy"]
    return out


def jobs() -> list[dict]:
    if not JOBS.exists():
        return []
    out = []
    for d in sorted(JOBS.iterdir(), reverse=True):
        try:
            out.append(job_summary(d.name))
        except (KeyError, ValueError, OSError):
            continue
    return out


def control(job_id: str, action: str) -> dict:
    d = JOBS / job_id
    job_summary(job_id)                                   # validates the id
    if action == "cancel":
        (d / "cancel").touch()
    elif action == "pause":
        (d / "pause").touch()
    elif action == "resume":
        (d / "pause").unlink(missing_ok=True)
        (d / "cancel").unlink(missing_ok=True)
        st = json.loads((d / "status.json").read_text())
        if st.get("state") in ("paused", "failed", "cancelled"):
            (d / "status.json").write_text(json.dumps({"state": "queued", "updated": time.time(), "text": "Starting again"}))
            (d / "result.json").unlink(missing_ok=True)      # events are kept: the job continues from resume.pt
            _launch(job_id)
    elif action == "delete":
        (d / "cancel").touch()
        _end_process(job_id)          # a job still running in a deleted folder could otherwise go on to publish
        shutil.rmtree(d, ignore_errors=True)
        return {"id": job_id, "deleted": True}
    return job_summary(job_id)

"""HTTP routes for training (included by basal/server.py). Mutating routes are protected by the server's middleware
like every other /api/ route (the X-Basal-Client header, the cross-site guard)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from .. import finetunes
from . import manager

router = APIRouter()
MAX_UPLOAD = 200 * 1024 * 1024


def _settings() -> dict:
    srv = sys.modules.get("basal.server")
    return getattr(srv, "SETTINGS", {}) if srv else {}


@router.get("/api/training/capabilities")
def capabilities(refresh: bool = False):
    if refresh:
        manager.probe(force=True)
    ok, why, p = manager.enabled(_settings())
    return {"enabled": ok, "reason": why, "device": {k: p.get(k) for k in ("name", "kind", "tier", "unified", "available_gb")},
            "experimental": p.get("tier") == "experimental", "models": manager.candidates(None, _settings())}


@router.post("/api/training/datasets")
async def upload_dataset(file: UploadFile = File(...), questions: str = Form(default="")):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "That file is larger than 200 MB. Use a smaller sample of your examples.")
    try:
        q = json.loads(questions) if questions else None
    except ValueError:
        raise HTTPException(422, "questions must be JSON")
    meta = manager.import_dataset(data, file.filename or "examples.csv", q)
    return {**meta, "recommended": manager.recommend(meta, _settings()),
            "models": manager.candidates(meta, _settings())}


@router.get("/api/training/datasets/{ds_id}")
def get_dataset(ds_id: str):
    try:
        meta = manager.dataset(ds_id)
    except (KeyError, OSError):
        raise HTTPException(404, "No such dataset")
    return {**meta, "recommended": manager.recommend(meta, _settings()), "models": manager.candidates(meta, _settings())}


@router.post("/api/training/datasets/{ds_id}/questions")
def set_questions(ds_id: str, body: dict = Body(...)):
    try:
        meta = manager.update_questions(ds_id, body.get("questions") or {})
    except (KeyError, OSError):
        raise HTTPException(404, "No such dataset")
    return {**meta, "recommended": manager.recommend(meta, _settings()), "models": manager.candidates(meta, _settings())}


@router.post("/api/training/jobs")
def start_job(body: dict = Body(...)):
    ok, why, _ = manager.enabled(_settings())
    if not ok:
        raise HTTPException(409, why)
    try:
        meta = manager.dataset(body["dataset_id"])
    except (KeyError, OSError):
        raise HTTPException(404, "No such dataset")
    model_id = body.get("model_id") or (manager.recommend(meta, _settings()) or {}).get("id")
    if not model_id:
        raise HTTPException(409, "None of the downloaded models can learn these examples on this computer. "
                                 "Download a model on the Models page first.")
    cand = next((c for c in manager.candidates(meta, _settings()) if c["id"] == model_id), None)
    if cand is None or not cand["trainable"]:
        raise HTTPException(409, (cand or {}).get("reason") or "That model can't be trained here.")
    try:
        return manager.start(model_id, body["dataset_id"], body.get("name") or "")
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/api/training/jobs")
def list_jobs():
    finetunes.refresh()
    return {"jobs": manager.jobs()}


@router.get("/api/training/jobs/{job_id}")
def get_job(job_id: str):
    try:
        out = manager.job_summary(job_id, events=True)
    except (KeyError, OSError):
        raise HTTPException(404, "No such training")
    if out.get("state") == "done":
        finetunes.refresh()
    return out


@router.post("/api/training/jobs/{job_id}/{action}")
def job_action(job_id: str, action: str):
    if action not in ("cancel", "pause", "resume", "delete"):
        raise HTTPException(404, "Unknown action")
    try:
        return manager.control(job_id, action)
    except (KeyError, OSError):
        raise HTTPException(404, "No such training")


@router.get("/api/finetunes")
def list_finetunes():
    finetunes.refresh()
    return {"finetunes": finetunes.manifests()}


@router.get("/api/finetunes/{ft_id}/export")
def export_finetune(ft_id: str):
    """The fine-tune as one .zip (its delta and manifest), to import into a studio on another computer."""
    from fastapi.responses import FileResponse
    try:
        path, name = finetunes.export_zip(ft_id)
    except KeyError:
        raise HTTPException(404, "No such fine-tuned model")
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/api/finetunes/import")
async def import_finetune(file: UploadFile = File(...)):
    """Adds a fine-tune exported from any studio (the .zip from export). The model it was trained from must be one
    this studio knows; it downloads like any other model."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        size = 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 4 * 1024 ** 3:
                raise HTTPException(413, "That file is too large to be a fine-tuned model.")
            tmp.write(chunk)
    try:
        man = finetunes.import_zip(Path(tmp.name))
    except ValueError as e:
        raise HTTPException(422, str(e))
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return {k: v for k, v in man.items() if k != "dir"}


@router.delete("/api/finetunes/{ft_id}")
async def delete_finetune(ft_id: str):
    srv = sys.modules.get("basal.server")
    if srv is not None and ft_id in getattr(srv, "workers").handles:
        await srv.workers.stop(ft_id)
    if not finetunes.delete(ft_id):
        raise HTTPException(404, "No such fine-tuned model")
    return {"deleted": ft_id}

"""Downloads: what's on disk, what's missing, and a one-at-a-time download queue.

Weights live in the standard Hugging Face cache (~/.cache/huggingface/hub), so
anything downloaded by other tools is recognised, and nothing is duplicated.

Queue rule: one download at a time. Waiting models are ordered by the size still
to download (smallest first); ties go to the model with more likes on the Hub.
Each download runs as its own process (`python -m basal.fetch`), so cancelling
is immediate and a failed download can't disturb the studio.
"""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import BY_ID, CATALOG, ModelSpec, Repo
from .paths import DATA, DETACHED, LOGS

META_FILE = DATA / "hub_meta.json"
QUEUE_FILE = DATA / "download_queue.json"


def hub_cache() -> Path:
    from huggingface_hub import constants
    return Path(constants.HF_HUB_CACHE)


def repo_dir(repo_id: str) -> Path:
    return hub_cache() / ("models--" + repo_id.replace("/", "--"))


# ------------------------------------------------------------------------------------------------------------------
# Hub metadata (file list, sizes, likes), cached on disk so the studio works offline


class Meta:
    def __init__(self):
        self.data: dict = json.loads(META_FILE.read_text()) if META_FILE.exists() else {}
        self.lock = threading.Lock()

    def get(self, repo_id: str) -> dict | None:
        return self.data.get(repo_id)

    def refresh(self, repo_ids: list[str], force: bool = False) -> None:
        from huggingface_hub import HfApi
        api = HfApi()
        changed = False
        for rid in repo_ids:
            cur = self.data.get(rid)
            if cur and not force and "release_date" in cur and time.time() - cur.get("fetched", 0) < 6 * 3600:
                continue
            try:
                info = api.model_info(rid, files_metadata=True)
                self.data[rid] = {
                    "sha": info.sha, "likes": info.likes or 0, "downloads": info.downloads or 0,
                    "release_date": info.last_modified.date().isoformat() if getattr(info, "last_modified", None) else None,
                    "files": {s.rfilename: (s.size or 0) for s in (info.siblings or [])},
                    "fetched": time.time(),
                }
                changed = True
            except Exception as e:  # offline or rate-limited: keep what we had
                print(f"[hub] could not refresh {rid}: {e}", flush=True)
        if changed:
            with self.lock:
                META_FILE.write_text(json.dumps(self.data, indent=1))


META = Meta()


def _wanted(repo: Repo, name: str) -> bool:
    if repo.include and not any(fnmatch.fnmatch(name, p) for p in repo.include):
        return False
    return not any(fnmatch.fnmatch(name, p) for p in repo.exclude)


def expected_files(repo: Repo) -> dict[str, int] | None:
    m = META.get(repo.id)
    if not m:
        return None
    return {f: s for f, s in m["files"].items() if _wanted(repo, f)}


def repo_size(repo: Repo) -> int:
    files = expected_files(repo)
    return sum(files.values()) if files else int(repo.size_gb * 1e9)


def repo_likes(repo: Repo) -> int:
    m = META.get(repo.id)
    return m["likes"] if m else repo.likes


def snapshot_dir(repo_id: str) -> Path | None:
    d = repo_dir(repo_id)
    ref = d / "refs" / "main"
    if ref.exists():
        p = d / "snapshots" / ref.read_text().strip()
        if p.exists():
            return p
    snaps = sorted((d / "snapshots").glob("*"), key=lambda p: p.stat().st_mtime) if (d / "snapshots").exists() else []
    return snaps[-1] if snaps else None


def repo_status(repo: Repo) -> dict:
    """{'complete': bool, 'have': bytes present, 'total': bytes expected, 'missing': [files]}"""
    files = expected_files(repo)
    snap = snapshot_dir(repo.id)
    if files is None:  # never seen metadata: trust any existing snapshot with weights
        ok = bool(snap and any(snap.glob("*.safetensors")) or snap and any(snap.glob("*.pt")))
        total = int(repo.size_gb * 1e9)
        return {"complete": ok, "have": total if ok else 0, "total": total, "missing": [] if ok else ["?"]}
    have, missing = 0, []
    for f, size in files.items():
        p = snap / f if snap else None
        if p is not None and p.exists():
            have += size
        else:
            missing.append(f)
    partial = sum(active_partials(repo.id).values())
    return {"complete": not missing, "have": have, "partial": partial, "total": sum(files.values()), "missing": missing}


STALE_S = 600   # a partial file untouched this long belongs to an interrupted download, not the current one


def active_partials(repo_id: str) -> dict[str, int]:
    """Bytes of each file currently being downloaded, keyed by blob hash (largest live partial per file)."""
    out: dict[str, int] = {}
    blobs = repo_dir(repo_id) / "blobs"
    if blobs.exists():
        now = time.time()
        for p in blobs.glob("*.incomplete"):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            if now - st.st_mtime < STALE_S:
                h = p.name.split(".")[0]
                out[h] = max(out.get(h, 0), st.st_size)
    return out


def remove_stale_partials(repo_id: str) -> int:
    """Delete partial files left by interrupted downloads (they are never resumed). -> bytes freed"""
    freed, blobs = 0, repo_dir(repo_id) / "blobs"
    if blobs.exists():
        now = time.time()
        for p in blobs.glob("*.incomplete"):
            try:
                st = p.stat()
                if now - st.st_mtime >= STALE_S:
                    p.unlink(); freed += st.st_size
            except FileNotFoundError:
                pass
    return freed


def model_status(spec: ModelSpec) -> dict:
    parts = {r.id: repo_status(r) for r in spec.repos()}
    total = sum(p["total"] for p in parts.values())
    have = sum(p["have"] for p in parts.values())
    partial = sum(p.get("partial", 0) for p in parts.values())
    return {"complete": all(p["complete"] for p in parts.values()), "have": have, "partial": partial,
            "total": total, "remaining": max(0, total - have), "parts": parts}


def delete_model_files(spec: ModelSpec, downloaded_ids: set[str]) -> list[str]:
    """Remove this model's repo, and its base only if no other downloaded model shares it. -> removed repo ids."""
    removed = []
    for r in spec.repos():
        shared = any(o.id != spec.id and o.id in downloaded_ids and r.id in [x.id for x in o.repos()] for o in CATALOG)
        if shared:
            continue
        d = repo_dir(r.id)
        if d.exists():
            shutil.rmtree(d)
            removed.append(r.id)
    return removed


# ------------------------------------------------------------------------------------------------------------------
# The queue


@dataclass
class Job:
    model_id: str
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    proc: subprocess.Popen | None = None
    repo_index: int = 0
    error: str | None = None
    speed_bps: float = 0.0
    pid: int | None = None          # a download process started by a previous studio run, adopted on restart
    _last: tuple[float, int] | None = None


class Downloader:
    def __init__(self):
        self.queue: list[Job] = []
        self.active: Job | None = None
        self.failed: dict[str, str] = {}
        self.lock = threading.RLock()
        adopted = self._adopt_running()
        for mid in self._saved():
            if mid in BY_ID and (adopted is None or mid != adopted.model_id):
                self.enqueue(mid)
        if adopted:
            self.active = adopted
            self._save()
        threading.Thread(target=self._loop, daemon=True, name="downloader").start()

    # -- persistence: a restart resumes where the queue left off ---------------------------------------------
    @staticmethod
    def _saved() -> list[str]:
        try:
            return json.loads(QUEUE_FILE.read_text())
        except Exception:
            return []

    def _save(self) -> None:
        with self.lock:
            ids = ([self.active.model_id] if self.active and not self.active.error else []) + [j.model_id for j in self.queue]
        try:
            QUEUE_FILE.write_text(json.dumps(ids))
        except OSError:
            pass

    @staticmethod
    def _adopt_running() -> "Job | None":
        """Downloads run in their own process group and outlive a studio restart (the Hub library cannot resume a
        half-downloaded file, so killing one would throw its progress away). A restarted studio picks it back up."""
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = p.info.get("cmdline") or []
            if "basal.fetch" in cmd and p.pid != os.getpid():
                repo_id = cmd[cmd.index("basal.fetch") + 1] if len(cmd) > cmd.index("basal.fetch") + 1 else None
                for spec in CATALOG:
                    if repo_id in [r.id for r in spec.repos()] and not model_status(spec)["complete"]:
                        job = Job(spec.id, started_at=time.time(), pid=p.pid)
                        print(f"[hub] resuming watch of {repo_id} download (pid {p.pid})", flush=True)
                        return job
        return None

    # -- ordering -----------------------------------------------------------------------------------------------
    @staticmethod
    def sort_key(model_id: str):
        spec = BY_ID[model_id]
        st = model_status(spec)
        return (st["remaining"], -repo_likes(spec.repo))

    def order(self, ids: list[str]) -> list[str]:
        return sorted(ids, key=self.sort_key)

    # -- api ------------------------------------------------------------------------------------------------------
    def enqueue(self, model_id: str) -> None:
        with self.lock:
            self.failed.pop(model_id, None)
            if (self.active and self.active.model_id == model_id) or any(j.model_id == model_id for j in self.queue):
                return
            if model_status(BY_ID[model_id])["complete"]:
                return
            self.queue.append(Job(model_id))
            self.queue.sort(key=lambda j: self.sort_key(j.model_id))
        self._save()

    def enqueue_all(self) -> list[str]:
        ids = [m.id for m in CATALOG if not model_status(m)["complete"]]
        for i in self.order(ids):
            self.enqueue(i)
        return [j.model_id for j in self.queue]

    def cancel(self, model_id: str) -> bool:
        with self.lock:
            before = len(self.queue)
            self.queue = [j for j in self.queue if j.model_id != model_id]
            if self.active and self.active.model_id == model_id:
                if self.active.proc and self.active.proc.poll() is None:
                    self.active.proc.terminate()
                if self.active.pid:
                    try: os.kill(self.active.pid, 15)
                    except ProcessLookupError: pass
                self.active.error = "cancelled"
                self._save()
                return True
            self._save()
            return len(self.queue) != before

    def snapshot(self) -> dict:
        with self.lock:
            active = None
            if self.active:
                j = self.active
                active = {"model_id": j.model_id, "started_at": j.started_at, "speed_bps": j.speed_bps,
                          "repo": BY_ID[j.model_id].repos()[min(j.repo_index, len(BY_ID[j.model_id].repos()) - 1)].id}
            return {"active": active, "queue": [j.model_id for j in self.queue], "failed": dict(self.failed),
                    "rule": "One at a time. Smallest remaining download first; ties go to the model with more likes."}

    # -- worker loop ------------------------------------------------------------------------------------------------
    def _loop(self):
        while True:
            job = None
            with self.lock:
                if self.active is not None and self.active.pid and not getattr(self.active, "_running", False):
                    job = self.active
                    job._running = True
                elif self.active is None and self.queue:
                    self.queue.sort(key=lambda j: self.sort_key(j.model_id))
                    job = self.active = self.queue.pop(0)
                    job.started_at = time.time()
            if job is None:
                time.sleep(0.5)
                continue
            try:
                self._run(job)
            except Exception as e:  # noqa: BLE001
                job.error = str(e)
            with self.lock:
                if job.error and job.error != "cancelled":
                    self.failed[job.model_id] = job.error
                self.active = None
            self._save()

    def _run(self, job: Job):
        with open(LOGS / f"download-{BY_ID[job.model_id].id}.log", "a") as log:
            self._run_logged(job, log)

    def _run_logged(self, job: Job, log):
        spec = BY_ID[job.model_id]
        if job.pid:
            import psutil
            def alive(pid):
                try:
                    return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
                except psutil.Error:
                    return False
            while alive(job.pid):
                if job.error == "cancelled":
                    return
                self._measure(job)
                time.sleep(1.0)
            job.pid = None
            if job.error == "cancelled":
                return
        for i, repo in enumerate(spec.repos()):
            job.repo_index = i
            if job.error == "cancelled":
                return
            if repo_status(repo)["complete"]:
                continue
            freed = remove_stale_partials(repo.id)
            if freed:
                log.write(f"removed {freed / 1e9:.2f} GB of stale partial files from an interrupted download\n"); log.flush()
            cmd = [sys.executable, "-m", "basal.fetch", repo.id]
            if repo.include:
                cmd += ["--include", *repo.include]
            if repo.exclude:
                cmd += ["--exclude", *repo.exclude]
            env = {**os.environ, "HF_HUB_DISABLE_PROGRESS_BARS": "1", "HF_HUB_OFFLINE": "0",
                   "HF_HUB_DISABLE_XET": "1"}   # classic transfer writes .incomplete files, so progress is measurable
            job.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, **DETACHED,
                                        cwd=Path(__file__).resolve().parent.parent)
            while job.proc.poll() is None:
                self._measure(job)
                time.sleep(1.0)
            if job.error == "cancelled":
                return
            if job.proc.returncode != 0:
                job.error = f"download of {repo.id} failed (exit {job.proc.returncode}); see data/logs/download-{spec.id}.log"
                return
        META.refresh([r.id for r in spec.repos()], force=False)
        if not model_status(spec)["complete"]:
            job.error = "download finished but some files are still missing; try again"

    @staticmethod
    def _measure(job: Job):
        st = model_status(BY_ID[job.model_id])
        now, got = time.time(), st["have"] + st["partial"]
        if job._last:
            dt = now - job._last[0]
            if dt > 0:
                inst = max(0.0, (got - job._last[1]) / dt)
                job.speed_bps = inst if job.speed_bps == 0 else 0.7 * job.speed_bps + 0.3 * inst
        job._last = (now, got)

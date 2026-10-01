"""The training job process: `python -m basal.training.job <job dir>`.

The studio starts one of these per training run, like it starts one worker per loaded model: a crash, an
out-of-memory error or a cancel ends this process and leaves the studio untouched. The job reads job.json, writes
progress to events.jsonl and its outcome to status.json, and stops cleanly when a `cancel` or `pause` file appears in
its folder. Only one job trains at a time on a computer (a lock file), so two trainings never compete for memory.
The process holding the lock is a small supervisor; the engine runs in its child (`--engine`), which is restarted if
it stops showing signs of life.

For verification runs outside the studio:

    python -m basal.training.job --model julia-1 --data examples.csv --out /tmp/job1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


class Lock:
    """One training at a time per computer, first come first served: each waiting job holds a ticket in a queue folder
    and only the oldest live ticket may take the lock (a plain file lock would hand it to whichever waiter polls first)."""

    def __init__(self, path: Path):
        self.path = path
        self.f = None
        self.queue = path.parent / "queue"

    def _try(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _first_in_line(self, ticket: Path) -> bool:
        import psutil
        for t in sorted(self.queue.iterdir()):
            if t == ticket:
                return True
            try:
                alive = psutil.pid_exists(int(t.name.rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                alive = False
            if alive:
                return False
            t.unlink(missing_ok=True)          # left behind by a job that ended without cleaning up
        return True

    def acquire(self, on_wait, should_stop=lambda: None) -> str | None:
        """Waits for the lock. Returns None once held, or the reason ("cancelled" / "paused") if asked to stop while
        waiting."""
        self.queue.mkdir(parents=True, exist_ok=True)
        ticket = self.queue / f"{time.time():017.6f}-{os.getpid()}"
        ticket.touch()
        self.f = open(self.path, "a+")
        waited = False
        try:
            while True:
                if self._first_in_line(ticket) and self._try():
                    return None
                why = should_stop()
                if why:
                    self.f.close()
                    self.f = None
                    return why
                if not waited:
                    on_wait()
                    waited = True
                time.sleep(3)
        finally:
            ticket.unlink(missing_ok=True)

    def release(self) -> None:
        if self.f:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
            finally:
                self.f.close()


def _stack_dumps(job_dir: Path) -> None:
    """Write every thread's Python stack to <job>/stacks.log on a crash, or on demand with `kill -USR1 <pid>`, so a
    stuck job can be diagnosed on any machine without a debugger."""
    import faulthandler
    f = open(job_dir / "stacks.log", "a")
    faulthandler.enable(file=f, all_threads=True)
    if hasattr(faulthandler, "register") and hasattr(__import__("signal"), "SIGUSR1"):
        import signal
        faulthandler.register(signal.SIGUSR1, file=f, all_threads=True)
    _stack_dumps.file = f


def machine_lock_path() -> Path:
    """One lock per user account, whatever studio data folder a job uses, so two trainings never overlap.
    BASAL_TRAIN_LOCK names another lock file; it is for development only (running a small verification job beside a
    long one), never set by the studio."""
    override = os.environ.get("BASAL_TRAIN_LOCK")
    return Path(override) if override else Path.home() / ".cache" / "bud-decision-studio" / "training.lock"


# How long the engine's main thread may go without a sign of life before the supervisor ends it and starts again. A
# stage that legitimately runs long without a heartbeat (loading a large model under memory pressure) gets more time.
STALL_SECONDS = {"load": 1800, "check": 900, "save": 600, "verify": 1800}
STALL_DEFAULT = 900
MAX_RESTARTS = 2


def _files(job_dir: Path):
    events = open(job_dir / "events.jsonl", "a")
    owner = int(os.environ.get("BASAL_JOB_PID") or os.getpid())     # the supervisor: what the studio watches

    def status(state: str, **kw):
        (job_dir / "status.json").write_text(json.dumps({"state": state, "updated": time.time(), "pid": owner, **kw}))

    def emit(ev: dict):
        ev = {"t": round(time.time(), 2), **ev}
        events.write(json.dumps(ev, default=str) + "\n")
        events.flush()
        if ev.get("type") == "stage":
            status("running", stage=ev.get("stage"), text=ev.get("text"), progress=ev.get("progress"))
        print(json.dumps(ev, default=str), flush=True)

    def should_stop():
        if (job_dir / "cancel").exists():
            return "cancelled"
        if (job_dir / "pause").exists():
            return "paused"
        return None
    return events, status, emit, should_stop


def run(job_dir: Path) -> int:
    """The job: wait for the machine lock, then run the engine in a child process and watch it. If the child stops
    showing signs of life (on the GB10 a GPU call has been seen to hang with the GPU idle under heavy memory pressure),
    it is ended and started again, up to MAX_RESTARTS times; a paused job's resume point is kept, so it continues."""
    job_dir = Path(job_dir)
    events, status, emit, should_stop = _files(job_dir)
    lock = Lock(machine_lock_path())
    why = lock.acquire(lambda: (emit({"type": "waiting", "text": "Waiting for another training to finish"}),
                                status("waiting", stage="waiting", text="Waiting for another training to finish")),
                       should_stop)
    try:
        if why:
            msg = "Training was cancelled." if why == "cancelled" else "Training was paused."
            emit({"type": "stopped", "state": why, "text": msg})
            status(why, message=msg)
            return 2
        for attempt in range(MAX_RESTARTS + 1):
            outcome = _supervise(job_dir, status)
            if outcome != "stalled":
                _settle(job_dir, outcome, emit, status)
                return outcome
            if attempt < MAX_RESTARTS:
                emit({"type": "attempt", "n": "restart", "text": "The GPU stopped responding, so training is starting again."})
        msg = ("The GPU stopped responding several times, so training was stopped. Closing other programs that use a lot "
               "of memory usually helps; then press Try again.")
        emit({"type": "error", "text": msg})
        status("failed", message=msg)
        return 1
    finally:
        events.close()
        lock.release()


def _settle(job_dir: Path, code: int, emit, status) -> None:
    """The engine ended without saying how (killed by the operating system, a crash in native code): say so."""
    try:
        state = json.loads((job_dir / "status.json").read_text()).get("state")
    except (OSError, ValueError):
        state = None
    if state not in ("running", "waiting", "queued", None):
        return
    msg = ("The computer ran out of memory and stopped the training. Closing other programs usually helps; then "
           "press Try again." if code in (-9, 137) else
           f"The training process stopped unexpectedly (exit code {code}). Its log is in the job folder.")
    emit({"type": "error", "text": msg})
    status("failed", message=msg)


def _supervise(job_dir: Path, status, cmd: list[str] | None = None):
    import signal
    import subprocess
    env = {**os.environ, "BASAL_JOB_PID": str(os.getpid())}
    child = subprocess.Popen(cmd or [sys.executable, "-m", "basal.training.job", "--engine", str(job_dir)], env=env)
    previous = signal.getsignal(signal.SIGTERM)

    def stop_child(*_):                       # the studio ending this job ends the engine too
        child.kill()
        sys.exit(1)
    signal.signal(signal.SIGTERM, stop_child)
    alive, events = job_dir / "alive", job_dir / "events.jsonl"
    cancel_seen, started = None, time.time()
    try:
        while True:
            try:
                return child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if (job_dir / "cancel").exists():
                cancel_seen = cancel_seen or time.time()
                if time.time() - cancel_seen > 60:        # stuck and asked to stop: stop it from outside
                    child.kill()
                    child.wait()
                    status("cancelled", message="Training was cancelled.")
                    return 2
            try:
                stage = json.loads((job_dir / "status.json").read_text()).get("stage")
            except (OSError, ValueError):
                stage = None
            last = max([started] + [f.stat().st_mtime for f in (alive, events) if f.exists()])
            if time.time() - last > STALL_SECONDS.get(stage, STALL_DEFAULT):
                if hasattr(signal, "SIGUSR1"):
                    child.send_signal(signal.SIGUSR1)     # its stacks go to stacks.log, for diagnosis
                    time.sleep(2)
                child.kill()
                child.wait()
                return "stalled"
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_engine(job_dir: Path) -> int:
    """The engine itself, in the supervisor's child process."""
    from ..paths import DATA
    from .engine import Engine, Job, Stopped

    job_dir = Path(job_dir)
    _stack_dumps(job_dir)
    spec = json.loads((job_dir / "job.json").read_text())
    job = Job(**{k: v for k, v in spec.items() if k in Job.__dataclass_fields__})
    job.out = str(job_dir)
    job.publish_dir = job.publish_dir or str(DATA / "finetunes")
    events, status, emit, should_stop = _files(job_dir)
    try:
        status("running", stage="check", text="Starting", progress=0.0)
        result = Engine(job, emit, should_stop).run()
        if result.get("finetune_dir"):
            try:
                from .. import finetunes
                finetunes.refresh()
            except Exception:  # noqa: BLE001
                pass
        emit({"type": "done", "result": {k: result[k] for k in ("verdict", "finetune_dir", "seconds", "parity")
                                         if k in result}})
        status("done", verdict=result["verdict"]["outcome"], accepted=result["verdict"]["accepted"],
               message=result["verdict"]["message"], finetune_dir=result.get("finetune_dir"), progress=1.0)
        return 0
    except Stopped as e:
        why = str(e)
        state = "cancelled" if why == "cancelled" else "paused"
        msg = "Training was cancelled." if state == "cancelled" else (
            why if why != "paused" else "Training was paused.")
        emit({"type": "stopped", "state": state, "text": msg})
        status(state, message=msg)
        return 2
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        (job_dir / "error.log").write_text(tb)
        msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
        emit({"type": "error", "text": msg})
        status("failed", message=msg)
        print(tb, file=sys.stderr, flush=True)
        return 1
    finally:
        events.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir", nargs="?")
    ap.add_argument("--engine", help=argparse.SUPPRESS)          # internal: the supervisor's child
    ap.add_argument("--model")
    ap.add_argument("--data")
    ap.add_argument("--out")
    ap.add_argument("--name", default="")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-parity", action="store_true")
    ap.add_argument("--no-replay", action="store_true")
    ap.add_argument("--override", default="{}", help="JSON of recipe fields (advanced)")
    a = ap.parse_args()
    if a.engine:
        return run_engine(Path(a.engine))
    if a.job_dir:
        return run(Path(a.job_dir))
    if not (a.model and a.data and a.out):
        ap.error("give a job folder, or --model, --data and --out")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from .engine import Job
    job = Job(model_id=a.model, data=str(Path(a.data).resolve()), out=str(out.resolve()), name=a.name, seed=a.seed,
              overrides=json.loads(a.override), parity=not a.no_parity, replay=not a.no_replay)
    (out / "job.json").write_text(json.dumps(asdict(job), indent=1))
    return run(out)


if __name__ == "__main__":
    sys.exit(main())

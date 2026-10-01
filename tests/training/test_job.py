"""The job process's machine lock: one training at a time, first come first served, and a waiting training can still
be cancelled."""
import os

from basal.training.job import Lock


def test_second_training_waits_and_can_be_cancelled_while_waiting(tmp_path):
    first, second = Lock(tmp_path / "training.lock"), Lock(tmp_path / "training.lock")
    assert first.acquire(lambda: None) is None
    waited = []
    assert second.acquire(lambda: waited.append(1), should_stop=lambda: "cancelled") == "cancelled"
    first.release()
    assert second.acquire(lambda: None) is None          # free again: the next one gets it at once
    second.release()


def test_an_earlier_waiter_goes_first_and_dead_waiters_are_skipped(tmp_path):
    lock = Lock(tmp_path / "training.lock")
    lock.queue.mkdir(parents=True)
    earlier = lock.queue / f"{1.0:017.6f}-{os.getpid()}"       # a live job that started waiting before us
    earlier.touch()
    assert lock.acquire(lambda: None, should_stop=lambda: "cancelled") == "cancelled"   # lock is free, but not our turn
    earlier.unlink()
    (lock.queue / f"{2.0:017.6f}-999999999").touch()            # a job that died while waiting
    assert lock.acquire(lambda: None) is None
    assert list(lock.queue.iterdir()) == []
    lock.release()


def test_job_summary_follows_the_second_attempt_and_live_progress(tmp_path, monkeypatch):
    import json
    import time
    from basal.training import manager
    monkeypatch.setattr(manager, "JOBS", tmp_path)
    d = tmp_path / "20261001-000000-abc123"
    d.mkdir()
    (d / "job.json").write_text(json.dumps({"model_id": "julia-1", "name": "t"}))
    (d / "status.json").write_text(json.dumps({"state": "running", "stage": "train", "progress": 0.1, "pid": os.getpid(),
                                               "updated": time.time()}))
    t = time.time()
    evs = [{"type": "baseline", "test": {"accuracy": 0.5}},
           {"type": "eval", "step": 10, "cal_accuracy": 0.7},
           {"type": "attempt", "n": 2, "text": "practising again"},
           *[{"type": "step", "step": s, "total": 40, "progress": 0.1 + 0.78 * s / 40, "t": t + s} for s in range(1, 9)],
           {"type": "eval", "step": 8, "cal_accuracy": 0.65}]
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs))
    s = manager.job_summary(d.name, events=True)
    assert s["curve"] == [{"step": 8, "accuracy": 0.65}]          # the new attempt's curve only
    assert s["baseline_accuracy"] == 0.5 and "practising again" in s["notes"]
    assert abs(s["progress"] - (0.1 + 0.78 * 8 / 40)) < 1e-9       # from the latest step, not the stage start
    assert s["eta_seconds"] == 32                                   # 1 s per step, 32 steps left


def test_supervisor_ends_a_silent_engine_and_reports_an_unexplained_exit(tmp_path, monkeypatch):
    import json
    import sys
    from basal.training import job
    monkeypatch.setattr(job, "STALL_DEFAULT", 2)
    (tmp_path / "status.json").write_text(json.dumps({"state": "running", "stage": "train"}))
    statuses = []
    hang = [sys.executable, "-c", "import time; time.sleep(60)"]
    assert job._supervise(tmp_path, lambda *a, **k: statuses.append(a), hang) == "stalled"
    alive = [sys.executable, "-c", f"import pathlib, time\nfor _ in range(4): pathlib.Path({str(tmp_path / 'alive')!r}).touch(); time.sleep(1)"]
    assert job._supervise(tmp_path, lambda *a, **k: None, alive) == 0          # a heartbeat keeps it going
    events = []
    job._settle(tmp_path, -9, events.append, lambda state, **kw: statuses.append((state, kw)))
    assert statuses[-1][0] == "failed" and "ran out of memory" in statuses[-1][1]["message"]

"""Import the Activity log that came before history (DATA/activity.jsonl) as decisions, then rename it to
activity.jsonl.imported. Normal retention applies to the imported decisions afterwards."""
import json


def run(c, database):
    from basal import history
    from basal.contract import render
    from basal.ids import new_id
    from basal.templates import sha

    src = database.data_dir / "activity.jsonl"
    if not src.exists():
        return
    n = 0
    for line in src.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
            req = e.get("request") or {}
            questions = req.get("questions") or {}
            if not isinstance(questions, dict) or not questions:
                continue
            ok = e.get("status") == 200
            res = e.get("response") or {}
            answers = res.get("answers") if ok and isinstance(res.get("answers"), dict) else None
            act = lowest = None
            if answers:
                per = {k: {"act_threshold": 0.9} for k in answers}
                act, _, lowest = history.gate_answers(answers, per, {}, set())
            client = e.get("client")
            path = e.get("path") or "/v1/systemone"
            surface = "playground" if client == "Studio" else "compare" if path == "/api/compare" else "imported"
            t = int(float(e.get("time") or 0) * 1000)
            state = req.get("state")
            rec = history.Record(
                id=new_id("dec", t), created_ms=t, completed_ms=t, status="completed" if ok else "failed", storage="full",
                source={"surface": surface, "endpoint": path, "format": e.get("format") or "typesafe", "client": client,
                        "request_id": e.get("request_id"), "attempt": 0},
                template=None, model=e.get("model"), model_requested=req.get("model"), questions=questions,
                input_hash=sha({"state": render(state), "media": []}), state=state, rendered_state=render(state),
                settings={"act_threshold": 0.9, "temperature": (req.get("settings") or {}).get("temperature") or 1.0,
                          "questions": {}, "sources": {"act_threshold": "studio", "temperature": "studio"}},
                answers=answers, act=act, min_certainty=lowest,
                timing={"queue_ms": None, "load_ms": None, "model_ms": e.get("latency_ms"), "total_ms": e.get("wall_ms")},
                usage=res.get("usage") or {}, passes=res.get("passes"), notes=res.get("notes") or [],
                metadata={"basal.legacy_id": str(e.get("id"))},
                error=None if ok else {"type": "api_error", "code": "imported_failure",
                                       "message": str(res.get("detail") or res)[:2000]},
                http_status=e.get("status"))
            history.insert(c, rec, search=False)
            n += 1
        except Exception as err:  # noqa: BLE001 - one bad line never blocks the rest
            print(f"[history] skipped an activity line: {err}", flush=True)
    src.rename(src.with_name("activity.jsonl.imported"))
    print(f"[history] imported {n} decisions from the old activity log", flush=True)

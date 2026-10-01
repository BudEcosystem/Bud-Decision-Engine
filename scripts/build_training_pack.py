"""Build basal/training/assets/general.jsonl: the general decision examples every fine-tune replays (so the model
keeps its existing skills) and is checked against (the "guard" set that detects forgetting).

Sources (both Apache-2.0, see basal/training/assets/NOTICE):
- LocalLLaMA/typed-decisions: business workflows with choice, yes/no and scale questions on JSON states;
- fastino/fast-decisions: 17 domains of single- and multi-label decisions on text.

Records are split by record into "replay" and "guard" so the guard set is never trained on. Replay takes every record
it may: a small replay set is seen many times per run and gets fitted exactly, which no longer protects anything else
(basal/training/engine.py, `_pack_units`). Run once on a machine with
the datasets downloaded (training_research/datasets/hf) and pyarrow available:

    PYTHONPATH=<dir with pyarrow> .venv/bin/python scripts/build_training_pack.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "training_research" / "datasets" / "hf"
OUT = ROOT / "basal" / "training" / "assets" / "general.jsonl"


def split_of(key: str, guard_share: float) -> str:
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "guard" if h < guard_share else "replay"


def typed_decisions(max_train: int = 1200, max_test: int = 80):
    import pyarrow.parquet as pq
    out = []
    for split, cap, which in (("train", max_train, "replay"), ("test", max_test, "guard")):
        rows = pq.read_table(SRC / "LocalLLaMA__typed-decisions" / "all" / f"{split}-00000-of-00001.parquet").to_pylist()
        per_wf: dict[str, int] = {}
        for r in rows:
            wf = r["workflow"]
            if per_wf.get(wf, 0) >= cap // 4:
                continue
            per_wf[wf] = per_wf.get(wf, 0) + 1
            state, questions, gold = (json.loads(r[k]) if isinstance(r[k], str) else r[k] for k in ("state", "questions", "gold"))
            answers = {}
            for qid, g in gold.items():
                qt = questions[qid]["type"]
                lab = g["label"]
                answers[qid] = (str(lab).lower() == "true") if qt == "noul" else int(lab) if qt == "score" else lab
            out.append({"id": f"td-{r['id']}", "state": state, "questions": questions, "answers": answers,
                        "split": which, "source": "typed-decisions"})
    return out


def fast_decisions(per_file_replay: int = 100, per_file_guard: int = 12):
    out = []
    for f in sorted((SRC / "fastino__fast-decisions").glob("*.jsonl")):
        counts = {"replay": 0, "guard": 0}
        for i, line in enumerate(f.read_text().splitlines()):
            if not line.strip():
                continue
            r = json.loads(line)
            which = split_of(f"{f.stem}:{i}", 0.25)
            cap = per_file_replay if which == "replay" else per_file_guard
            if counts[which] >= cap:
                continue
            questions, answers = {}, {}
            for c in r["output"]["classifications"]:
                labels = [str(x) for x in c["labels"]]
                task = c["task"]
                instr = task.replace("_", " ").strip()
                if sorted(l.lower() for l in labels) == ["no", "yes"]:
                    questions[task] = {"type": "noul", "instructions": f"{instr[:1].upper()}{instr[1:]}?"}
                    answers[task] = str(c["true_label"][0]).lower() == "yes"
                elif c.get("multi_label"):
                    questions[task] = {"type": "multi", "instructions": f"Which {instr} apply?", "criteria": {l: None for l in labels}}
                    answers[task] = [str(x) for x in c["true_label"]]
                else:
                    questions[task] = {"type": "choice", "instructions": f"Which {instr} fits best?",
                                       "criteria": {l: None for l in labels}}
                    answers[task] = str(c["true_label"][0])
            out.append({"id": f"fd-{f.stem}-{i}", "state": r["input"], "questions": questions, "answers": answers,
                        "split": which, "source": "fast-decisions"})
            counts[which] += 1
    return out


def main():
    recs = typed_decisions() + fast_decisions()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    by = {}
    for r in recs:
        by[(r["source"], r["split"])] = by.get((r["source"], r["split"]), 0) + 1
    print(OUT, f"{OUT.stat().st_size / 1e6:.2f} MB", by)


if __name__ == "__main__":
    main()

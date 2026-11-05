"""Quality checks beyond the judges.

1. No-AI baseline: per episode, end on the episode's final beat and start at the hottest moment
   20–60 s earlier (code-only heat). Judged by the SAME judges, so the comparison is fair.
2. Model comparison: how many of one run's top promos another run also found (footage overlap).
3. Human grading: export the top promos for a person to grade; score the tool against them.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .heat import block_heat
from .parse import Show
from .validate import Candidate, validate


def baseline_candidates(show: Show, rules: dict, as_candidates: bool = False) -> list[Candidate]:
    """One code-only promo per episode: open on the hottest moment of the last minute, end on the
    writers' own episode ending. As an eval baseline it shows what the LLM adds; as a candidate
    source (`as_candidates`) it competes with LLM promos in front of the same judges."""
    out = []
    lo, hi = rules["min_seconds"], rules["max_seconds"]
    for ep in show.episodes:
        story = [b for b in ep.blocks if b.kind in ("dialogue", "action")]
        if not story:
            continue
        last = max(story, key=lambda b: (b.end, b.idx))
        window = [b for b in story if last.end - hi + 3 <= b.start <= last.end - lo]
        if not window:
            continue
        hook = max(window, key=lambda b: (block_heat(b), -b.start))
        opener = hook.text if len(hook.text) <= 38 else hook.text[:37].rsplit(" ", 1)[0] + "…"
        idea = {"title": f"E{ep.number} ending · {opener}" if as_candidates else f"Baseline E{ep.number:02d}",
                "angle": "episode_ending" if as_candidates else "baseline",
                "segments": [{"start": hook.id, "end": last.id}], "hook_block": hook.id,
                "hook": hook.text, "cliffhanger_block": last.id, "cliffhanger": last.text,
                "open_question": "What happens next?" if as_candidates else "",
                "why": "Ends on the writers' own episode cliffhanger; opens on the hottest moment of its final "
                       "minute (code-only pick)" if as_candidates else
                       "no-AI heuristic: hottest moment in the last minute → episode end"}
        out.append(validate(idea, ep.number, show, f"B{ep.number:02d}", rules,
                            source="heuristic" if as_candidates else "baseline"))
    return out


def _footage(rec: dict) -> set[tuple[int, int]]:
    return {(s["episode"], t) for s in rec["segments"] for t in range(int(s["start_s"] * 2), int(s["end_s"] * 2))}


def top_overlap(run_a: list[dict], run_b: list[dict], k: int = 10, threshold: float = 0.4) -> tuple[int, list[str]]:
    """How many of run A's top-k promos also appear (≥ threshold shared footage) in run B's top-k."""
    a = [r for r in run_a if r["rank"]][:k]
    b = [r for r in run_b if r["rank"]][:k]
    hits = []
    for ra in a:
        fa = _footage(ra)
        if any(len(fa & _footage(rb)) / max(1, min(len(fa), len(_footage(rb)))) >= threshold for rb in b):
            hits.append(ra["id"])
    return len(hits), hits


def export_grading_sheet(records: list[dict], path: Path, k: int = 15) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "id", "title", "segments", "duration_s", "hook_line", "cliffhanger_line",
                    "your_grade (good / ok / bad)", "your_note"])
        for r in [r for r in records if r["rank"]][:k]:
            segs = " + ".join(f"E{s['episode']:02d} {s['start']}–{s['end']}" for s in r["segments"])
            w.writerow([r["rank"], r["id"], r["title"], segs, r["duration"], r["hook"]["script_line"],
                        r["cliffhanger"]["script_line"], "", ""])


def score_grades(path: Path) -> dict:
    rows = list(csv.DictReader(open(path)))
    grades = [r["your_grade (good / ok / bad)"].strip().lower() for r in rows]
    graded = [g for g in grades if g in ("good", "ok", "bad")]
    return {"graded": len(graded), "good": graded.count("good"), "ok": graded.count("ok"),
            "bad": graded.count("bad"),
            "precision_good_or_ok": round(sum(g in ("good", "ok") for g in graded) / max(1, len(graded)), 2)}


def load_run(run_dir: str | Path) -> list[dict]:
    return json.loads((Path(run_dir) / "promos.json").read_text())

"""End to end: parse → mine → validate → stitch → validate → judge → rank → report."""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Callable

from .config import make_llm
from .evaluate import baseline_candidates
from .judge import judge
from .mine import mine
from .parse import load_show
from .rank import rank
from .report import write_outputs
from .stitch import stitch
from .validate import RE_ID, validate


def select_episodes(show, wanted: list[int] | None) -> tuple[list[int], list[int]]:
    """Requested episodes that exist in the show (all of them if none requested or none match)."""
    present = [ep.number for ep in show.episodes]
    if not wanted:
        return present, []
    eps = [n for n in wanted if n in set(present)]
    skipped = [n for n in wanted if n not in set(present)]
    return (eps or present), skipped


def _first_episode(idea: dict) -> int:
    m = RE_ID.match(str(idea["segments"][0]["start"]).strip()) if idea.get("segments") else None
    return int(m.group(1)) if m and m.group(1) else 1


def run(input_path: str | Path, out_dir: str | Path, cfg: dict, generator: str | None = None,
        judge_model: str | None = None, episodes: list[int] | None = None, title: str | None = None,
        stitch_n: int = 5, progress: Callable[[str], None] = lambda m: print(m, flush=True)) -> list[dict]:
    t0 = time.time()
    show = load_show(input_path, title=title)
    eps, skipped = select_episodes(show, episodes)
    log: list[dict] = []
    gen = make_llm(cfg, "generator", generator, log)
    jud = make_llm(cfg, "judge", judge_model, log)
    progress(f"1/5 Parsed {len(show.episodes)} episodes of “{show.title}”"
             + (f" ({len(show.warnings)} warnings)" if show.warnings else "")
             + (f"; skipped episodes not in this show: {skipped}" if skipped else ""))

    ideas = mine(show, gen, eps, per_call=cfg["budget"]["episodes_per_call"])
    ideas_json = [e.model_dump() for e in ideas]
    candidates = [validate(p, e["episode"], show, f"E{e['episode']:02d}-P{k}", cfg["promo"])
                  for e in ideas_json for k, p in enumerate(e["promos"], 1)]
    progress(f"2/5 Mined {len(candidates)} single-episode promo ideas from {len(eps)} episodes")

    if stitch_n and len(eps) > 1:
        rules = {**cfg["promo"], "max_segments": 4}
        def check(p: dict, k: int):
            return validate(p, _first_episode(p), show, f"T{k}", rules, source="stitch")

        trailers = [p.model_dump() for p in stitch(show, ideas, gen, n=stitch_n, check=check)]
        stitched = [check(p, k) for k, p in enumerate(trailers, 1)]
        candidates += stitched
        progress(f"3/5 Stitched {len(trailers)} cross-episode trailers "
                 f"({sum(c.status != 'rejected' for c in stitched)} pass the checks)")

    rounds = cfg["budget"].get("judge_rounds", 3)
    batch = cfg["budget"]["promos_per_judge_call"]
    verdicts = judge(show, candidates, jud, batch_size=batch, rounds=rounds)
    if cfg.get("candidates", {}).get("episode_endings", True):
        # Evaluation showed the writers' own episode endings make the strongest cliffhangers, so a
        # code-only "episode ending" promo per episode competes too, in front of the same judges.
        endings = [c for c in baseline_candidates(show, cfg["promo"], as_candidates=True) if c.episode in eps]
        verdicts.update(judge(show, endings, jud, batch_size=batch, rounds=rounds))
        candidates += endings
    progress(f"4/5 Judged {len(verdicts)} valid promos × {rounds} rounds (scroll test, cold viewer, comprehension)")

    ranked = rank(show, candidates, verdicts, cfg["promo"], cfg["ranking"])
    status = Counter(c.status for c in candidates)
    live = [c for c in log if not c["cached"]]
    meta = {
        # name the models that actually answered (a fallback may have stood in for the first choice)
        "show": show.title, "episodes": len(eps),
        "generator": " + ".join(sorted({c["model"] for c in log if c["tag"].startswith(("mine", "stitch"))}))
                     or gen.chain[0],
        "judge": " + ".join(sorted({c["model"] for c in log if c["tag"].startswith("judge")})) or jud.chain[0],
        "finished": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0, 1),
        "counts": {"ideas": len(candidates), "ok": status["ok"], "repaired": status["repaired"],
                   "rejected": status["rejected"], "duplicates": sum(1 for r in ranked if r.duplicate_of)},
        "live_requests": len(live), "cache_hits": len(log) - len(live),
        "tokens_in": sum(c["in"] for c in live), "tokens_out": sum(c["out"] for c in live),
        "usage_by_model": {**gen.usage(), **jud.usage()},
        "fallbacks": [c for c in log if c.get("fell_back_from")],
        "config": cfg,
    }
    records = write_outputs(Path(out_dir), show, ideas_json, candidates, ranked, log, meta, gen.raw_reply)
    progress(f"5/5 Ranked {sum(1 for r in ranked if r.rank)} promos → {out_dir}")
    return records

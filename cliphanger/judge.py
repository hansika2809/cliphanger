"""Stage 5 · JUDGE (LLM, ideally a different model from the generator): simulate real viewers.

  1. 3-second scroll test: sees ONLY what plays in the promo's first 3 s. Would you stop?
  2. Cold-viewer test: sees the whole clip, with no title, episode or logline. Do you follow it,
     do you need to know what happens next, did the clip already resolve itself?
  3. Comprehension check: is the cold viewer's understanding actually right (vs. the logline)?

Clips get opaque ids ("clip-07") and are shuffled across the show, so the judge can't infer
context from episode numbers or neighbouring clips.
"""
from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from .clip import clip_blocks, render_clip
from .llm import LLM, LLMError
from .parse import Show
from .validate import Candidate

PROMPTS = Path(__file__).parent / "prompts"


class ScrollResult(BaseModel):
    id: str
    stop: float
    what_stops_me: str = ""


class ColdResult(BaseModel):
    id: str
    understood: str = ""
    left_wondering: str = ""
    clarity: float = 0
    cliffhanger: float = 0
    already_resolved: bool = False
    confusing: list[str] = []


class TruthResult(BaseModel):
    id: str
    match: int = 1
    misread: str = ""


class ScrollReply(BaseModel):
    results: list[ScrollResult]


class ColdReply(BaseModel):
    results: list[ColdResult]


class TruthReply(BaseModel):
    results: list[TruthResult]


@dataclass
class Verdict:
    hook: float | None = None             # 0–10, 3-second scroll test
    hook_reason: str = ""
    clarity: float | None = None          # 0–10, cold viewer
    cliffhanger: float | None = None      # 0–10, cold viewer
    resolved: bool | None = None
    understood: str = ""
    left_wondering: str = ""
    confusing: list[str] = field(default_factory=list)
    comprehension: float | None = None    # 0–2 vs. the true logline (mean over rounds)
    misread: str = ""
    rounds: int = 1
    spread: dict[str, float] = field(default_factory=dict)   # max − min across rounds, per score


def combine(rounds: list[Verdict]) -> Verdict:
    """Average independent judging rounds; keep the spread so disagreement is visible."""
    def vals(attr: str) -> list[float]:
        return [getattr(v, attr) for v in rounds if getattr(v, attr) is not None]

    def mean(attr: str) -> float | None:
        xs = vals(attr)
        return round(sum(xs) / len(xs), 2) if xs else None

    votes = [v.resolved for v in rounds if v.resolved is not None]
    first = next((v for v in rounds if v.understood), rounds[0])
    return Verdict(
        hook=mean("hook"), hook_reason=next((v.hook_reason for v in rounds if v.hook_reason), ""),
        clarity=mean("clarity"), cliffhanger=mean("cliffhanger"),
        resolved=(sum(votes) > len(votes) / 2) if votes else None,
        understood=first.understood, left_wondering=first.left_wondering, confusing=first.confusing,
        comprehension=mean("comprehension"), misread=next((v.misread for v in rounds if v.misread), ""),
        rounds=len(rounds),
        spread={k: round(max(xs) - min(xs), 2) for k in ("hook", "cliffhanger", "clarity", "comprehension")
                if len(xs := vals(k)) > 1},
    )


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def judge(show: Show, candidates: list[Candidate], llm: LLM, batch_size: int = 8,
          workers: int = 3, rounds: int = 3) -> dict[str, Verdict]:
    """Judge every valid promo in `rounds` independent rounds (different shuffles → different
    batch neighbours) and average them: a single LLM judging pass is noticeably noisy."""
    seeds = [7, 11, 13, 17, 19][:max(1, rounds)]
    per_round = [_judge_once(show, candidates, llm, batch_size, workers, s) for s in seeds]
    return {cid: combine([r[cid] for r in per_round]) for cid in per_round[0]}


def _judge_once(show: Show, candidates: list[Candidate], llm: LLM, batch_size: int,
                workers: int, seed: int) -> dict[str, Verdict]:
    live = [c for c in candidates if c.status != "rejected"]
    order = live[:]
    random.Random(seed).shuffle(order)
    alias = {c.id: f"clip-{i + 1:02d}" for i, c in enumerate(order)}
    back = {v: k for k, v in alias.items()}
    verdicts = {c.id: Verdict() for c in live}
    timed = {c.id: clip_blocks(show, c.cuts) for c in live}

    def run_stage(system_file: str, schema, items: list[Candidate], size: int, render, apply, tag: str):
        system = (PROMPTS / system_file).read_text()

        def one(chunk: list[Candidate]):
            user = "\n\n".join(render(c) for c in chunk)
            first = alias[chunk[0].id]
            try:
                return llm.json(system, user, schema=schema, tag=f"judge:{tag}:s{seed}:{first}+{len(chunk)}")
            except LLMError:      # degrade gracefully: these clips stay unjudged for this round
                return None

        pending = items
        for _attempt in range(2):                       # second pass re-asks for anything skipped
            if not pending:
                break
            with ThreadPoolExecutor(max_workers=workers) as pool:
                replies = list(pool.map(one, _chunks(pending, size)))
            for reply in replies:
                if reply is None:
                    continue
                for r in reply.results:
                    if r.id in back and back[r.id] in verdicts:
                        apply(verdicts[back[r.id]], r)
            pending = [c for c in pending if _missing(verdicts[c.id], tag)]

    def apply_scroll(v: Verdict, r: ScrollResult):
        v.hook, v.hook_reason = r.stop, r.what_stops_me

    def apply_cold(v: Verdict, r: ColdResult):
        v.understood, v.left_wondering = r.understood, r.left_wondering
        v.clarity, v.cliffhanger, v.resolved, v.confusing = r.clarity, r.cliffhanger, r.already_resolved, r.confusing

    def apply_truth(v: Verdict, r: TruthResult):
        v.comprehension, v.misread = max(0, min(2, r.match)), r.misread

    run_stage("judge_scroll.md", ScrollReply, order, batch_size * 3,
              lambda c: f"### {alias[c.id]}\n{render_clip(timed[c.id], until=3.0) or '(nothing happens)'}",
              apply_scroll, "scroll")
    run_stage("judge_cold.md", ColdReply, order, batch_size,
              lambda c: f"### {alias[c.id]} ({c.duration:.0f} s)\n{render_clip(timed[c.id])}",
              apply_cold, "cold")

    def truth_item(c: Candidate) -> str:
        eps = sorted({cut.episode for cut in c.cuts})
        loglines = " / ".join(show.episode(n).logline for n in eps)
        return f"### {alias[c.id]}\nViewer understood: {verdicts[c.id].understood}\nTrue logline: {loglines}"

    run_stage("judge_truth.md", TruthReply, [c for c in order if verdicts[c.id].understood], 25,
              truth_item, apply_truth, "truth")
    return verdicts


def _missing(v: Verdict, stage: str) -> bool:
    return {"scroll": v.hook is None, "cold": v.clarity is None, "truth": v.comprehension is None}[stage]

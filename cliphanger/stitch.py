"""Stage 4 · STITCH (LLM): join the strongest moments of DIFFERENT episodes into trailer promos.

Map → reduce: mining (per episode) produced beats; here one request sees only the strongest
beats across the whole show, each with the lines around it, so it can choose clean segments.
Code then checks every trailer, and the exact reasons for any rejection go back to the model
for one revision round ("code checks, AI revises").
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .llm import LLM
from .mine import EpisodeIdeas, PromoIdea, series_context
from .parse import Show, block_line
from .validate import Candidate, normalize_id

PROMPTS = Path(__file__).parent / "prompts"


class StitchReply(BaseModel):
    promos: list[PromoIdea]


def beat_windows(show: Show, ideas: list[EpisodeIdeas], max_beats: int = 32,
                 before: float = 14.0, after: float = 12.0) -> str:
    beats = []
    for ep in ideas:
        for bt in ep.beats:
            bid = normalize_id(bt.block, ep.episode)
            if show.has_block(bid):
                beats.append((bt.intensity, -ep.episode, bid, bt.kind, bt.what))
    beats.sort(reverse=True)                      # strongest first; earlier episodes win ties
    chosen = sorted(beats[:max_beats], key=lambda b: (-b[1], b[2]))   # back to story order
    parts = []
    for intensity, _, bid, kind, what in chosen:
        b = show.block(bid)
        window = [x for x in show.episode(b.episode).blocks if b.start - before <= x.start <= b.start + after]
        lines = "\n".join(block_line(x, global_id=True) for x in window)
        parts.append(f"### {bid} · {kind} · intensity {intensity} · {what}\n{lines}")
    return "\n\n".join(parts)


def stitch(show: Show, ideas: list[EpisodeIdeas], llm: LLM, n: int = 5,
           check: Callable[[dict, int], Candidate] | None = None) -> list[PromoIdea]:
    system = ((PROMPTS / "stitch.md").read_text()
              .replace("{{show_title}}", show.title)
              .replace("{{loglines}}", series_context(show))
              .replace("{{n}}", str(n)))
    user = "Strongest moments across the show:\n\n" + beat_windows(show, ideas)
    promos = llm.json(system, user, schema=StitchReply, tag="stitch").promos
    if check is None:
        return promos

    verdicts = [check(p.model_dump(), k) for k, p in enumerate(promos, 1)]
    rejected = [(k, p, v) for k, (p, v) in enumerate(zip(promos, verdicts), 1) if v.status == "rejected"]
    if not rejected:
        return promos
    feedback = "\n".join(f'- Trailer {k} "{p.title}": REJECTED because {"; ".join(v.issues)}. '
                         f"Its segments currently last {', '.join(f'{c.duration:.1f}s' for c in v.cuts) or 'n/a'}."
                         for k, p, v in rejected)
    revise = (f"{user}\n\n## Your previous answer\n{json.dumps({'promos': [p.model_dump() for p in promos]})}\n\n"
              f"## The editor's automatic checks\n{feedback}\n\nReturn all {len(promos)} trailers again. Keep the "
              f"accepted ones unchanged; fix the rejected ones (usually: extend each segment to 8–20 s using "
              f"the neighbouring blocks shown, so the total is 30–60 s).")
    return llm.json(system, revise, schema=StitchReply, tag="stitch:revise").promos

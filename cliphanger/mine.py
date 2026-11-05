"""Stage 2 · MINE (LLM): read episodes, propose hooks, cliffhangers and promo ideas.

The model cites moments by block ID only; timestamps are computed later, in code.
Episodes are sent in small batches so a whole show fits in a few free-tier requests.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, field_validator

from .llm import LLM
from .parse import Show, render_for_llm

PROMPTS = Path(__file__).parent / "prompts"
ANGLES = ["betrayal", "humiliation", "secret_identity", "rescue", "revenge",
          "injustice", "romance", "danger", "villain_scheme"]


class Beat(BaseModel):
    block: str
    kind: str = "hook"
    intensity: int = 5
    what: str = ""


class Segment(BaseModel):
    start: str
    end: str


class PromoIdea(BaseModel):
    title: str
    angle: str = "other"
    segments: list[Segment]
    hook_block: str
    hook: str
    cliffhanger_block: str
    cliffhanger: str
    open_question: str = ""
    payoff_episode: int | None = None
    why: str
    est_seconds: float | None = None
    self_score: int | None = None

    @field_validator("payoff_episode", mode="before")
    @classmethod
    def _episode_number(cls, v):  # accepts 2, "2", "E02", None
        if v is None or isinstance(v, int):
            return v
        m = re.search(r"\d+", str(v))
        return int(m.group()) if m else None


class EpisodeIdeas(BaseModel):
    episode: int
    beats: list[Beat] = []
    promos: list[PromoIdea] = []


class MiningReply(BaseModel):
    episodes: list[EpisodeIdeas]


def series_context(show: Show) -> str:
    return "\n".join(f"E{ep.number:02d}: {ep.logline}" for ep in show.episodes)


def system_prompt(show: Show) -> str:
    return ((PROMPTS / "mine.md").read_text()
            .replace("{{show_title}}", show.title)
            .replace("{{loglines}}", series_context(show)))


def _tag(batch: list[int]) -> str:
    contiguous = batch == list(range(batch[0], batch[-1] + 1))
    if len(batch) > 1 and contiguous:
        return f"mine:E{batch[0]:02d}-E{batch[-1]:02d}"
    return "mine:" + ",".join(f"E{n:02d}" for n in batch)


def mine(show: Show, llm: LLM, episodes: list[int], per_call: int = 5, workers: int = 3) -> list[EpisodeIdeas]:
    """Batches run in parallel (a few at a time, to respect free-tier rate limits)."""
    from concurrent.futures import ThreadPoolExecutor

    system = system_prompt(show)
    batches = [episodes[i:i + per_call] for i in range(0, len(episodes), per_call)]

    def run(batch: list[int]) -> list[EpisodeIdeas]:
        user = "Episodes to analyse:\n\n" + "\n\n".join(
            render_for_llm(show.episode(n), global_ids=True) for n in batch)
        reply: MiningReply = llm.json(system, user, schema=MiningReply, tag=_tag(batch))
        by_number = {e.episode: e for e in reply.episodes}
        return [by_number.get(n, EpisodeIdeas(episode=n)) for n in batch]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [ep for batch_result in pool.map(run, batches) for ep in batch_result]

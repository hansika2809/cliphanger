"""Stage 6 · RANK (code only): one transparent score per promo, de-duplication, posting slate.

final = Σ weight × sub-score (each 0–10), minus a penalty when the cold viewer felt the clip
already resolves itself. Weights live in config.yaml so marketing can tune them, and every
sub-score is kept in the output, so a rank is always explainable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .clip import clip_blocks, name_drift
from .judge import Verdict
from .parse import Show
from .validate import Candidate


@dataclass
class Ranked:
    candidate: Candidate
    verdict: Verdict
    scores: dict[str, float]
    flags: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    rank: int | None = None
    post_first: bool = False


def length_fit(seconds: float, rules: dict) -> float:
    lo, hi = rules["min_seconds"], rules["max_seconds"]
    a, b = rules["sweet_spot"]
    if a <= seconds <= b:
        return 10.0
    if seconds < a:
        return round(6 + 4 * max(0.0, seconds - lo) / (a - lo), 2)
    return round(6 + 4 * max(0.0, hi - seconds) / (hi - b), 2)


def early_episode(episode: int, n_episodes: int) -> float:
    """A new viewer starts at E1, so early promos lead straight into what they'll watch next."""
    return round(10 * (1 - (episode - 1) / max(1, n_episodes - 1)), 2)


def _footage(c: Candidate) -> set[tuple[int, int]]:
    return {(cut.episode, t) for cut in c.cuts for t in range(int(cut.start * 2), int(cut.end * 2))}


def overlap(a: Candidate, b: Candidate) -> float:
    """Share of the shorter promo's footage that the other promo also uses."""
    fa, fb = _footage(a), _footage(b)
    return len(fa & fb) / max(1, min(len(fa), len(fb)))


def rank(show: Show, candidates: list[Candidate], verdicts: dict[str, Verdict], rules: dict, rcfg: dict) -> list[Ranked]:
    weights = rcfg["weights"]
    n = len(show.episodes)
    rows: list[Ranked] = []
    for c in candidates:
        if c.status == "rejected":
            continue
        v = verdicts.get(c.id, Verdict())
        s = {
            "hook": v.hook or 0.0,
            "cliffhanger": v.cliffhanger or 0.0,
            "clarity": v.clarity or 0.0,
            "comprehension": 5.0 * (v.comprehension if v.comprehension is not None else 1),
            "length": length_fit(c.duration, rules),
            "early_episode": early_episode(min(cut.episode for cut in c.cuts), n),
        }
        final = sum(weights[k] * s[k] for k in weights)
        flags: list[str] = []
        if v.resolved:
            final -= rcfg["resolved_penalty"]
            flags.append("cold viewer: the clip already resolves itself")
        if v.hook is None or v.clarity is None:
            flags.append("not fully judged")
        if v.confusing:
            flags.append("confusing for newcomers: " + ", ".join(map(str, v.confusing[:3])))
        if v.comprehension is not None and v.comprehension < 0.67:
            flags.append(f"cold viewer misread it: {v.misread}")
        split = {k: s for k, s in v.spread.items() if s >= (2.0 if k == "comprehension" else 4.0)}
        if split:
            flags.append("judges disagree (human check): " + ", ".join(f"{k} spread {s:g}" for k, s in split.items()))
        for a, b in name_drift(clip_blocks(show, c.cuts)):
            flags.append(f"name spelled two ways: {a} / {b}")
        rows.append(Ranked(c, v, {**{k: round(x, 2) for k, x in s.items()}, "final": round(final, 2)}, flags))

    rows.sort(key=lambda r: -r.scores["final"])
    kept: list[Ranked] = []
    for r in rows:
        dup = next((k for k in kept if overlap(r.candidate, k.candidate) > rcfg["duplicate_overlap"]), None)
        if dup:
            r.duplicate_of = dup.candidate.id
        else:
            kept.append(r)
    for i, r in enumerate(kept, 1):
        r.rank = i

    angles: set[str] = set()
    for r in kept:                       # posting slate: best promo of each story angle
        angle = r.candidate.idea.get("angle", "other")
        if angle not in angles and len(angles) < rcfg["slate_size"] and not r.verdict.resolved:
            angles.add(angle)
            r.post_first = True
    return kept + [r for r in rows if r.duplicate_of]

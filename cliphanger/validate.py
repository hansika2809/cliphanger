"""Stage 3 · VALIDATE + SNAP (code only): turn promo ideas (block IDs) into exact cut lists.

The LLM never computes a timestamp. This module:
  1. maps block IDs to script times (rejects invented IDs);
  2. snaps every cut onto a shot change (CAMERA block edge) and never inside a line of dialogue;
  3. moves the cut-in so the hook plays within the first 3 s, and the cut-out so the promo ends
     on the cliffhanger (holding at most one reaction shot);
  4. re-checks the brief's rules (20–60 s, hook ≤ 3 s, ends on the cliffhanger) and records every
     change it made, so an editor can see exactly what moved and why.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .parse import Block, Show, fmt_time

RE_ID = re.compile(r"^E?(\d+)?-?B(\d+)$", re.I)
EPS = 0.05


@dataclass
class Cut:
    episode: int
    start: float
    end: float
    first_block: str
    last_block: str

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)


@dataclass
class Candidate:
    id: str
    episode: int
    idea: dict
    source: str = "mine"              # mine | stitch
    cuts: list[Cut] = field(default_factory=list)
    duration: float = 0.0
    hook_at: float | None = None      # seconds into the promo where the hook block starts
    cliff_tail: float | None = None   # seconds the promo holds after the cliffhanger ends
    issues: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    original_cuts: list[Cut] = field(default_factory=list)
    status: str = "ok"                # ok | repaired | rejected

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cuts"] = [{**asdict(c), "duration": c.duration} for c in self.cuts]
        d["original_cuts"] = [{**asdict(c), "duration": c.duration} for c in self.original_cuts]
        return d


def normalize_id(raw: str, episode: int) -> str:
    """'E01-B034', 'e1-b34', 'B034' (episode implied) -> 'E01-B034'."""
    m = RE_ID.match(str(raw).strip())
    if not m:
        return str(raw).strip()
    ep = int(m.group(1)) if m.group(1) else episode
    return f"E{ep:02d}-B{int(m.group(2)):03d}"


# ── per-episode cut index: where shots change, where lines play ─────────────────
@dataclass
class _Index:
    shot_starts: list[float]
    shot_ends: list[float]
    lines: list[Block]


def _index(show: Show, episode: int) -> _Index:
    cache = show.__dict__.setdefault("_cut_index", {})
    if episode not in cache:
        ep = show.episode(episode)
        cams = [b for b in ep.blocks if b.kind == "camera"]
        starts = {b.start for b in cams} | {s.start for s in ep.scenes if s.start is not None}
        ends = {b.end for b in cams} | {s.end for s in ep.scenes if s.end is not None}
        lines = sorted((b for b in ep.blocks if b.kind == "dialogue"), key=lambda b: b.start)
        cache[episode] = _Index(sorted(starts), sorted(ends), lines)
    return cache[episode]


def _line_at(ix: _Index, t: float) -> Block | None:
    """The dialogue line being spoken at time t, if t falls strictly inside one."""
    return next((b for b in ix.lines if b.start < t - EPS and b.end > t + EPS), None)


def snap_in(ix: _Index, target: float, lo: float, hi: float) -> float:
    """Cut-in point in [lo, hi] nearest `target`: prefer a shot start; never mid-line."""
    lo = max(lo, 0.0)
    shots = [s for s in ix.shot_starts if lo - EPS <= s <= hi + EPS]
    t = min(shots, key=lambda s: abs(s - target)) if shots else min(max(target, lo), hi)
    if line := _line_at(ix, t):
        t = line.start if line.start >= lo - EPS else line.end
    return round(t, 2)


def snap_out(ix: _Index, target: float, lo: float, hi: float) -> float:
    """Cut-out point in [lo, hi] nearest `target`: prefer a shot end; never mid-line.
    A line that starts inside the window is cut before it; one already playing is finished."""
    shots = [s for s in ix.shot_ends if lo - EPS <= s <= hi + EPS]
    t = min(shots, key=lambda s: abs(s - target)) if shots else min(max(target, lo), hi)
    if line := _line_at(ix, t):
        t = line.start if line.start >= lo - EPS else line.end
    return round(t, 2)


# ── locating blocks inside a cut list ──────────────────────────────────────────
def _locate(cuts: list[Cut], block: Block) -> tuple[int, float] | None:
    """(cut index, seconds into the promo) where `block` starts, if it plays inside a cut."""
    offset = 0.0
    for i, cut in enumerate(cuts):
        if block.episode == cut.episode and cut.start - EPS <= block.start < cut.end - EPS:
            return i, round(offset + max(0.0, block.start - cut.start), 2)
        offset += cut.duration
    return None


def _measure(c: Candidate, hook: Block | None, cliff: Block | None) -> list[str]:
    """Recompute length / hook position / cliffhanger tail; return hard problems."""
    hard: list[str] = []
    c.duration = round(sum(cut.duration for cut in c.cuts), 2)
    c.hook_at = c.cliff_tail = None
    if hook is not None:
        pos = _locate(c.cuts, hook)
        if pos is None:
            hard.append("hook block is not inside the promo")
        else:
            c.hook_at = pos[1]
    if cliff is not None:
        last = c.cuts[-1]
        if not (cliff.episode == last.episode and last.start - EPS <= cliff.start and cliff.end <= last.end + EPS):
            hard.append("promo does not end on its cliffhanger block")
        else:
            c.cliff_tail = round(last.end - cliff.end, 2)
    return hard


# ── main entry point ───────────────────────────────────────────────────────────
def validate(idea: dict, episode: int, show: Show, pid: str, rules: dict, source: str = "mine") -> Candidate:
    c = Candidate(id=pid, episode=episode, idea=idea, source=source)
    hard: list[str] = []

    # 1) IDs → times
    for seg in idea.get("segments", []):
        a, b = normalize_id(seg["start"], episode), normalize_id(seg["end"], episode)
        missing = [x for x in (a, b) if not show.has_block(x)]
        if missing:
            hard.append(f"invented block id {', '.join(missing)}")
            continue
        first, last = show.block(a), show.block(b)
        if first.episode != last.episode:
            hard.append(f"one segment spans two episodes ({a} → {b})")
            continue
        if first.idx > last.idx:
            first, last = last, first
            c.repairs.append(f"segment {a} → {b} was given backwards; flipped")
        span = show.episode(first.episode).blocks[first.idx - 1:last.idx]
        c.cuts.append(Cut(first.episode, min(x.start for x in span), max(x.end for x in span), first.id, last.id))
    if len(idea.get("segments", [])) > rules.get("max_segments", 3):
        hard.append(f"{len(idea['segments'])} segments (max {rules.get('max_segments', 3)})")

    hook_id = normalize_id(idea.get("hook_block", ""), episode)
    cliff_id = normalize_id(idea.get("cliffhanger_block", ""), episode)
    hook = show.block(hook_id) if show.has_block(hook_id) else None
    cliff = show.block(cliff_id) if show.has_block(cliff_id) else None
    if hook is None:
        hard.append(f"invented hook block {hook_id}")
    if cliff is None:
        hard.append(f"invented cliffhanger block {cliff_id}")
    if hard or not c.cuts:
        c.issues, c.status = hard or ["no usable segments"], "rejected"
        return c

    hard += _measure(c, hook, cliff)
    if hard:
        c.issues, c.status = hard, "rejected"
        return c
    c.original_cuts = [Cut(**asdict(x)) for x in c.cuts]

    # 2) snap every cut onto shot changes; enforce hook ≤ H s and end on the cliffhanger
    H = rules["hook_within_seconds"]
    lo, hi = rules["min_seconds"], rules["max_seconds"]
    late_hook = c.hook_at > H
    hook_cut = _locate(c.cuts, hook)[0]

    def snapped(trim_to_hook: bool) -> list[Cut]:
        cuts = []
        for i, cut in enumerate(c.original_cuts):
            ix = _index(show, cut.episode)
            if i == hook_cut == 0 and not late_hook:      # hook already early: tidy the cut-in
                start = snap_in(ix, cut.start, max(hook.start - H, cut.start - 1.5), min(hook.start, cut.start + 1.0))
            elif i == hook_cut == 0 and trim_to_hook:     # hook late: cut in just before it
                start = snap_in(ix, hook.start - 1.0, hook.start - H, hook.start)
            else:
                start = snap_in(ix, cut.start, cut.start - 1.5, cut.start + 1.0)
            if i == len(c.original_cuts) - 1:             # end on the cliffhanger (+ ≤ 1 reaction shot)
                end = snap_out(ix, min(max(cut.end, cliff.end), cliff.end + 2.5), cliff.end, cliff.end + 2.5)
            else:
                end = snap_out(ix, cut.end, cut.end - 1.5, cut.end + 1.5)
            cuts.append(Cut(cut.episode, start, max(end, start), cut.first_block, cut.last_block))
        return cuts

    c.cuts = snapped(trim_to_hook=True)
    hard = _measure(c, hook, cliff)
    flashed = False
    if late_hook and hook_cut == 0 and c.duration < lo:
        # Trimming to the hook threw away too much build-up. Use the classic microdrama ad
        # opening instead: flash-forward to the hook for a few seconds, then play the build-up.
        ix = _index(show, hook.episode)
        fs = snap_in(ix, hook.start, hook.start - 1.0, hook.start)
        fe = snap_out(ix, min(hook.end + 0.5, fs + 4.0), max(hook.end, fs + 1.5), fs + 4.5)
        saved = (c.cuts, c.duration, c.hook_at, c.cliff_tail)
        c.cuts = [Cut(hook.episode, fs, fe, hook.id, hook.id)] + snapped(trim_to_hook=False)
        hard_alt = _measure(c, hook, cliff)
        if not hard_alt and lo <= c.duration <= hi:
            hard, flashed = hard_alt, True
            c.repairs.append(f"flash-forward: opens on the hook (E{hook.episode:02d} {fmt_time(fs)}–{fmt_time(fe)}), "
                             f"then plays the build-up")
        else:
            c.cuts, c.duration, c.hook_at, c.cliff_tail = saved

    for old, new in zip(c.original_cuts, c.cuts[1:] if flashed else c.cuts):
        for label, a, b in (("start", old.start, new.start), ("end", old.end, new.end)):
            if abs(a - b) > 0.5:
                c.repairs.append(f"E{new.episode:02d} {label} {fmt_time(a)} → {fmt_time(b)}")

    # 3) final rule check
    if not lo <= c.duration <= hi:
        hard.append(f"length {c.duration:.1f}s outside {lo}–{hi}s")
    if c.hook_at is not None and c.hook_at > H + EPS:
        hard.append(f"hook still lands at {c.hook_at:.1f}s")
    c.issues = hard
    c.status = "rejected" if hard else ("repaired" if c.repairs else "ok")
    return c

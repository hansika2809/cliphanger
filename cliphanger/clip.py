"""What a promo actually plays: blocks inside its cuts, in play order, timed in promo seconds."""
from __future__ import annotations

import re

from .parse import Block, Show
from .validate import EPS, Cut

LABEL = {"camera": "SHOT", "action": "ACTION", "sfx": "SOUND"}


def clip_blocks(show: Show, cuts: list[Cut]) -> list[tuple[float, Block]]:
    """(seconds into the promo, block) for every block on screen or heard during the promo."""
    out: list[tuple[float, Block]] = []
    offset = 0.0
    for cut in cuts:
        for b in sorted(show.episode(cut.episode).blocks, key=lambda b: (b.start, b.idx)):
            if b.end > cut.start + EPS and b.start < cut.end - EPS:
                out.append((round(offset + max(0.0, b.start - cut.start), 2), b))
        offset += cut.duration
    return out


RE_MID_SENTENCE_NAME = re.compile(r"(?<=[a-z,;] )[A-Z][a-z]{4,}")


def name_drift(timed: list[tuple[float, Block]]) -> list[tuple[str, str]]:
    """Proper nouns spelled two ways inside one promo (e.g. 'Ravenwood' vs 'Ravenswood').
    AI-written scripts drift on names; a promo that shows both confuses new viewers."""
    names = {w.removesuffix("'s") for _, b in timed for w in RE_MID_SENTENCE_NAME.findall(b.text)}
    ordered = sorted(names)
    return [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]
            if a[:5] == b[:5] and not (b.startswith(a) and len(b) - len(a) <= 2)]


def render_clip(timed: list[tuple[float, Block]], until: float | None = None) -> str:
    """Viewer's-eye transcript: no block IDs, no episode numbers."""
    lines = []
    for t, b in timed:
        if until is not None and t >= until:
            break
        if b.kind == "dialogue":
            mood = f" ({', '.join(x for x in b.tags if x not in ('O.S.', 'V.O.'))})" if b.tags else ""
            lines.append(f'{t:4.1f}s  {b.speaker}{mood}: "{b.text}"')
        else:
            lines.append(f"{t:4.1f}s  [{LABEL[b.kind]}] {b.text}")
    return "\n".join(lines)

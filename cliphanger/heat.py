"""Code-only drama intensity ("heat") per block and per second, with no LLM involved.

Signals the screenplay already carries: emotion tags on dialogue ("(Furious)", "(Sobbing)"),
violent or shocking actions ("slap", "chains", "blood"), sound cues, exclamations.
Uses: the show-wide heat-map in the app, and the no-AI baseline promo picker for evaluation.
"""
from __future__ import annotations

import re

from .parse import Block, Episode

EMOTION = {
    3: "furious enraged screaming shrieking sobbing horrified terrified hysterical agonized murderous "
       "venomous seething livid devastated panicked frantic wailing roaring",
    2: "shouting crying desperate menacing threatening cruel mocking gloating smug contemptuous defiant "
       "outraged shocked gasping choking pleading broken trembling furiously icy vicious sneering "
       "bitter accusing incredulous stunned triumphant vengeful cold commanding",
    1: "angry tense stern firm sharp hurt tearful anxious urgent hard scornful dismissive disgusted "
       "fearful nervous haughty proud resolute grim",
}
ACTION = {
    3: "slap slaps slapped strike strikes struck punch punches blood bleeding stab knife dagger sword "
       "explosion explodes fire flames ablaze burning inferno gallows execution chains chained shackle "
       "manacled poison arsenic whip crop collapses faints",
    2: "grabs seizes shoves drags kneels knees falls smash smashes shatter shatters guards arrest "
       "throws hurls rips tears kiss kisses scream screams thorns wound wounded bruised dungeon",
    1: "tears trembling clutches grips glares slams storms burst bursts reveals",
}
_EMO = {w: s for s, words in EMOTION.items() for w in words.split()}
_ACT = {w: s for s, words in ACTION.items() for w in words.split()}
_WORD = re.compile(r"[a-z]+")


def block_heat(b: Block) -> float:
    if b.kind == "dialogue":
        tags = " ".join(b.tags).lower()
        score = max((_EMO.get(w, 0) for w in _WORD.findall(tags)), default=0)
        score += min(2, b.text.count("!")) * 0.75 + (0.5 if b.text.rstrip().endswith("?") else 0)
        return score
    words = _WORD.findall(b.text.lower())
    score = max((_ACT.get(w, 0) for w in words), default=0)
    if b.kind == "sfx":
        score += 1 + (1.5 if "cliffhanger" in words else 0)
    return score * (0.6 if b.kind == "camera" else 1.0)   # camera notes echo the action: count less


def heat_curve(ep: Episode, step: float = 1.0, smooth: int = 3) -> list[float]:
    """Heat per `step` seconds over the episode, smoothed with a moving average."""
    n = int(ep.duration / step) + 1
    raw = [0.0] * n
    for b in ep.blocks:
        h = block_heat(b)
        if h:
            for i in range(int(b.start / step), min(n, int(b.end / step) + 1)):
                raw[i] += h
    half = smooth // 2
    return [round(sum(raw[max(0, i - half): i + half + 1]) / smooth, 2) for i in range(n)]

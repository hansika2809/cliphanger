"""Presentation helpers for the Streamlit app: theme-aware CSS and small HTML components.

Every piece of dynamic text is HTML-escaped: it comes from uploaded scripts and model output.
"""
from __future__ import annotations

import html

ANGLE_ICON = {
    "betrayal": "💔", "humiliation": "🫣", "secret_identity": "🎭", "rescue": "🛡️", "revenge": "🔥",
    "injustice": "⚖️", "romance": "💘", "danger": "⚠️", "villain_scheme": "🐍", "episode_ending": "🎬",
    "power_reversal": "⚡", "sacrifice": "🩸", "baseline": "🧪",
}
SOURCE_NAME = {"mine": "LLM pick", "stitch": "Cross-episode trailer", "heuristic": "Episode ending"}



def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def icon(angle) -> str:
    return ANGLE_ICON.get(str(angle), "🎞️")


def nice(angle) -> str:
    return str(angle or "other").replace("_", " ")


def css(theme: str = "dark") -> str:
    """Theme-agnostic: text inherits Streamlit's theme colour; surfaces are translucent, so the
    components look right in dark and light mode alike."""
    return """<style>
:root { --ch-surface: rgba(128,128,128,.075); --ch-surface2: rgba(128,128,128,.13); --ch-border: rgba(128,128,128,.26);
        --ch-accent: #ff6b4a; --ch-accent-text: #ff8064; --ch-gold: #f5b83d; --ch-warn: #f59e0b;
        --ch-serif: Georgia, 'Times New Roman', serif; }
.ch-muted, .ch-sub, .ch-kpi .l, .ch-poster .m, .ch-quote .k, .ch-bars .lbl, .ch-note, .ch-feat .d, .ch-score small { opacity: .68; }
.ch-hero { padding: 26px 30px 24px; border-radius: 18px; border: 1px solid var(--ch-border); color: inherit;
           background: radial-gradient(900px 260px at 0% 0%, rgba(255,107,74,.16), transparent 65%), var(--ch-surface); }
.ch-eyebrow { color: var(--ch-accent); font-size: .74rem; letter-spacing: .16em; text-transform: uppercase; font-weight: 700; }
.ch-title { font-family: var(--ch-serif); font-size: 2.35rem; line-height: 1.1; margin: 6px 0 8px; }
.ch-sub { font-size: .98rem; max-width: 780px; line-height: 1.5; }
.ch-kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin: 14px 0 4px; }
.ch-kpi { background: var(--ch-surface); border: 1px solid var(--ch-border); border-radius: 14px; padding: 12px 14px; }
.ch-kpi .n { font-size: 1.55rem; font-weight: 750; line-height: 1.1; font-variant-numeric: tabular-nums; }
.ch-kpi .l { font-size: .76rem; margin-top: 3px; line-height: 1.3; }
.ch-slate { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin: 6px 0 18px; }
.ch-poster { background: linear-gradient(180deg, var(--ch-surface2), var(--ch-surface)); border: 1px solid var(--ch-border);
             border-top: 3px solid var(--ch-accent); border-radius: 14px; padding: 12px 14px 14px; }
.ch-poster .rk { font-family: var(--ch-serif); font-size: 1.9rem; color: var(--ch-accent); line-height: 1; }
.ch-poster .t { font-weight: 700; margin: 8px 0 6px; line-height: 1.25; font-size: .95rem; }
.ch-poster .m { font-size: .78rem; line-height: 1.5; }
.ch-chip { display: inline-block; padding: 2px 10px; border-radius: 999px; background: var(--ch-surface2);
           border: 1px solid var(--ch-border); font-size: .76rem; margin: 0 6px 6px 0; }
.ch-chip.accent { border-color: var(--ch-accent); color: var(--ch-accent-text); }
.ch-seg { font-family: ui-monospace, Menlo, monospace; font-size: .8rem; background: var(--ch-surface2);
          border: 1px solid var(--ch-border); border-radius: 8px; padding: 3px 9px; display: inline-block; margin: 0 6px 6px 0; }
.ch-card-title { font-family: var(--ch-serif); font-size: 1.45rem; line-height: 1.2; margin: 2px 0 8px; }
.ch-card-title .rk { color: var(--ch-accent); margin-right: 8px; }
.ch-quote { border-left: 3px solid var(--ch-gold); padding: 7px 12px; margin: 8px 0; background: var(--ch-surface);
            border-radius: 0 10px 10px 0; }
.ch-quote.cut { border-left-color: var(--ch-accent); }
.ch-quote.plain { border-left-color: var(--ch-border); }
.ch-quote .k { font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; font-weight: 700; }
.ch-quote .v { margin-top: 2px; font-size: .92rem; line-height: 1.45; }
.ch-scorebox { display: grid; grid-template-columns: 92px 1fr; gap: 14px; align-items: center; margin-top: 10px;
               background: var(--ch-surface); border: 1px solid var(--ch-border); border-radius: 12px; padding: 10px 14px; }
.ch-score { font-family: var(--ch-serif); font-size: 2.2rem; line-height: 1; text-align: center; }
.ch-score small { display: block; font-family: inherit; font-size: .68rem; letter-spacing: .1em; text-transform: uppercase; margin-top: 4px; }
.ch-bars { display: grid; grid-template-columns: 104px 1fr 30px; gap: 5px 10px; align-items: center; font-size: .78rem; }
.ch-bars .trk { height: 6px; background: var(--ch-border); border-radius: 4px; overflow: hidden; }
.ch-bars .fil { height: 100%; background: var(--ch-accent); border-radius: 4px; }
.ch-bars .val { text-align: right; font-variant-numeric: tabular-nums; }
.ch-flag { color: var(--ch-warn); font-size: .8rem; margin-top: 6px; }
.ch-note { font-size: .8rem; margin-top: 6px; }
.ch-feats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 16px; }
.ch-feat { background: var(--ch-surface); border: 1px solid var(--ch-border); border-radius: 14px; padding: 14px 16px; }
.ch-feat .i { font-size: 1.4rem; }
.ch-feat .h { font-weight: 700; margin: 6px 0 3px; }
.ch-feat .d { font-size: .85rem; line-height: 1.45; }
.ch-steps { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin-top: 18px; }
.ch-step { padding: 6px 12px; border-radius: 10px; border: 1px solid var(--ch-border); background: var(--ch-surface2); font-size: .8rem; }
.ch-step b { color: var(--ch-accent); margin-right: 5px; }
.ch-arrow { opacity: .55; }
@media (max-width: 900px) { .ch-kpis, .ch-slate { grid-template-columns: repeat(2, minmax(0, 1fr)); }
                            .ch-feats { grid-template-columns: 1fr; } }
</style>"""


def hero(eyebrow: str, title: str, sub: str = "", extra: str = "") -> str:
    return (f'<div class="ch-hero"><div class="ch-eyebrow">{esc(eyebrow)}</div><div class="ch-title">{esc(title)}</div>'
            f'<div class="ch-sub">{esc(sub)}</div>{extra}</div>')


def kpis(items: list[tuple[str, str]]) -> str:
    return '<div class="ch-kpis">' + "".join(
        f'<div class="ch-kpi"><div class="n">{esc(n)}</div><div class="l">{esc(label)}</div></div>' for n, label in items) + "</div>"


def slate(records: list[dict]) -> str:
    cards = []
    for r in records:
        cards.append(
            f'<div class="ch-poster"><div class="rk">#{r["rank"]}</div><div class="t">{esc(r["title"])}</div>'
            f'<div class="m">{icon(r["angle"])} {esc(nice(r["angle"]))}<br>{r["duration"]:.0f} s · score '
            f'{r["scores"]["final"]:.1f}</div></div>')
    return '<div class="ch-slate">' + "".join(cards) + "</div>"


def card_head(r: dict) -> str:
    segs = "".join(f'<span class="ch-seg">E{s["episode"]:02d} · {esc(s["start"])} → {esc(s["end"])}</span>' for s in r["segments"])
    chips = [f'<span class="ch-chip accent">{icon(r["angle"])} {esc(nice(r["angle"]))}</span>',
             f'<span class="ch-chip">⏱ {r["duration"]:.1f} s</span>',
             f'<span class="ch-chip">{esc(SOURCE_NAME.get(r["source"], r["source"]))}</span>']
    if r.get("post_first"):
        chips.insert(0, '<span class="ch-chip accent">⭐ post first</span>')
    return (f'<div class="ch-card-title"><span class="rk">#{r["rank"]}</span>{esc(r["title"])}</div>'
            f'<div>{"".join(chips)}</div><div>{segs}</div>')


def quote(label: str, text: str, kind: str = "") -> str:
    return f'<div class="ch-quote {kind}"><div class="k">{esc(label)}</div><div class="v">{esc(text)}</div></div>'


def scores(r: dict) -> str:
    s = r["scores"]
    rows = [("3-second hook", s["hook"]), ("Cliffhanger", s["cliffhanger"]), ("Clarity", s["clarity"]),
            ("Comprehension", s["comprehension"]), ("Length fit", s["length"]), ("Early episode", s["early_episode"])]
    bars = "".join(f'<span class="lbl">{esc(n)}</span><span class="trk"><span class="fil" style="display:block;width:'
                   f'{max(0.0, min(10.0, float(v))) * 10:.0f}%"></span></span><span class="val">{float(v):.1f}</span>'
                   for n, v in rows)
    rounds = r.get("judge_rounds", 1)
    return (f'<div class="ch-scorebox"><div class="ch-score">{s["final"]:.1f}<small>score / 10</small></div>'
            f'<div class="ch-bars">{bars}</div></div>'
            f'<div class="ch-note">Judge scores averaged over {rounds} round{"s" if rounds != 1 else ""} '
            f'of simulated viewers.</div>')


def flags(r: dict) -> str:
    out = "".join(f'<div class="ch-flag">⚠ {esc(f)}</div>' for f in r.get("flags", []))
    if r.get("repairs"):
        out += f'<div class="ch-note">✂ Cut repairs: {esc("; ".join(r["repairs"]))}</div>'
    return out


def landing() -> str:
    feats = [("🪝", "Hook in 3 seconds", "Every promo opens on a slap, a reveal or a line that stops the scroll, checked in code."),
             ("✂️", "Cut on the cliffhanger", "Promos end at the peak of tension, before the answer, on a clean shot change."),
             ("🧭", "Makes sense on its own", "A cold-viewer judge with no context must understand who's who and what's at stake.")]
    steps = [("Parse", "code"), ("Mine", "LLM"), ("Check & snap", "code"), ("Stitch", "LLM"),
             ("Judge ×3", "LLM"), ("Rank", "code"), ("Review", "you")]
    feat_html = "".join(f'<div class="ch-feat"><div class="i">{i}</div><div class="h">{esc(h)}</div><div class="d">{esc(d)}</div></div>'
                        for i, h, d in feats)
    step_html = '<span class="ch-arrow">→</span>'.join(f'<span class="ch-step"><b>{esc(a)}</b>{esc(b)}</span>' for a, b in steps)
    return (hero("ClipHanger · promo finder for microdramas", "Find the promos hiding in your episodes",
                 "Upload a show's screenplays. ClipHanger returns ranked 20–60 s promos with exact segment timestamps, "
                 "the hook, the cliffhanger and why each works, and plays every promo as a timed read-along "
                 "before anyone opens an editing timeline.",
                 f'<div class="ch-steps">{step_html}</div>')
            + f'<div class="ch-feats">{feat_html}</div>')

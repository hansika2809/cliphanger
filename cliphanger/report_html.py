"""Self-contained report.html: every ranked promo with its Script Player. Opens in any browser,
no install and no API key, so reviewers can experience the promos without running anything."""
from __future__ import annotations

import html

from .player import player_html

CSS = """
:root { --bg:#fbfbfa; --card:#ffffff; --ink:#1b1b1d; --muted:#5d5f66; --line:#e4e4e1; --accent:#c2410c; --chip:#f1f0ec; }
@media (prefers-color-scheme: dark) { :root { --bg:#151517; --card:#1e1e21; --ink:#ececee; --muted:#a3a5ad; --line:#2f2f34; --accent:#fb923c; --chip:#2a2a2e; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width: 1060px; margin: 0 auto; padding: 28px 16px 60px; }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 19px; margin: 34px 0 12px; }
.sub { color: var(--muted); font-size: 13.5px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px; margin:14px 0;
        display:grid; grid-template-columns: 262px 1fr; gap:22px; }
.card iframe { width:262px; height:492px; border:0; }
.card h3 { margin:0 0 4px; font-size:17px; }
.meta { color:var(--muted); font-size:13px; margin-bottom:10px; }
.row { margin:6px 0; } .row b { display:inline-block; min-width:118px; }
.chip { display:inline-block; background:var(--chip); border-radius:999px; padding:1px 9px; font-size:12px; margin-right:6px; }
.star { color:var(--accent); font-weight:700; }
.flag { color:var(--accent); font-size:13px; }
code { font-size:13px; }
@media (max-width: 720px) { .card { grid-template-columns: 1fr; } .card iframe { width:100%; } }
"""


def _esc(x) -> str:
    return html.escape(str(x or ""))


def _card(r: dict) -> str:
    s = r["scores"]
    segs = "<br>".join(f"<code>E{x['episode']:02d}  {x['start']} → {x['end']}</code> ({x['seconds']:.1f} s)" for x in r["segments"])
    flags = "".join(f'<div class="flag">⚠ {_esc(f)}</div>' for f in r["flags"])
    repairs = f'<div class="row sub">✂ Cut repairs: {_esc("; ".join(r["repairs"]))}</div>' if r["repairs"] else ""
    star = ' <span class="star">★ post first</span>' if r["post_first"] else ""
    return f"""
<section class="card">
  <iframe sandbox="allow-scripts" srcdoc="{_esc(player_html(r))}" title="Script player for promo {r['rank']}"></iframe>
  <div>
    <h3>#{r['rank']} · {_esc(r['title'])}{star}</h3>
    <div class="meta"><span class="chip">{_esc(r['angle'])}</span><span class="chip">{r['duration']:.1f} s</span>
      <span class="chip">score {s['final']:.1f}/10</span><span class="chip">{_esc(r['source'])}</span></div>
    <div class="row"><b>Segments</b><br>{segs}</div>
    <div class="row"><b>Hook ({r['hook']['at_seconds']:.1f} s in)</b> {_esc(r['hook']['script_line'])}</div>
    <div class="row sub">3-second test: {_esc(r['hook'].get('scroll_test'))}</div>
    <div class="row"><b>Cliffhanger</b> {_esc(r['cliffhanger']['script_line'])}</div>
    <div class="row"><b>Leaves them asking</b> {_esc(r['cliffhanger']['open_question'])}</div>
    <div class="row"><b>Why it works</b> {_esc(r['why_it_works'])}</div>
    <div class="row sub">Cold viewer understood: “{_esc(r.get('cold_viewer_understood'))}”</div>
    <div class="row sub">hook {s['hook']:.0f} · cliffhanger {s['cliffhanger']:.0f} · clarity {s['clarity']:.0f} ·
      comprehension {s['comprehension']:.0f} · length {s['length']:.0f} · early-episode {s['early_episode']:.0f}
      · averaged over {r.get('judge_rounds', 1)} judging rounds</div>
    {flags}{repairs}
  </div>
</section>"""


def render_html(records: list[dict], meta: dict) -> str:
    ranked = [r for r in records if r.get("rank")]
    c = meta["counts"]
    first = "".join(_card(r) for r in ranked if r["post_first"])
    rest = "".join(_card(r) for r in ranked if not r["post_first"])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>ClipHanger report</title>
<style>{CSS}</style></head><body><main>
<h1>ClipHanger · {_esc(meta['show'])}</h1>
<div class="sub">Run {meta['finished']} · generator {_esc(meta['generator'])} · judge {_esc(meta['judge'])} ·
{meta['episodes']} episodes · {c['ideas']} ideas → {c['ideas'] - c['rejected']} valid ({c['repaired']} repaired by the cut
snapper) → {len(ranked)} distinct promos · {meta['live_requests']} model requests · {meta['seconds']:.0f} s</div>
<div class="sub">Press ▶ on any phone to play the promo as a timed read-along. The yellow mark is the 3-second hook
deadline; blue marks are cuts between segments.</div>
<h2>★ Post first: the best promo from each story angle</h2>{first}
<h2>All other promos, ranked</h2>{rest}
</main></body></html>"""

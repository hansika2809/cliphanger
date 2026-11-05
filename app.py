"""ClipHanger · upload microdrama screenplays → ranked promos you can play, review and export.

Run locally:  .venv/bin/streamlit run app.py        Open a saved run directly:  ?run=<folder in outputs/>
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from cliphanger import pipeline, ui
from cliphanger.config import load_config
from cliphanger.heat import heat_curve
from cliphanger.llm import PROVIDERS, LLMError, split_spec
from cliphanger.parse import load_show
from cliphanger.player import player_html

ROOT = Path(__file__).parent
st.set_page_config(page_title="ClipHanger · promo finder", page_icon="🎬", layout="wide")

try:  # Streamlit Cloud secrets → environment (keys never leave the server)
    for k, v in st.secrets.items():
        if isinstance(v, str) and not os.environ.get(k):
            os.environ[k] = v
except Exception:
    pass

cfg = load_config()
ss = st.session_state
THEME = st.get_option("theme.base") or getattr(getattr(st.context, "theme", None), "type", None) or "dark"
st.html(ui.css(THEME))

DECISIONS = ["👍 Approve", "🤔 Maybe", "👎 Reject"]
SOURCES = {"LLM picks": "mine", "Trailers": "stitch", "Episode endings": "heuristic"}
decisions: dict[str, str | None] = ss.setdefault("decisions", {})


def episode_list(spec: str) -> list[int] | None:
    if spec.strip().lower() == "all":
        return None
    nums: list[int] = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        nums += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return nums


@st.cache_data(show_spinner=False)
def cached_show(path: str):
    return load_show(path)


def saved_runs() -> list[Path]:
    return sorted((ROOT / "outputs").glob("*/promos.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def open_run(run_dir: Path) -> None:
    packs = sorted(ROOT.parent.glob("*.zip"))
    ss.update(records=json.loads((run_dir / "promos.json").read_text()), run_dir=str(run_dir),
              picked=run_dir.name, show_path=str(packs[0]) if packs else None)


# ── deep link: ?run=final_run ──────────────────────────────────────────────────
wanted = st.query_params.get("run")
if wanted and ss.get("picked") != wanted and (ROOT / "outputs" / wanted / "promos.json").exists():
    open_run(ROOT / "outputs" / wanted)

# ── sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🎬 ClipHanger")
    st.caption("Promo finder for microdrama screenplays")

    st.markdown("##### Open results")
    runs = saved_runs()
    pick = "—"
    if runs:
        names = ["—"] + [p.parent.name for p in runs]
        current = ss.get("picked")
        pick = st.selectbox("Saved runs on this machine", names, index=names.index(current) if current in names else 0)
    prev = st.file_uploader("…or a promos.json from any run", type=["json"])

    st.divider()
    st.markdown("##### Find promos in a new show")
    upload = st.file_uploader("Episode screenplays (.zip)", type=["zip"])
    local_packs = sorted(ROOT.parent.glob("*.zip"))
    local_choice = None
    if not upload and local_packs:
        local_choice = st.selectbox("…or a pack on this machine", ["—"] + [p.name for p in local_packs])
    eps = st.text_input("Episodes", "all", help='"all", "1-10" or "1,13,42"')
    trailers = st.slider("Cross-episode trailers", 0, 8, 5)
    with st.expander("Model settings"):
        gen = st.text_input("Generator (finds promos)", cfg["model"]["generator"],
                            help="provider/model: gemini, groq, openrouter, openai, ollama, anthropic or custom")
        jud = st.text_input("Judge (simulated viewers)", cfg["model"]["judge"],
                            help="A different model from the generator gives a more independent check")
        key = st.text_input("API key (this session only)", type="password")
        base_url = st.text_input("Custom OpenAI-compatible base URL", "", help="For provider 'custom'")
    go = st.button("🎬  Find promos", type="primary", width="stretch")

    if decisions:
        counts = {d: sum(v == d for v in decisions.values()) for d in DECISIONS}
        st.divider()
        st.markdown("##### Your decisions")
        st.caption(" · ".join(f"{d} {n}" for d, n in counts.items()))

# ── actions ────────────────────────────────────────────────────────────────────
if go:
    if upload:
        src = Path(tempfile.mkdtemp()) / upload.name
        src.write_bytes(upload.getvalue())
    elif local_choice and local_choice != "—":
        src = ROOT.parent / local_choice
    else:
        st.sidebar.error("Upload a .zip of screenplays first.")
        st.stop()
    for spec in {gen, jud}:
        try:
            env = PROVIDERS[split_spec(spec.split(",")[0].strip())[0]].get("key_env")
        except LLMError as err:
            st.sidebar.error(str(err))
            st.stop()
        if key and env:
            os.environ[env] = key
    if base_url:
        os.environ["CLIPHANGER_BASE_URL"] = base_url
    out = ROOT / "outputs" / f"run_app_{time.strftime('%Y%m%d_%H%M%S')}"
    with st.status("Finding promos… a 50-episode show takes 10–20 minutes on free tiers", expanded=True) as status:
        try:
            # unchanged model boxes keep config.yaml's fallback chains; an edit replaces the chain
            pipeline.run(src, out, cfg,
                         generator=None if gen.strip() == cfg["model"]["generator"] else gen.strip(),
                         judge_model=None if jud.strip() == cfg["model"]["judge"] else jud.strip(),
                         episodes=episode_list(eps), stitch_n=trailers, progress=status.write)
        except LLMError as err:
            status.update(label="The model provider refused or failed", state="error")
            st.error(f"{err}\n\nCheck the API key and the provider's quota, or switch models in Model settings.")
            st.stop()
        status.update(label="Done", state="complete", expanded=False)
    open_run(out)
    ss["show_path"] = str(src)
elif prev is not None:
    ss.update(records=json.loads(prev.getvalue()), run_dir=None, show_path=None, picked=None)
elif pick != "—" and ss.get("picked") != pick:
    open_run(ROOT / "outputs" / pick)

records: list[dict] | None = ss.get("records")

# ── landing ────────────────────────────────────────────────────────────────────
if not records:
    st.html(ui.landing())
    st.space("small")
    if runs:
        if st.button(f"▶  Open the latest saved run ({runs[0].parent.name})", type="primary"):
            open_run(runs[0].parent)
            st.rerun()
    else:
        st.info("Upload a .zip of episode screenplays in the sidebar to begin.", icon="🎬")
    st.stop()

# ── results ────────────────────────────────────────────────────────────────────
meta = None
if ss.get("run_dir") and (Path(ss["run_dir"]) / "run.json").exists():
    meta = json.loads((Path(ss["run_dir"]) / "run.json").read_text())

ranked = [r for r in records if r.get("rank")]
slate = [r for r in ranked if r.get("post_first")]
approved = sum(v == DECISIONS[0] for v in decisions.values())

if meta:
    c = meta["counts"]
    sub = (f"{meta['episodes']} episodes · generator {meta['generator']} · judge {meta['judge']} · "
           f"run {meta['finished']}")
    tiles = [(str(len(ranked)), "distinct promos ranked"),
             (f"{c['ideas'] - c['rejected']}/{c['ideas']}", "candidates passed the code checks"),
             (str(c["repaired"]), "cuts repaired by the snapper"),
             (str(meta["live_requests"] + meta["cache_hits"]), "model calls (cached replies reused)"),
             (str(approved), "promos you approved")]
else:
    sub = "Loaded from a promos.json file"
    tiles = [(str(len(ranked)), "distinct promos ranked"), (str(len(slate)), "in the post-first slate"),
             (f"{sum(r['duration'] for r in ranked) / max(1, len(ranked)):.0f} s", "average promo length"),
             (f"{max((r['scores']['final'] for r in ranked), default=0):.1f}", "top score"),
             (str(approved), "promos you approved")]
st.html(ui.hero("Promo report", meta["show"] if meta else "Promo run", sub) + ui.kpis(tiles))


def _sync(rid: str, where: str) -> None:
    value = ss.get(f"dec-{where}-{rid}")
    decisions[rid] = value
    for other in ("first", "all"):
        ss[f"dec-{other}-{rid}"] = value


def promo_card(r: dict, where: str) -> None:
    left, right = st.columns([1, 1.55], gap="large")
    with left:
        # data: URL → the player runs in its own opaque origin, isolated from the app
        st.iframe("data:text/html;charset=utf-8;base64," + base64.b64encode(player_html(r).encode()).decode(), height=500)
    with right:
        st.html(ui.card_head(r)
                + ui.quote(f"Hook · {r['hook']['at_seconds']:.1f} s in", r["hook"]["script_line"])
                + ui.quote("Cliffhanger · the cut", r["cliffhanger"]["script_line"], "cut")
                + ui.quote("Leaves them asking", r["cliffhanger"].get("open_question") or "What happens next?", "plain")
                + ui.quote("Why it works", r.get("why_it_works") or "", "plain"))
        st.html(ui.scores(r) + ui.flags(r))
        if r.get("cold_viewer_understood"):
            st.caption(f"🧑‍💻 A cold viewer understood: “{r['cold_viewer_understood']}”")
        k = f"dec-{where}-{r['id']}"
        if k not in ss:
            ss[k] = decisions.get(r["id"])
        st.segmented_control("Editor decision", DECISIONS, key=k, on_change=_sync, args=(r["id"], where))


tab_first, tab_all, tab_heat, tab_table, tab_how = st.tabs(
    ["⭐ Post first", "🎞️ All promos", "🔥 Drama heat-map", "📋 Table & export", "ℹ️ How it works"])

with tab_first:
    st.markdown("#### The best promo from each story angle")
    st.caption("Post these together as an A/B test of which angle sells this show, then cut more promos like the winner.")
    st.html(ui.slate(slate))
    for r in slate:
        with st.container(border=True):
            promo_card(r, "first")

with tab_all:
    angles = sorted({r["angle"] for r in ranked})
    f1, f2, f3 = st.columns([2.4, 1.7, 1], vertical_alignment="bottom")
    with f1:
        chosen = st.pills("Story angle", angles, selection_mode="multi", key="f-angle",
                          format_func=lambda a: f"{ui.icon(a)} {ui.nice(a)}")
    with f2:
        src_pick = st.segmented_control("Source", ["All", *SOURCES], default="All", key="f-src")
    with f3:
        min_score = st.slider("Minimum score", 0.0, 10.0, 0.0, 0.5, key="f-min")
    shown = [r for r in ranked
             if (not chosen or r["angle"] in chosen)
             and (src_pick in (None, "All") or r["source"] == SOURCES[src_pick])
             and r["scores"]["final"] >= min_score]
    show_all = st.toggle(f"Show all {len(shown)} matching promos", value=False) if len(shown) > 20 else True
    st.caption(f"Showing {len(shown) if show_all else 20} of {len(shown)} matching promos ({len(ranked)} ranked in total)")
    for r in (shown if show_all else shown[:20]):
        label = f"#{r['rank']} · {r['title']} · {r['duration']:.0f} s · score {r['scores']['final']:.1f}"
        with st.expander(label + ("  ·  ⭐ post first" if r.get("post_first") else "")):
            promo_card(r, "all")

with tab_heat:
    if not ss.get("show_path"):
        st.info("The heat-map needs the screenplays: run from a .zip, or open a saved run on the machine that has the pack.")
    else:
        show = cached_show(ss["show_path"])
        ramp = (["#13213a", "#1c3a66", "#2f6bb3", "#78aee8", "#d6e7fb"] if THEME == "dark"
                else ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#104281"])
        outline = "#ff6b4a" if THEME == "dark" else "#d9480f"
        cells = pd.DataFrame([{"episode": ep.number, "t": i * 2.0, "t2": i * 2.0 + 2.0, "heat": h}
                              for ep in show.episodes for i, h in enumerate(heat_curve(ep, step=2.0))])
        boxes = pd.DataFrame([{"episode": s["episode"], "start": s["start_s"], "end": s["end_s"],
                               "promo": f"#{r['rank']} {r['title']}", "cut": f"{s['start']}–{s['end']}"}
                              for r in ranked[:25] for s in r["segments"]])
        y = alt.Y("episode:O", title="Episode", axis=alt.Axis(labelFontSize=9))
        heat = alt.Chart(cells).mark_rect().encode(
            x=alt.X("t:Q", title="Seconds into the episode"), x2="t2:Q", y=y,
            color=alt.Color("heat:Q", title="Drama heat", scale=alt.Scale(range=ramp)),
            tooltip=[alt.Tooltip("episode:O", title="Episode"), alt.Tooltip("t:Q", title="Second"),
                     alt.Tooltip("heat:Q", title="Heat", format=".1f")])
        promos = alt.Chart(boxes).mark_rect(fill=None, stroke=outline, strokeWidth=2, cornerRadius=2).encode(
            x="start:Q", x2="end:Q", y=y, tooltip=[alt.Tooltip("promo:N", title="Promo"), alt.Tooltip("cut:N", title="Cut")])
        st.markdown("#### Where the drama is, episode by episode")
        st.caption("Heat comes from the script alone (emotion tags, violent or shocking actions, sound cues), "
                   "with no AI. Outlines mark the top 25 promos’ segments: hover for details.")
        st.altair_chart((heat + promos).properties(height=max(300, 13 * len(show.episodes))), width="stretch")

with tab_table:
    table = pd.DataFrame([{
        "rank": r["rank"], "post first": bool(r.get("post_first")), "title": r["title"], "angle": ui.nice(r["angle"]),
        "source": ui.SOURCE_NAME.get(r["source"], r["source"]),
        "segments": " + ".join(f"E{s['episode']:02d} {s['start']}–{s['end']}" for s in r["segments"]),
        "seconds": r["duration"], "hook": r["hook"]["script_line"], "cliffhanger": r["cliffhanger"]["script_line"],
        "why it works": r.get("why_it_works"), "score": r["scores"]["final"], "hook score": r["scores"]["hook"],
        "cliffhanger score": r["scores"]["cliffhanger"], "editor decision": decisions.get(r["id"]) or "",
        "flags": " | ".join(r["flags"]),
    } for r in ranked])
    st.dataframe(table, hide_index=True, width="stretch", height=520, column_config={
        "score": st.column_config.ProgressColumn("score", min_value=0, max_value=10, format="%.1f"),
        "hook score": st.column_config.ProgressColumn("hook", min_value=0, max_value=10, format="%.1f"),
        "cliffhanger score": st.column_config.ProgressColumn("cliffhanger", min_value=0, max_value=10, format="%.1f"),
        "seconds": st.column_config.NumberColumn("seconds", format="%.1f s"),
        "post first": st.column_config.CheckboxColumn("⭐"),
    })
    c1, c2 = st.columns(2)
    c1.download_button("⬇️  promos.csv (with your decisions)", table.to_csv(index=False), "promos.csv", "text/csv",
                       width="stretch")
    c2.download_button("⬇️  promos.json (everything)", json.dumps(records, indent=1, ensure_ascii=False), "promos.json",
                       "application/json", width="stretch")

with tab_how:
    st.html(ui.landing())

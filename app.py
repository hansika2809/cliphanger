"""ClipHanger · upload microdrama screenplays → ranked promos you can play, review and export.

Run locally:  .venv/bin/streamlit run app.py
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

from cliphanger import pipeline
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


# ── sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎬 ClipHanger")
    st.caption("Finds promo clips in microdrama screenplays: a hook in 3 seconds, a cliffhanger at the cut.")

    st.subheader("1 · Screenplays")
    upload = st.file_uploader("Upload a .zip of episode scripts", type=["zip"])
    local_packs = sorted(ROOT.parent.glob("*.zip"))
    local_choice = None
    if not upload and local_packs:
        local_choice = st.selectbox("…or a pack on this machine", ["—"] + [p.name for p in local_packs])

    st.subheader("2 · Models")
    gen = st.text_input("Generator (finds promos)", cfg["model"]["generator"],
                        help="provider/model: gemini, groq, openrouter, openai, ollama, anthropic or custom")
    jud = st.text_input("Judge (simulated viewers)", cfg["model"]["judge"],
                        help="Use a different model from the generator for a more independent check")
    key = st.text_input("API key (optional, this session only)", type="password",
                        help="Overrides the server's key for the providers above")
    base_url = st.text_input("Custom OpenAI-compatible base URL", "",
                             help="For provider 'custom', e.g. your company's model gateway")

    st.subheader("3 · Scope")
    eps = st.text_input("Episodes", "all", help='"all", "1-10" or "1,13,42"')
    trailers = st.slider("Cross-episode trailers", 0, 8, 5)
    go = st.button("Find promos", type="primary", width="stretch")

    st.divider()
    st.subheader("…or open a finished run")
    runs = sorted((ROOT / "outputs").glob("*/promos.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    pick = st.selectbox("Saved runs on this machine", ["—"] + [p.parent.name for p in runs]) if runs else "—"
    prev = st.file_uploader("promos.json from a run", type=["json"])

# ── actions ────────────────────────────────────────────────────────────────────
if go:
    if upload:
        tmp = Path(tempfile.mkdtemp()) / upload.name
        tmp.write_bytes(upload.getvalue())
        src = tmp
    elif local_choice and local_choice != "—":
        src = ROOT.parent / local_choice
    else:
        st.sidebar.error("Upload a .zip of screenplays first.")
        st.stop()
    for spec in {gen, jud}:
        try:
            env = PROVIDERS[split_spec(spec)[0]].get("key_env")
        except LLMError as err:
            st.sidebar.error(str(err))
            st.stop()
        if key and env:
            os.environ[env] = key
    if base_url:
        os.environ["CLIPHANGER_BASE_URL"] = base_url
    out = ROOT / "outputs" / f"run_app_{time.strftime('%Y%m%d_%H%M%S')}"
    with st.status("Finding promos… (a full 50-episode show takes a few minutes on free tiers)", expanded=True) as status:
        try:
            records = pipeline.run(src, out, cfg, generator=gen, judge_model=jud, episodes=episode_list(eps),
                                   stitch_n=trailers, progress=status.write)
        except LLMError as err:
            status.update(label="The model provider refused or failed", state="error")
            st.error(f"{err}\n\nCheck the API key and the provider's quota, or switch models in the sidebar.")
            st.stop()
        status.update(label="Done", state="complete", expanded=False)
    ss.update(records=records, run_dir=str(out), show_path=str(src))
elif prev is not None:
    ss.update(records=json.loads(prev.getvalue()), run_dir=None, show_path=None)
elif pick != "—" and ss.get("picked") != pick:
    run_dir = ROOT / "outputs" / pick
    ss.update(records=json.loads((run_dir / "promos.json").read_text()), run_dir=str(run_dir), picked=pick,
              show_path=str(local_packs[0]) if local_packs else None)

records: list[dict] | None = ss.get("records")

if not records:
    st.title("Find the promos hiding in your episodes")
    st.markdown(
        "Upload a show's screenplays and ClipHanger returns **ranked 20–60 s promos**: exact segment "
        "timestamps, the hook, the cliffhanger and why each one works. Then **play each promo as a "
        "timed read-along** before anyone opens an editing timeline.\n\n"
        "1. **Parse** every scene, line and shot with its timestamp (code)\n"
        "2. **Mine** hooks, cliffhangers and promo ideas, citing moments by ID only (LLM)\n"
        "3. **Validate & snap** cuts onto shot changes; hook ≤ 3 s; end on the cliffhanger (code)\n"
        "4. **Stitch** cross-episode trailers (LLM)\n"
        "5. **Judge** with simulated viewers: a 3-second scroll test and a cold viewer who has never seen the show (LLM)\n"
        "6. **Rank** with a transparent score and build a *post first* slate, one promo per story angle (code)")
    st.stop()

meta = None
if ss.get("run_dir") and (Path(ss["run_dir"]) / "run.json").exists():
    meta = json.loads((Path(ss["run_dir"]) / "run.json").read_text())

ranked = [r for r in records if r.get("rank")]
st.title(meta["show"] if meta else "Promo run")
m = st.columns(5)
m[0].metric("Promos ranked", len(ranked))
m[1].metric("Post-first slate", sum(r["post_first"] for r in ranked))
if meta:
    c = meta["counts"]
    m[2].metric("Ideas → valid", f"{c['ideas'] - c['rejected']}/{c['ideas']}", help=f"{c['repaired']} repaired by the cut snapper")
    m[3].metric("Model requests", meta["live_requests"], help=f"{meta['cache_hits']} answered from cache")
    m[4].metric("Tokens in / out", f"{meta['tokens_in'] // 1000}K / {meta['tokens_out'] // 1000}K")

decisions: dict[str, str] = ss.setdefault("decisions", {})


def promo_card(r: dict, where: str) -> None:
    left, right = st.columns([1, 1.35], gap="large")
    with left:
        # data: URL → the player runs in its own opaque origin, isolated from the app
        st.iframe("data:text/html;base64," + base64.b64encode(player_html(r).encode()).decode(), height=500)
    with right:
        star = " ⭐" if r["post_first"] else ""
        st.markdown(f"#### #{r['rank']} · {r['title']}{star}")
        st.caption(f"{r['angle']} · {r['duration']:.1f} s · final score {r['scores']['final']:.1f}/10 · {r['source']}")
        seg_rows = [f"`E{s['episode']:02d}  {s['start']} → {s['end']}`  ({s['seconds']:.1f} s)" for s in r["segments"]]
        st.markdown("**Segments, in play order**  \n" + "  \n".join(seg_rows))
        st.markdown(f"**Hook** ({r['hook']['at_seconds']:.1f} s in): {r['hook']['script_line']}")
        if r["hook"].get("scroll_test"):
            st.caption(f"3-second test: {r['hook']['scroll_test']}")
        st.markdown(f"**Cliffhanger:** {r['cliffhanger']['script_line']}")
        st.markdown(f"**Leaves them asking:** {r['cliffhanger']['open_question']}")
        st.markdown(f"**Why it works:** {r['why_it_works']}")
        s = r["scores"]
        st.caption(f"hook {s['hook']:.0f} · cliffhanger {s['cliffhanger']:.0f} · clarity {s['clarity']:.0f} · "
                   f"comprehension {s['comprehension']:.0f} · length {s['length']:.0f} · early-episode {s['early_episode']:.0f}")
        if r.get("cold_viewer_understood"):
            st.caption(f"A cold viewer understood: “{r['cold_viewer_understood']}”")
        for f in r["flags"]:
            st.warning(f, icon="⚠️")
        if r["repairs"]:
            st.caption("✂ Cut repairs: " + "; ".join(r["repairs"]))
        choice = st.radio("Editor decision", ["undecided", "approve", "reject"], horizontal=True,
                          key=f"dec-{where}-{r['id']}", index=["undecided", "approve", "reject"].index(decisions.get(r["id"], "undecided")))
        decisions[r["id"]] = choice


tab_first, tab_all, tab_heat, tab_table = st.tabs(["⭐ Post first", "All promos", "Drama heat-map", "Table & export"])

with tab_first:
    st.markdown("The best promo from each **story angle**. Post these first as an A/B test of which angle "
                "sells this show, then cut more promos like the winner.")
    for r in [r for r in ranked if r["post_first"]]:
        with st.container(border=True):
            promo_card(r, "first")

with tab_all:
    n = st.slider("Show top", 5, max(5, len(ranked)), min(15, len(ranked)))
    for r in ranked[:n]:
        with st.expander(f"#{r['rank']} · {r['title']} · {r['duration']:.0f} s · score {r['scores']['final']:.1f}"
                         + (" · ⭐" if r["post_first"] else "")):
            promo_card(r, "all")

with tab_heat:
    if not ss.get("show_path"):
        st.info("The heat-map needs the screenplays: run from a .zip (or pick a saved run on the machine that has the pack).")
    else:
        show = cached_show(ss["show_path"])
        dark = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
        ramp = ["#104281", "#256abf", "#5598e7", "#9ec5f4", "#cde2fb"] if dark else \
               ["#cde2fb", "#9ec5f4", "#5598e7", "#256abf", "#104281"]
        outline = "#d95926" if dark else "#eb6834"
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
            x="start:Q", x2="end:Q", y=y,
            tooltip=[alt.Tooltip("promo:N", title="Promo"), alt.Tooltip("cut:N", title="Cut")])
        st.markdown("Heat is computed from the script alone (emotion tags, violent or shocking actions, sound "
                    "cues), with no AI. **Orange outlines** are the top 25 promos' segments.")
        st.altair_chart((heat + promos).properties(height=max(300, 13 * len(show.episodes))), width="stretch")

with tab_table:
    table = pd.DataFrame([{
        "rank": r["rank"], "post first": r["post_first"], "title": r["title"], "angle": r["angle"],
        "segments": " + ".join(f"E{s['episode']:02d} {s['start']}–{s['end']}" for s in r["segments"]),
        "seconds": r["duration"], "hook": r["hook"]["script_line"], "cliffhanger": r["cliffhanger"]["script_line"],
        "why": r["why_it_works"], "score": r["scores"]["final"], "hook score": r["scores"]["hook"],
        "cliffhanger score": r["scores"]["cliffhanger"], "clarity": r["scores"]["clarity"],
        "editor decision": decisions.get(r["id"], "undecided"), "flags": " | ".join(r["flags"]),
    } for r in ranked])
    st.dataframe(table, width="stretch", hide_index=True)
    c1, c2 = st.columns(2)
    c1.download_button("Download promos.csv (with editor decisions)", table.to_csv(index=False), "promos.csv", "text/csv")
    c2.download_button("Download promos.json", json.dumps(records, indent=1, ensure_ascii=False), "promos.json", "application/json")

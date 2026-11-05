"""Write a run's outputs: promos.json/.csv (tools), report.md (people), raw replies + logs (audit)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .clip import clip_blocks
from .parse import Show, fmt_time
from .rank import Ranked
from .validate import Candidate, normalize_id


def _line(show: Show, block_id: str, episode: int) -> str:
    bid = normalize_id(block_id, episode)
    if not show.has_block(bid):
        return ""
    b = show.block(bid)
    return f'{b.speaker}: "{b.text}"' if b.kind == "dialogue" else f"[{b.kind}] {b.text}"


def promo_record(show: Show, r: Ranked) -> dict:
    c, v, i = r.candidate, r.verdict, r.candidate.idea
    return {
        "rank": r.rank, "post_first": r.post_first, "id": c.id, "source": c.source,
        "title": i.get("title"), "angle": i.get("angle"),
        "segments": [{"episode": x.episode, "start": fmt_time(x.start), "end": fmt_time(x.end),
                      "start_s": x.start, "end_s": x.end, "seconds": x.duration} for x in c.cuts],
        "duration": c.duration,
        "hook": {"at_seconds": c.hook_at, "script_line": _line(show, i["hook_block"], c.episode),
                 "description": i.get("hook"), "scroll_test": v.hook_reason},
        "cliffhanger": {"script_line": _line(show, i["cliffhanger_block"], c.episode),
                        "description": i.get("cliffhanger"), "open_question": i.get("open_question"),
                        "cold_viewer_left_wondering": v.left_wondering},
        "why_it_works": i.get("why"),
        "payoff_episode": i.get("payoff_episode"),
        "cold_viewer_understood": v.understood,
        "scores": r.scores, "judge_rounds": v.rounds, "judge_spread": v.spread, "flags": r.flags, "repairs": c.repairs, "duplicate_of": r.duplicate_of,
        "script": [{"t": t, "kind": b.kind, "speaker": b.speaker, "tags": b.tags, "text": b.text,
                    "episode": b.episode, "dur": b.duration} for t, b in clip_blocks(show, c.cuts)],
    }


def _segments_str(rec: dict) -> str:
    return " + ".join(f"E{s['episode']:02d} {s['start']}–{s['end']}" for s in rec["segments"])


def write_outputs(out: Path, show: Show, ideas: list, candidates: list[Candidate], ranked: list[Ranked],
                  llm_log: list[dict], meta: dict, raw_lookup) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    records = [promo_record(show, r) for r in ranked]

    (out / "promos.json").write_text(json.dumps(records, indent=1, ensure_ascii=False))
    (out / "ideas.json").write_text(json.dumps(ideas, indent=1, ensure_ascii=False))
    (out / "candidates_all.json").write_text(json.dumps([c.to_dict() for c in candidates], indent=1, ensure_ascii=False))
    (out / "llm_calls.json").write_text(json.dumps(llm_log, indent=1))
    (out / "run.json").write_text(json.dumps(meta, indent=1))

    raw = out / "raw_llm_replies"
    raw.mkdir(exist_ok=True)
    for call in llm_log:
        reply = raw_lookup(call["key"])
        if reply:
            name = call["tag"].replace(":", "_").replace(",", "_").replace("+", "_")
            (raw / f"{name}.json").write_text(json.dumps(reply, indent=1, ensure_ascii=False))

    with open(out / "promos.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "post_first", "id", "title", "angle", "segments", "duration_s", "hook_at_s",
                    "hook_line", "cliffhanger_line", "open_question", "why_it_works", "final", "hook",
                    "cliffhanger", "clarity", "comprehension", "length", "early_episode", "flags", "duplicate_of"])
        for rec in records:
            s = rec["scores"]
            w.writerow([rec["rank"], rec["post_first"], rec["id"], rec["title"], rec["angle"], _segments_str(rec),
                        rec["duration"], rec["hook"]["at_seconds"], rec["hook"]["script_line"],
                        rec["cliffhanger"]["script_line"], rec["cliffhanger"]["open_question"], rec["why_it_works"],
                        s["final"], s["hook"], s["cliffhanger"], s["clarity"], s["comprehension"], s["length"],
                        s["early_episode"], " | ".join(rec["flags"]), rec["duplicate_of"] or ""])

    (out / "report.md").write_text(render_report(records, candidates, meta))
    from .report_html import render_html
    (out / "report.html").write_text(render_html(records, meta))
    return records


def render_report(records: list[dict], candidates: list[Candidate], meta: dict) -> str:
    ranked = [r for r in records if r["rank"]]
    st = meta["counts"]
    md = [f"# ClipHanger · {meta['show']}", "",
          f"Run {meta['finished']} · generator `{meta['generator']}` · judge `{meta['judge']}` · "
          f"{meta['episodes']} episodes · {meta['seconds']:.0f} s · {meta['tokens_in']:,} tokens in / "
          f"{meta['tokens_out']:,} out · {meta['live_requests']} live requests",
          "",
          f"Candidates: {st['ideas']} ideas → {st['ok']} valid as-is, {st['repaired']} repaired, "
          f"{st['rejected']} rejected → {len(ranked)} distinct promos ranked "
          f"({st['duplicates']} near-duplicates folded).", "",
          "## Post first: the best promo from each story angle", "",
          "Post these as an A/B test of which angle sells this show, then make more of the winner.", ""]
    for r in [r for r in ranked if r["post_first"]]:
        md.append(f"{r['rank']}. **{r['title']}** ({r['angle']}) · {_segments_str(r)} · {r['duration']:.1f} s · "
                  f"score {r['scores']['final']:.1f}  \n   Hook: {r['hook']['script_line']}  \n"
                  f"   Cliffhanger: {r['cliffhanger']['script_line']}  \n   Why: {r['why_it_works']}")
    md += ["", "## All promos, ranked", ""]
    for r in ranked:
        s = r["scores"]
        star = " · ⭐ post first" if r["post_first"] else ""
        md += [f"### #{r['rank']} · {r['title']}{star}",
               f"*{r['angle']} · {r['duration']:.1f} s · {r['source']} · id {r['id']} · "
               f"final {s['final']:.1f}/10*", "",
               f"- **Segments:** {_segments_str(r)}",
               f"- **Hook ({r['hook']['at_seconds']:.1f} s in):** {r['hook']['script_line']}"
               + (f" (3-second test: *{r['hook']['scroll_test']}*)" if r['hook']['scroll_test'] else ""),
               f"- **Cliffhanger:** {r['cliffhanger']['script_line']}",
               f"- **Open question:** {r['cliffhanger']['open_question']}"
               + (f" · payoff in E{r['payoff_episode']}" if r["payoff_episode"] else ""),
               f"- **Why it works:** {r['why_it_works']}",
               f"- **Cold viewer understood:** {r['cold_viewer_understood']}",
               f"- **Scores:** hook {s['hook']:.0f} · cliffhanger {s['cliffhanger']:.0f} · clarity {s['clarity']:.0f}"
               f" · comprehension {s['comprehension']:.0f} · length {s['length']:.0f} · early-episode {s['early_episode']:.0f}"]
        if r["flags"]:
            md.append(f"- **Flags:** {'; '.join(r['flags'])}")
        if r["repairs"]:
            md.append(f"- **Cut repairs:** {'; '.join(r['repairs'])}")
        md.append("")
    dups = [r for r in records if r["duplicate_of"]]
    if dups:
        md += ["## Near-duplicates (same footage as a higher-ranked promo)", ""]
        md += [f"- {r['id']} · {r['title']} → duplicate of {r['duplicate_of']}" for r in dups]
        md.append("")
    rejected = [c for c in candidates if c.status == "rejected"]
    if rejected:
        md += ["## Rejected ideas and why", "", "| id | title | reason |", "|---|---|---|"]
        md += [f"| {c.id} | {c.idea.get('title', '')} | {'; '.join(c.issues)} |" for c in rejected]
    return "\n".join(md) + "\n"

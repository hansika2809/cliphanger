"""Command line:  python -m cliphanger <command> --input <pack.zip | folder>

Commands
  parse   parse the screenplays; write show.json, readable transcripts and the exact LLM view
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .parse import fmt_time, load_show, render_for_llm, render_markdown


def cmd_parse(args: argparse.Namespace) -> None:
    show = load_show(args.input, title=args.title)
    out = Path(args.out)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    (out / "llm_view").mkdir(parents=True, exist_ok=True)

    (out / "show.json").write_text(json.dumps(show.to_dict(), indent=1, ensure_ascii=False))
    for ep in show.episodes:
        (out / "transcripts" / f"E{ep.number:02d}.md").write_text(render_markdown(ep))
        (out / "llm_view" / f"E{ep.number:02d}.txt").write_text(render_for_llm(ep))

    blocks = [b for ep in show.episodes for b in ep.blocks]
    kinds = Counter(b.kind for b in blocks)
    lengths = [ep.duration for ep in show.episodes]
    llm_chars = sum(len(render_for_llm(ep)) for ep in show.episodes)
    speakers = Counter(b.speaker for b in blocks if b.kind == "dialogue")

    print(f"Show        : {show.title}")
    print(f"Episodes    : {len(show.episodes)}  (E{show.episodes[0].number}–E{show.episodes[-1].number})")
    print(f"Scenes      : {sum(len(ep.scenes) for ep in show.episodes)}")
    print(f"Blocks      : {len(blocks)}  " + "  ".join(f"{k}={v}" for k, v in kinds.most_common()))
    print(f"Runtime     : {fmt_time(sum(lengths))} total  |  episodes {fmt_time(min(lengths))} – {fmt_time(max(lengths))}")
    print(f"LLM view    : ~{llm_chars // 4:,} tokens for the whole show (chars/4)")
    print(f"Top speakers: " + ", ".join(f"{s} {n}" for s, n in speakers.most_common(8)))
    print(f"Warnings    : {len(show.warnings)}")
    for w in show.warnings[:20]:
        print(f"   ! {w}")
    print(f"\nWrote {out}/show.json, {out}/transcripts/*.md, {out}/llm_view/*.txt")


def cmd_models(args: argparse.Namespace) -> None:
    from .llm import list_models, probe

    ids = list_models(args.provider)
    shown = [i for i in ids if args.match in i] if args.match else ids
    print(f"{len(ids)} models visible to your {args.provider} key" + (f"; {len(shown)} match {args.match!r}" if args.match else ""))
    if not args.probe:
        print("\n".join(f"  {i}" for i in shown))
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda i: (i, *probe(f"{args.provider}/{i}")), shown))
    for model_id, ok, secs, err in results:
        print(f"  {'OK  ' if ok else 'FAIL'}  {args.provider}/{model_id:32s} {secs:5.1f}s  {err}")


def _episode_list(spec: str, show) -> list[int]:
    if spec == "all":
        return [ep.number for ep in show.episodes]
    nums: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        nums += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return nums


def _show_line(show, block_id: str, episode: int) -> str:
    from .parse import block_line
    from .validate import normalize_id
    bid = normalize_id(block_id, episode)
    return block_line(show.block(bid), global_id=True)[:110] if show.has_block(bid) else f"{block_id} (NOT IN SCRIPT)"


def _validate_all(show, ideas: list[dict], rules: dict) -> list:
    """ideas = [{"episode": n, "promos": [...]}, ...] -> validated Candidates."""
    from .validate import validate
    return [validate(promo, ep["episode"], show, f"E{ep['episode']:02d}-P{k}", rules)
            for ep in ideas for k, promo in enumerate(ep["promos"], 1)]


def _print_candidates(show, candidates, verbose: bool = True) -> None:
    from collections import Counter
    if verbose:
        for c in candidates:
            i = c.idea
            segs = " + ".join(f"E{x.episode:02d} {fmt_time(x.start)}–{fmt_time(x.end)}" for x in c.cuts) or "—"
            print(f"\n[{c.status.upper()}] {c.id} · {i['title']}  ({i.get('angle')}, self {i.get('self_score')}/10)")
            print(f"   cut        {segs}  = {c.duration:.1f}s   (LLM estimated {i.get('est_seconds')}s)")
            hook_at = f"{c.hook_at:.1f}s" if c.hook_at is not None else "?"
            print(f"   hook @{hook_at:<5} {_show_line(show, i['hook_block'], c.episode)}")
            print(f"   cliff      {_show_line(show, i['cliffhanger_block'], c.episode)}")
            print(f"   question   {i.get('open_question')}   (payoff → {('E' + str(i['payoff_episode'])) if i.get('payoff_episode') else '?'})")
            for r in c.repairs:
                print(f"   repaired   {r}")
            for issue in c.issues:
                print(f"   REJECTED   {issue}")
    status = Counter(c.status for c in candidates)
    print(f"\nPromo ideas: {len(candidates)} | ok as-is: {status['ok']} | repaired: {status['repaired']} "
          f"| rejected: {status['rejected']}")
    import re
    reasons = Counter(re.sub(r"\d+(\.\d+)?s", "#s", re.sub(r"E\d+-B\d+", "<id>", i)) for c in candidates for i in c.issues)
    repairs = Counter("flash-forward" if r.startswith("flash") else ("cut-in moved" if " start " in r else "cut-out moved")
                      for c in candidates for r in c.repairs)
    if repairs:
        print("Repairs: " + "; ".join(f"{r} ×{n}" for r, n in repairs.most_common()))
    if reasons:
        print("Rejection reasons: " + "; ".join(f"{r} ×{n}" for r, n in reasons.most_common()))


def cmd_mine(args: argparse.Namespace) -> None:
    from .config import load_config, make_llm
    from .mine import mine

    cfg = load_config()
    show = load_show(args.input, title=args.title)
    llm = make_llm(cfg, "generator", override=args.model)
    episodes = _episode_list(args.episodes, show)
    ideas = mine(show, llm, episodes, per_call=args.per_call or cfg["budget"]["episodes_per_call"])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ideas_json = [e.model_dump() for e in ideas]
    candidates = _validate_all(show, ideas_json, cfg["promo"])
    (out / "ideas.json").write_text(json.dumps(ideas_json, indent=1, ensure_ascii=False))
    (out / "candidates.json").write_text(json.dumps([c.to_dict() for c in candidates], indent=1, ensure_ascii=False))
    (out / "llm_calls.json").write_text(json.dumps(llm.log, indent=1))
    _print_candidates(show, candidates, verbose=len(episodes) <= 5)
    print("Beats per episode: " + ", ".join(f"E{e.episode:02d}={len(e.beats)}" for e in ideas))
    for model, u in llm.usage().items():
        print(f"Usage {model}: {u}")
    print(f"Wrote {out}/ideas.json, candidates.json, llm_calls.json")


def cmd_validate(args: argparse.Namespace) -> None:
    """Re-run the deterministic validator on saved ideas: no LLM calls, no cost."""
    from .config import load_config
    cfg = load_config()
    show = load_show(args.input, title=args.title)
    ideas = json.loads(Path(args.ideas).read_text())
    candidates = _validate_all(show, ideas, cfg["promo"])
    out = Path(args.ideas).with_name("candidates.json")
    out.write_text(json.dumps([c.to_dict() for c in candidates], indent=1, ensure_ascii=False))
    _print_candidates(show, candidates, verbose=args.verbose)
    print(f"Wrote {out}")


def cmd_run(args: argparse.Namespace) -> None:
    import time

    from .config import load_config
    from .pipeline import run

    cfg = load_config()
    episodes = None if args.episodes == "all" else _episode_list(args.episodes, None)
    out = args.out or f"outputs/run_{time.strftime('%Y%m%d_%H%M')}"
    records = run(args.input, out, cfg, generator=args.model, judge_model=args.judge_model,
                  episodes=episodes, title=args.title, stitch_n=args.stitch)
    print("\nTop promos:")
    for r in [r for r in records if r["rank"]][: args.top]:
        segs = " + ".join(f"E{s['episode']:02d} {s['start']}–{s['end']}" for s in r["segments"])
        star = "⭐" if r["post_first"] else "  "
        print(f" {star}#{r['rank']:<3} {r['scores']['final']:4.1f}  {r['title'][:34]:34s} {r['duration']:5.1f}s  {segs}")
    print(f"\nFull report: {out}/report.md")


def cmd_report(args: argparse.Namespace) -> None:
    """Rebuild report.html from a saved run (no LLM calls)."""
    from .report_html import render_html
    run = Path(args.run)
    records = json.loads((run / "promos.json").read_text())
    meta = json.loads((run / "run.json").read_text())
    (run / "report.html").write_text(render_html(records, meta))
    print(f"Wrote {run}/report.html")


def cmd_eval(args: argparse.Namespace) -> None:
    """Quality checks for a saved run: no-AI baseline (same judges), human grading sheet, run comparison."""
    from statistics import mean

    from .config import load_config, make_llm
    from .evaluate import baseline_candidates, export_grading_sheet, load_run, score_grades, top_overlap
    from .judge import judge
    from .rank import rank
    from .report import promo_record

    cfg = load_config()
    run_dir = Path(args.run)
    records = load_run(run_dir)
    result: dict = {}

    if args.grades:
        result["human_grades"] = score_grades(Path(args.grades))
    if args.compare:
        hits, ids = top_overlap(records, load_run(args.compare), k=10)
        result["top10_overlap_with"] = {"run": args.compare, "shared": hits, "ids": ids}
    if not args.no_baseline:
        show = load_show(args.input)
        jud = make_llm(cfg, "judge", args.judge_model)
        base = baseline_candidates(show, cfg["promo"])
        verdicts = judge(show, base, jud, batch_size=cfg["budget"]["promos_per_judge_call"],
                         rounds=cfg["budget"].get("judge_rounds", 3))
        base_records = [promo_record(show, r) for r in rank(show, base, verdicts, cfg["promo"], cfg["ranking"])]
        (run_dir / "baseline_promos.json").write_text(json.dumps(base_records, indent=1, ensure_ascii=False))

        def stats(recs: list[dict], k: int | None) -> dict:
            top = [r for r in recs if r["rank"]][:k] if k else [r for r in recs if r["rank"]]
            out = {m: round(mean(r["scores"][m] for r in top), 2) for m in ("final", "hook", "cliffhanger", "clarity", "comprehension")}
            out["resolved_share"] = round(sum(any("resolves itself" in f for f in r["flags"]) for r in top) / len(top), 2)
            out["n"] = len(top)
            return out

        hits, _ = top_overlap(records, base_records, k=10)
        result["baseline"] = {"pipeline_top10": stats(records, 10), "baseline_top10": stats(base_records, 10),
                              "pipeline_all": stats(records, None), "baseline_all": stats(base_records, None),
                              "pipeline_top10_also_in_baseline_top10": hits,
                              "baseline_rejected": sum(c.status == "rejected" for c in base)}
    sheet = run_dir / "grade_top15.csv"
    if not sheet.exists():
        export_grading_sheet(records, sheet)
    (run_dir / "eval.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print(f"\nGrading sheet for a human: {sheet}  (fill the grade column, then: eval --grades {sheet})")


def main() -> None:
    p = argparse.ArgumentParser(prog="cliphanger", description="Find promo clips in microdrama screenplays.")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("parse", help="parse screenplays and write transcripts")
    sp.add_argument("--input", required=True, help="zip, folder or .txt of episode screenplays")
    sp.add_argument("--title", default=None, help="show title (default: from the zip/folder name)")
    sp.add_argument("--out", default="outputs/parsed")
    sp.set_defaults(func=cmd_parse)

    sm = sub.add_parser("models", help="list (and optionally probe) the models your key can use")
    sm.add_argument("--provider", default="gemini")
    sm.add_argument("--match", default="", help="only models whose id contains this text, e.g. flash")
    sm.add_argument("--probe", action="store_true", help="send one tiny request to each listed model")
    sm.set_defaults(func=cmd_models)

    sn = sub.add_parser("mine", help="LLM stage: propose promo ideas for some episodes")
    sn.add_argument("--input", required=True)
    sn.add_argument("--title", default=None)
    sn.add_argument("--episodes", default="all", help='"all", "1,13,42" or "1-10"')
    sn.add_argument("--model", default=None, help="override the generator, e.g. gemini/gemini-3.8-flash")
    sn.add_argument("--per-call", type=int, default=None, help="episodes per LLM request")
    sn.add_argument("--out", default="outputs/mine")
    sn.set_defaults(func=cmd_mine)

    sv = sub.add_parser("validate", help="re-validate saved ideas.json (code only, no LLM)")
    sv.add_argument("--input", required=True)
    sv.add_argument("--title", default=None)
    sv.add_argument("--ideas", required=True, help="path to an ideas.json written by `mine`")
    sv.add_argument("--verbose", action="store_true")
    sv.set_defaults(func=cmd_validate)

    sr = sub.add_parser("run", help="the whole pipeline: promos.json/.csv + report.md")
    sr.add_argument("--input", required=True, help="zip, folder or .txt of episode screenplays")
    sr.add_argument("--title", default=None)
    sr.add_argument("--episodes", default="all")
    sr.add_argument("--model", default=None, help="generator, e.g. gemini/gemini-3.6-flash or anthropic/claude-sonnet-5-5")
    sr.add_argument("--judge-model", default=None, help="judge (use a different model for an independent check)")
    sr.add_argument("--stitch", type=int, default=5, help="cross-episode trailers to build (0 = off)")
    sr.add_argument("--top", type=int, default=15)
    sr.add_argument("--out", default=None)
    sr.set_defaults(func=cmd_run)

    sh = sub.add_parser("report", help="rebuild report.html for a saved run")
    sh.add_argument("--run", required=True, help="a run folder containing promos.json and run.json")
    sh.set_defaults(func=cmd_report)

    se = sub.add_parser("eval", help="baseline comparison, human grading sheet, run-vs-run overlap")
    se.add_argument("--input", required=True)
    se.add_argument("--run", required=True, help="run folder to evaluate")
    se.add_argument("--judge-model", default=None)
    se.add_argument("--compare", default=None, help="another run folder (e.g. a different generator model)")
    se.add_argument("--grades", default=None, help="a filled-in grade_top15.csv")
    se.add_argument("--no-baseline", action="store_true")
    se.set_defaults(func=cmd_eval)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

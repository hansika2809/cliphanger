# ClipHanger 🎬

**Finds promo clips in microdrama screenplays.** Give it a show's episode scripts; it returns ranked
20–60 s promos. Each promo opens on a hook within 3 seconds, ends on a cliffhanger, and makes sense to
someone who has never seen the show. For each one you get the exact segment timestamps (in play
order), the hook, the cliffhanger, why it works, and a **Script Player** that plays it as a timed
read-along before anyone opens an editing timeline.

## How it works

```
screenplays.zip
  ├─[1] PARSE     code   every block (shot, action, sound, line) → ID like E01-B034 + start/end
  ├─[2] MINE      LLM    per batch of episodes: hooks, cliffhangers, 2–3 promo ideas each,
  │                      citing moments by block ID only (the LLM never writes a timestamp)
  ├─[3] VALIDATE  code   IDs → times · cuts snapped onto shot changes, never mid-line ·
  │                      hook ≤ 3 s (else trim, or open with a flash-forward) · end on the
  │                      cliffhanger · 20–60 s · every change recorded
  ├─[4] STITCH    LLM    cross-episode trailers from the strongest beats; rejected ones go
  │                      back to the model once with the exact reasons
  ├─[5] JUDGE     LLM    a different model plays three viewers: a 3-second scroll test (sees
  │                      only the first 3 s), a cold viewer (no title, no context), and a
  │                      comprehension check against the true logline
  └─[6] RANK      code   weighted score (weights in config.yaml) · near-duplicates folded ·
                         "post first" slate = best promo per story angle
```

| Code (deterministic) | LLM (judgement) | Human editor |
|---|---|---|
| parsing, IDs, all timestamp maths, cut snapping, length/hook/cliffhanger rules, ranking, outputs, caching | spotting hooks and cliffhangers, judging "makes sense on its own", one-line "why it works" | plays promos in the Script Player, approves/rejects in the app, exports the decisions |

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # paste a free key from https://aistudio.google.com/apikey

# whole pipeline → outputs/run_<time>/ (report.html, report.md, promos.csv, promos.json, raw replies)
.venv/bin/python -m cliphanger run --input "path/to/show-scripts.zip"

# the review app (upload a zip, play promos, approve/reject, export)
.venv/bin/streamlit run app.py
```

A first run on 50 episodes needs ~114 requests: 12 to the generator model (10 mining batches, stitch
+ one revision) and ~100 small judge requests (3 rounds). That's ~676K tokens in / 122K out, and
~20 minutes on Gemini's free tier, where a client-side limiter keeps it under the per-minute caps.
Every reply is cached, so re-runs are instant and free, and an interrupted run resumes where it
stopped.

## Commands

| Command | What it does |
|---|---|
| `run --input X [--model M] [--judge-model J] [--episodes 1-10] [--stitch 5]` | the full pipeline |
| `parse --input X` | parse only; writes readable transcripts and the exact text the LLM sees |
| `models --provider gemini --match flash --probe` | which models your key can actually use right now |
| `mine --input X --episodes 1,13,42` | mining + validation only, printed for inspection |
| `validate --input X --ideas run/ideas.json` | re-run the validator on saved ideas (no API cost) |
| `eval --input X --run outputs/run_…` | no-AI baseline judged by the same judges, a human grading sheet, run-vs-run overlap |
| `report --run outputs/run_…` | rebuild report.html for a saved run |

## Swap the model (one line)

Models are `provider/model` strings, set in `config.yaml` or per run with `--model` / `--judge-model`:

```bash
--model gemini/gemini-3.6-flash               # default (free tier)
--model anthropic/claude-sonnet-5-5           # needs ANTHROPIC_API_KEY
--model groq/llama-3.3-70b-versatile          # any OpenAI-compatible provider: groq, openrouter, openai, ollama
--model custom/<model>                        # your own gateway: set CLIPHANGER_BASE_URL + CLIPHANGER_API_KEY
```

Each role has a fallback chain in `config.yaml`. Overloaded or quota-exhausted models are skipped
automatically, and every output records which model produced it.

## Outputs (per run)

`report.html` (open in any browser: every promo with its Script Player) · `report.md` · `promos.csv`
(one row per promo, for a spreadsheet) · `promos.json` (everything, incl. sub-scores and the timed
script) · `candidates_all.json` (incl. rejected ideas and why) · `raw_llm_replies/` · `run.json`
(models, tokens, time, config).

## Known limits

- It reads the script, not the video: a scripted slap may not land on screen.
- Clarity is judged from text that includes character names, which a real viewer doesn't see.
- Cross-episode trailers are the weakest output: names and context don't carry across episodes.
- AI-written scripts drift on names (this pack spells one title four ways); promos that show two
  spellings are flagged, not fixed.

## Tests

```bash
.venv/bin/python -m pytest -q    # synthetic fixtures; the real-pack test runs only if the pack is present
```

## Confidentiality

Show scripts are confidential. This repo holds code and synthetic test fixtures only; input packs,
transcripts, outputs and caches are git-ignored.

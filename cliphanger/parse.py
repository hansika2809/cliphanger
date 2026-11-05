"""Parse microdrama screenplays into structured, timestamped blocks.

Every block (camera shot, action, sound cue, dialogue line) gets a stable ID such as
``E01-B034``. Later stages, including the LLM, refer to moments ONLY by these IDs, so
every timestamp in the output comes from the script itself and none is invented.

The parser never crashes on odd input: it records warnings and keeps going, because the
tool has to run on new shows it has never seen.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Block labels that are not characters. Anything else in **LABEL:** is a speaker.
KIND_BY_LABEL = {
    "CAMERA": "camera",
    "SHOT": "camera",
    "ACTION": "action",
    "SOUND FX": "sfx",
    "SFX": "sfx",
    "SOUND": "sfx",
    "MUSIC": "sfx",
}

RE_META = re.compile(r"^\s*(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES)\s*:\s*(.*)$", re.I)
RE_SCRIPT_START = re.compile(r"^\s*-{2,}\s*SCRIPT START\s*-{2,}\s*$", re.I)
RE_SCRIPT_END = re.compile(r"^\s*-{2,}\s*SCRIPT END\s*-{2,}\s*$", re.I)
RE_SCENE = re.compile(r"^\s*SCENE\s+(\d+)\s*$", re.I)
RE_SCENE_FIELD = re.compile(r"^\s*SCENE\s+(HEADING|START|END)\s*:\s*(.*)$", re.I)
RE_SECTION = re.compile(r"^\s*ACTIONS?\s+AND\s+DIALOGUES?\s*:\s*$", re.I)
RE_LABEL = re.compile(r"^\s*\*\*(.+?):\*\*\s*$")
RE_TIMES = re.compile(
    r"^\s*start\s*:\s*\(?\s*([\d:.]+)\s*\)?\s*\|\s*end\s*:\s*\(?\s*([\d:.]+)\s*\)?\s*$", re.I
)
RE_PAREN = re.compile(r"\(([^()]*)\)")
RE_EP_IN_NAME = re.compile(r"episode[\s_-]*0*(\d+)", re.I)


def parse_time(value: str) -> float:
    """'01:03.50' -> 63.5 ; '1:02:03.5' -> 3723.5 ; '45.2' -> 45.2"""
    seconds = 0.0
    for part in value.strip().split(":"):
        seconds = seconds * 60 + float(part)
    return round(seconds, 3)


def fmt_time(seconds: float) -> str:
    """63.5 -> '01:03.50' (same style as the scripts)."""
    minutes, secs = divmod(max(seconds, 0.0), 60)
    return f"{int(minutes):02d}:{secs:05.2f}"


@dataclass
class Block:
    id: str                    # "E01-B034": the only way later stages refer to a moment
    episode: int
    scene: int
    idx: int                   # 1-based position in the episode, in script order
    kind: str                  # camera | action | sfx | dialogue
    start: float               # seconds from episode start
    end: float
    text: str
    speaker: str | None = None
    tags: list[str] = field(default_factory=list)  # e.g. ["O.S.", "Furious"]

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


@dataclass
class Scene:
    number: int
    heading: str = ""
    start: float | None = None
    end: float | None = None
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    source: str
    scenes: list[Scene] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> list[Block]:
        return [b for s in self.scenes for b in s.blocks]

    @property
    def duration(self) -> float:
        ends = [s.end for s in self.scenes if s.end is not None]
        ends += [b.end for b in self.blocks]
        return max(ends, default=0.0)


@dataclass
class Show:
    title: str
    episodes: list[Episode]

    def __post_init__(self) -> None:
        self._by_id = {b.id: b for ep in self.episodes for b in ep.blocks}
        self._by_num = {ep.number: ep for ep in self.episodes}

    def block(self, block_id: str) -> Block:
        return self._by_id[block_id]

    def has_block(self, block_id: str) -> bool:
        return block_id in self._by_id

    def episode(self, number: int) -> Episode:
        return self._by_num[number]

    @property
    def warnings(self) -> list[str]:
        return [w for ep in self.episodes for w in ep.warnings]

    def to_dict(self) -> dict:
        return {"title": self.title, "episodes": [asdict(ep) for ep in self.episodes]}


def _parse_label(label: str) -> tuple[str, str | None, list[str]]:
    """'MATRIARCH (O.S.) (Firm)' -> ('dialogue', 'MATRIARCH', ['O.S.', 'Firm'])."""
    tags = [t.strip() for group in RE_PAREN.findall(label) for t in group.split(",") if t.strip()]
    base = re.sub(r"\s+", " ", RE_PAREN.sub("", label)).strip()
    kind = KIND_BY_LABEL.get(base.upper())
    if kind:
        return kind, None, tags
    return "dialogue", base.upper() or "UNKNOWN", tags


def parse_episode_text(text: str, source: str = "", fallback_number: int = 0) -> Episode:
    meta = {"EPISODE NUMBER": "", "EPISODE LOGLINE": "", "CONTINUITY NOTES": ""}
    warnings: list[str] = []
    scenes: list[Scene] = []
    pending: dict | None = None  # block being assembled
    in_script = False
    last_meta: str | None = None

    def current_scene() -> Scene:
        if not scenes:
            warnings.append("block before any SCENE marker; created an implicit scene 1")
            scenes.append(Scene(number=1))
        return scenes[-1]

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        if pending["start"] is None:
            warnings.append(f"dropped block without timestamps: {pending['label']!r}")
        else:
            text_ = " ".join(pending["lines"]).strip()
            if pending["kind"] == "dialogue" and len(text_) >= 2 and text_[0] in "\"“" and text_[-1] in "\"”":
                text_ = text_[1:-1].strip()
            start, end = pending["start"], pending["end"]
            if end < start:
                warnings.append(f"end before start at {fmt_time(start)}; clamped")
                end = start
            current_scene().blocks.append(Block(
                id="", episode=0, scene=current_scene().number, idx=0,
                kind=pending["kind"], start=start, end=end, text=text_,
                speaker=pending["speaker"], tags=pending["tags"],
            ))
        pending = None

    for raw in text.lstrip("﻿").splitlines():
        line = raw.rstrip()

        if not in_script:
            m = RE_META.match(line)
            if m:
                last_meta = m.group(1).upper()
                meta[last_meta] = m.group(2).strip()
                continue
            if RE_SCRIPT_START.match(line):
                in_script = True
                continue
            if RE_SCENE.match(line):           # script with no START marker
                in_script = True
            else:
                if line.strip() and last_meta:  # wrapped header value
                    meta[last_meta] += " " + line.strip()
                continue

        if RE_SCRIPT_END.match(line):
            break
        if not line.strip() or RE_SECTION.match(line):
            continue

        m = RE_SCENE.match(line)
        if m:
            flush()
            scenes.append(Scene(number=int(m.group(1))))
            continue

        m = RE_SCENE_FIELD.match(line)
        if m and pending is None:
            field_, value = m.group(1).upper(), m.group(2).strip()
            sc = current_scene()
            try:
                if field_ == "HEADING":
                    sc.heading = value
                elif field_ == "START":
                    sc.start = parse_time(value)
                else:
                    sc.end = parse_time(value)
            except ValueError:
                warnings.append(f"bad scene {field_.lower()} {value!r}")
            continue

        m = RE_LABEL.match(line)
        if m:
            flush()
            kind, speaker, tags = _parse_label(m.group(1))
            pending = {"label": m.group(1), "kind": kind, "speaker": speaker, "tags": tags,
                       "start": None, "end": None, "lines": []}
            continue

        m = RE_TIMES.match(line)
        if m and pending is not None and pending["start"] is None:
            try:
                pending["start"], pending["end"] = parse_time(m.group(1)), parse_time(m.group(2))
            except ValueError:
                warnings.append(f"bad timestamp line {line.strip()!r}")
            continue

        if pending is not None:
            pending["lines"].append(line.strip())
        else:
            warnings.append(f"stray line ignored: {line.strip()[:60]!r}")
    flush()

    # Episode number: header first, then filename, then load order.
    number = None
    if m := re.search(r"\d+", meta["EPISODE NUMBER"]):
        number = int(m.group())
    elif m := RE_EP_IN_NAME.search(source):
        number = int(m.group(1))
    else:
        number = fallback_number
        warnings.append(f"no episode number found; using load order {fallback_number}")

    # Stable IDs + scene bounds fallback.
    idx = 0
    for sc in scenes:
        for b in sc.blocks:
            idx += 1
            b.idx, b.episode, b.id = idx, number, f"E{number:02d}-B{idx:03d}"
        if sc.blocks:
            if sc.start is None:
                sc.start = min(b.start for b in sc.blocks)
                warnings.append(f"scene {sc.number}: no start time; used first block")
            if sc.end is None:
                sc.end = max(b.end for b in sc.blocks)
                warnings.append(f"scene {sc.number}: no end time; used last block")

    if idx == 0:
        warnings.append("no timestamped blocks found: is this the expected screenplay format?")

    title = meta["EPISODE NUMBER"] or f"Episode {number}"
    ep = Episode(number=number, title=title, logline=meta["EPISODE LOGLINE"],
                 continuity=meta["CONTINUITY NOTES"], source=source, scenes=scenes)
    ep.warnings = [f"E{number:02d}: {w}" for w in warnings]
    return ep


def _read_sources(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """Return (show title guess, [(file name, text)]) from a .zip, a folder or one .txt."""
    def wanted(name: str) -> bool:
        base = Path(name).name
        return name.lower().endswith(".txt") and "__MACOSX" not in name and not base.startswith(".")

    if path.is_dir():
        files = sorted(f for f in path.rglob("*.txt") if wanted(str(f)))
        return path.name, [(f.name, f.read_text(encoding="utf-8-sig", errors="replace")) for f in files]
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if wanted(n))
            tops = {n.split("/")[0] for n in names if "/" in n}
            title = tops.pop() if len(tops) == 1 else path.stem
            return title, [(Path(n).name, z.read(n).decode("utf-8-sig", errors="replace")) for n in names]
    if path.suffix.lower() == ".txt":
        return path.stem, [(path.name, path.read_text(encoding="utf-8-sig", errors="replace"))]
    raise ValueError(f"Expected a .zip, a folder or a .txt file, got: {path}")


def load_show(path: str | Path, title: str | None = None) -> Show:
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    guessed_title, sources = _read_sources(path)
    episodes = [parse_episode_text(text, source=name, fallback_number=i + 1)
                for i, (name, text) in enumerate(sources)]
    episodes.sort(key=lambda e: e.number)
    seen: set[int] = set()
    for ep in episodes:
        if ep.number in seen:
            ep.warnings.append(f"E{ep.number:02d}: duplicate episode number (from {ep.source})")
        seen.add(ep.number)
    # "Show-scripts (2) (1)" -> "Show"
    clean_title = re.sub(r"(-scripts?)?(\s*\(\d+\))*$", "", guessed_title, flags=re.I).strip()
    return Show(title=title or clean_title, episodes=episodes)


# ── Renderers ────────────────────────────────────────────────────────────────

def _short_time(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}:{secs:04.1f}"


def block_line(b: Block, with_id: bool = True, global_id: bool = False) -> str:
    """One compact line per block. This is what the LLM reads."""
    ref = (b.id if global_id else b.id.split("-")[1]) + " " if with_id else ""
    t = _short_time(b.start)
    if b.kind == "dialogue":
        mood = f" ({', '.join(b.tags)})" if b.tags else ""
        return f'{ref}{t} {b.speaker}{mood}: "{b.text}"'
    label = {"camera": "CAM", "action": "ACT", "sfx": "SFX"}[b.kind]
    return f"{ref}{t} [{label}] {b.text}"


def render_for_llm(ep: Episode, include_logline: bool = True, global_ids: bool = False) -> str:
    out = [f"### EPISODE {ep.number} (length {_short_time(ep.duration)})"]
    if include_logline and ep.logline:
        out.append(f"Logline: {ep.logline}")
    for sc in ep.scenes:
        out.append(f"[Scene {sc.number} | {sc.heading} | {_short_time(sc.start or 0)}-{_short_time(sc.end or 0)}]")
        out += [block_line(b, global_id=global_ids) for b in sc.blocks]
    return "\n".join(out)


def render_markdown(ep: Episode) -> str:
    """Readable transcript for humans (editor review, hand-labelling)."""
    out = [f"# Episode {ep.number}  ·  {fmt_time(ep.duration)}", "", f"> {ep.logline}", ""]
    for sc in ep.scenes:
        out += [f"## Scene {sc.number} · {sc.heading} ({fmt_time(sc.start or 0)} → {fmt_time(sc.end or 0)})", ""]
        for b in sc.blocks:
            t = f"`{fmt_time(b.start)}`"
            if b.kind == "dialogue":
                mood = f" _({', '.join(b.tags)})_" if b.tags else ""
                out.append(f"- {t} **{b.speaker}**{mood}: “{b.text}”")
            else:
                out.append(f"- {t} {b.kind.upper()}: _{b.text}_")
        out.append("")
    return "\n".join(out)

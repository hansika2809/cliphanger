"""Parser tests. Fixtures are synthetic: Kuku's scripts are confidential and never committed."""
import os
import zipfile
from pathlib import Path

import pytest

from cliphanger.parse import block_line, load_show, parse_episode_text, parse_time

SAMPLE = """EPISODE NUMBER: Episode 7
EPISODE LOGLINE: Mara learns the truth about the will.
CONTINUITY NOTES: Follows episode 6.

--- SCRIPT START ---

SCENE 01
Scene heading: INT. STUDY – DAY
Scene start: 00:00.00
Scene end: 00:20.00

Actions and dialogue:

**CAMERA:**
Start: (00:00.00) | End: (00:04.00)
Wide shot of an empty study.

**ACTION:**
Start: (00:01.00) | End: (00:03.50)
Mara slams the door.

**MARA (Furious, Shaking):**
Start: (00:04.10) | End: (00:06.00)
"You lied to me!"

**OLD MAN (O.S.) (Calm):**
Start: (00:06.50) | End: (00:08.00)
"I had no choice."

SCENE 02
SCENE HEADING: EXT. GARDEN – NIGHT
SCENE START: 00:20.00
SCENE END: 01:05.50

ACTIONS AND DIALOGUE:

**SOUND FX:**
Start: (00:20.00) | End: (00:25.00)
[Thunder rolls]

**CAMERA:**
Start: (01:00.00) | End: (01:05.50)
Cut to black.

--- SCRIPT END ---
"""


def test_parse_time():
    assert parse_time("00:39.10") == 39.1
    assert parse_time("01:03.50") == 63.5
    assert parse_time("1:02:03.5") == 3723.5
    assert parse_time("45.2") == 45.2


def test_parses_blocks_ids_and_speakers():
    ep = parse_episode_text(SAMPLE, source="episode-007.txt")
    assert ep.number == 7
    assert ep.logline.startswith("Mara learns")
    assert [s.number for s in ep.scenes] == [1, 2]
    assert [b.id for b in ep.blocks] == [f"E07-B00{i}" for i in range(1, 7)]
    assert [b.kind for b in ep.blocks] == ["camera", "action", "dialogue", "dialogue", "sfx", "camera"]
    mara, old_man = ep.blocks[2], ep.blocks[3]
    assert (mara.speaker, mara.tags, mara.text) == ("MARA", ["Furious", "Shaking"], "You lied to me!")
    assert (old_man.speaker, old_man.tags) == ("OLD MAN", ["O.S.", "Calm"])
    assert mara.start == 4.1 and mara.end == 6.0
    assert ep.warnings == []


def test_uppercase_scene_headers_are_accepted():
    ep = parse_episode_text(SAMPLE)
    garden = ep.scenes[1]
    assert garden.heading == "EXT. GARDEN – NIGHT"
    assert (garden.start, garden.end) == (20.0, 65.5)
    assert ep.duration == 65.5


def test_missing_scene_end_falls_back_to_last_block_with_warning():
    ep = parse_episode_text(SAMPLE.replace("Scene end: 00:20.00\n", ""))
    assert ep.scenes[0].end == 8.0
    assert any("no end time" in w for w in ep.warnings)


def test_episode_number_falls_back_to_filename():
    ep = parse_episode_text(SAMPLE.replace("EPISODE NUMBER: Episode 7\n", ""), source="episode-012-x.txt")
    assert ep.number == 12 and ep.blocks[0].id == "E12-B001"


def test_garbage_input_warns_instead_of_crashing():
    ep = parse_episode_text("this is not a screenplay", fallback_number=3)
    assert ep.number == 3 and ep.blocks == []
    assert any("no timestamped blocks" in w for w in ep.warnings)


def test_block_line_is_compact():
    ep = parse_episode_text(SAMPLE)
    assert block_line(ep.blocks[2]) == 'B003 0:04.1 MARA (Furious, Shaking): "You lied to me!"'
    assert block_line(ep.blocks[4], global_id=True) == "E07-B005 0:20.0 [SFX] [Thunder rolls]"


def test_load_show_from_zip_sorts_episodes_and_reads_title(tmp_path: Path):
    zip_path = tmp_path / "My Show-scripts (2) (1).zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("My Show/episode-002.txt", SAMPLE.replace("Episode 7", "Episode 2"))
        z.writestr("My Show/episode-001.txt", SAMPLE.replace("Episode 7", "Episode 1"))
        z.writestr("__MACOSX/My Show/._episode-001.txt", "junk")
    show = load_show(zip_path)
    assert show.title == "My Show"
    assert [e.number for e in show.episodes] == [1, 2]
    assert show.block("E02-B003").text == "You lied to me!"


def test_load_show_from_folder(tmp_path: Path):
    (tmp_path / "episode-001.txt").write_text(SAMPLE.replace("Episode 7", "Episode 1"))
    show = load_show(tmp_path)
    assert len(show.episodes) == 1 and show.has_block("E01-B006")


# ── Real input pack: runs locally only (the pack is confidential and git-ignored) ──
PACK = os.environ.get("CLIPHANGER_PACK") or next(iter(sorted(Path(__file__).parents[2].glob("*.zip"))), None)


@pytest.mark.skipif(not PACK, reason="input pack not present")
def test_real_pack_parses_cleanly():
    show = load_show(PACK)
    assert len(show.episodes) == 50
    assert sum(len(e.scenes) for e in show.episodes) == 100
    assert sum(len(e.blocks) for e in show.episodes) == 3727
    assert show.warnings == []

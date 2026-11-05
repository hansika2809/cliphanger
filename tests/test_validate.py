"""Snapper tests on a synthetic scene with known shot changes (no Kuku content)."""
from cliphanger.parse import Show, parse_episode_text
from cliphanger.validate import _Index, normalize_id, snap_in, validate

RULES = {"min_seconds": 20, "max_seconds": 60, "hook_within_seconds": 3, "max_segments": 3}


def _blocks(spec):
    out = []
    for label, a, b, text in spec:
        out.append(f"**{label}:**\nStart: (00:{a:05.2f}) | End: (00:{b:05.2f})\n{text}\n")
    return "\n".join(out)


SCRIPT = (
    "EPISODE NUMBER: Episode 1\nEPISODE LOGLINE: Test.\n\n--- SCRIPT START ---\n\nSCENE 01\n"
    "Scene heading: INT. HALL – NIGHT\nScene start: 00:00.00\nScene end: 00:59.00\n\n"
    + _blocks([
        ("CAMERA", 0, 5, "Wide shot of the hall."),                 # B001
        ("ACTION", 1, 5, "She walks slowly."),                      # B002
        ("CAMERA", 5, 12, "Medium shot."),                          # B003
        ("ANNA (Calm)", 6, 8, '"Line one."'),                       # B004
        ("CAMERA", 12, 20, "Close-up."),                            # B005
        ("BEN (Furious)", 12.5, 14, '"You will pay for this!"'),    # B006  hook
        ("ANNA (Cold)", 15, 18, '"Line three."'),                   # B007
        ("CAMERA", 20, 30, "Two-shot."),                            # B008
        ("BEN (Low)", 21, 24, '"Line four."'),                      # B009
        ("CAMERA", 30, 35, "Close-up on Anna."),                    # B010
        ("ANNA (Defiant)", 31, 35, '"Then kill me."'),              # B011  cliffhanger
        ("CAMERA", 35, 37, "Reaction shot of Ben."),                # B012
        ("CAMERA", 37, 40, "Ben lowers the knife."),                # B013
        ("BEN (Quiet)", 37.5, 40, '"Very well."'),                  # B014  resolution
        ("CAMERA", 40, 59, "Wide."),                                # B015
    ])
    + "\n--- SCRIPT END ---\n"
)
SHOW = Show("Test", [parse_episode_text(SCRIPT)])


def idea(start, end, hook="E01-B006", cliff="E01-B011"):
    return {"segments": [{"start": start, "end": end}], "hook_block": hook, "cliffhanger_block": cliff}


def test_late_hook_moves_cut_in_to_the_shot_before_the_hook():
    c = validate(idea("E01-B001", "E01-B011"), 1, SHOW, "p", RULES)
    assert c.status == "repaired"
    assert (c.cuts[0].start, c.cuts[0].end) == (12.0, 35.0)   # starts on the close-up shot
    assert c.hook_at == 0.5 and c.duration == 23.0
    assert any("start" in r for r in c.repairs)


def test_promo_running_into_the_resolution_is_cut_back_to_the_reaction_shot():
    c = validate(idea("E01-B006", "E01-B014"), 1, SHOW, "p", RULES)
    assert c.status == "repaired"
    assert c.cuts[0].end == 37.0          # holds the reaction shot, drops "Very well."
    assert c.cliff_tail == 2.0
    assert c.cuts[0].start == 12.0        # tidied back to the shot change


def test_flash_forward_rescues_a_promo_that_trimming_would_make_too_short():
    # Hook at 12.5 s, cliffhanger "Line four." ends at 24 s: trimming to the hook leaves 14.5 s.
    c = validate(idea("E01-B001", "E01-B009", cliff="E01-B009"), 1, SHOW, "p", RULES)
    assert c.status == "repaired"
    assert [(x.start, x.end) for x in c.cuts] == [(12.0, 14.5), (0.0, 26.5)]   # hook teaser, then build-up
    assert c.hook_at == 0.5 and c.duration == 29.0
    assert c.repairs[0].startswith("flash-forward")


def test_invented_block_id_is_rejected():
    c = validate(idea("E01-B001", "E01-B999"), 1, SHOW, "p", RULES)
    assert c.status == "rejected" and "invented block id E01-B999" in c.issues[0]


def test_too_short_is_rejected_with_reason():
    c = validate(idea("E01-B006", "E01-B007", cliff="E01-B007"), 1, SHOW, "p", RULES)
    assert c.status == "rejected" and any("length" in i for i in c.issues)


def test_cut_in_never_lands_mid_line():
    ix = _Index(shot_starts=[0.0, 5.0, 12.0], shot_ends=[5.0, 12.0], lines=SHOW.episode(1).blocks[3:4])
    assert snap_in(ix, target=7.0, lo=5.5, hi=8.0) == 6.0  # line runs 6–8: cut in at its start


def test_normalize_id():
    assert normalize_id("B034", 7) == "E07-B034"
    assert normalize_id("e1-b34", 7) == "E01-B034"
    assert normalize_id("E12-B005", 7) == "E12-B005"


def test_episode_range_beyond_the_show_is_trimmed_not_a_crash():
    from cliphanger.pipeline import select_episodes
    assert select_episodes(SHOW, None) == ([1], [])
    assert select_episodes(SHOW, [1, 2, 3]) == ([1], [2, 3])
    assert select_episodes(SHOW, [9]) == ([1], [9])          # nothing matches → whole show


def test_judge_rounds_are_averaged_and_disagreement_kept():
    from cliphanger.judge import Verdict, combine
    v = combine([Verdict(hook=8, clarity=9, cliffhanger=6, resolved=False, comprehension=2, understood="a"),
                 Verdict(hook=4, clarity=9, cliffhanger=7, resolved=True, comprehension=2),
                 Verdict(hook=6, clarity=8, cliffhanger=8, resolved=False, comprehension=1)])
    assert (v.hook, v.cliffhanger, v.rounds) == (6.0, 7.0, 3)
    assert v.resolved is False and v.understood == "a"     # majority vote; text from a real round
    assert v.spread["hook"] == 4 and v.spread["clarity"] == 1

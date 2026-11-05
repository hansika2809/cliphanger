"""Script Player: plays a promo as a real-time read-along on a phone-shaped screen.

Shot notes, actions, sounds and dialogue appear exactly when they would in the cut, with the
3-second hook window and segment cuts marked on the timeline, and a CUT card on the cliffhanger.
It lets an editor feel a promo's pacing before any video is cut.
"""
from __future__ import annotations

import json

_TEMPLATE = r"""<meta charset="utf-8">
<div id="root"></div>
<style>
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  .phone { position: relative; width: 244px; height: 434px; border-radius: 24px; background: #0e0e11;
           overflow: hidden; border: 3px solid #2a2a31; box-shadow: 0 6px 22px rgba(0,0,0,.28); }
  .shot { position: absolute; top: 38px; left: 14px; right: 14px; color: #a0a6b0; font-size: 11px;
          font-style: italic; line-height: 1.35; }
  .sfx { position: absolute; top: 31%; left: 16px; right: 16px; color: #f2c14e; font-size: 11px; text-align: center; }
  .act { position: absolute; top: 41%; left: 16px; right: 16px; color: #ececef; font-size: 13px;
         text-align: center; line-height: 1.4; }
  .cap { position: absolute; bottom: 62px; left: 12px; right: 12px; text-align: center; }
  .who { display: block; color: #ffd166; font-size: 10px; letter-spacing: .07em; font-weight: 700; margin-bottom: 3px; }
  .line { color: #fff; font-size: 16px; font-weight: 750; line-height: 1.28; text-shadow: 0 1px 3px #000; }
  .hook { position: absolute; top: 12px; left: 10px; background: #e34948; color: #fff; font-size: 9.5px;
          font-weight: 800; padding: 3px 7px; border-radius: 10px; letter-spacing: .04em; }
  .ep { position: absolute; top: 15px; right: 12px; color: #8ecae6; font-size: 9.5px; font-weight: 700; }
  .t { position: absolute; left: 12px; bottom: 30px; color: #a0a6b0; font-size: 10.5px; }
  .bar { position: absolute; left: 12px; right: 12px; bottom: 18px; height: 6px; background: #2e2e36;
         border-radius: 3px; cursor: pointer; }
  .fill { height: 100%; width: 0; background: #e34948; border-radius: 3px; }
  .mark { position: absolute; top: -4px; width: 2px; height: 14px; }
  .cut { position: absolute; inset: 0; display: none; flex-direction: column; align-items: center;
         justify-content: center; background: rgba(8,8,10,.86); color: #fff; text-align: center; padding: 22px; }
  .cut b { font-size: 20px; letter-spacing: .08em; }
  .cut span { font-size: 12.5px; color: #d6d6dc; margin-top: 10px; line-height: 1.4; }
  .ctl { margin-top: 10px; display: flex; gap: 8px; }
  button { font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 8px; border: 1px solid #9a9aa2;
           background: #fff; color: #111; cursor: pointer; }
</style>
<script>
const D = __DATA__;
const root = document.getElementById("root");
root.innerHTML = `
  <div class="phone" id="ph">
    <div class="shot" id="shot"></div><div class="sfx" id="sfx"></div><div class="act" id="act"></div>
    <div class="cap"><span class="who" id="who"></span><span class="line" id="line"></span></div>
    <div class="hook" id="hook">HOOK WINDOW · 3s</div><div class="ep" id="ep"></div>
    <div class="t" id="tm"></div>
    <div class="bar" id="bar"><div class="fill" id="fill"></div></div>
    <div class="cut" id="cut"><b>✂ CUT</b><span id="cl"></span><span>Would you tap for the next episode?</span></div>
  </div>
  <div class="ctl"><button id="pp">▶ Play</button><button id="rs">↺ Restart</button></div>`;
const $ = (id) => document.getElementById(id);
$("cl").textContent = D.cliff;
const bar = $("bar");
function addMark(at, color, title) {
  const m = document.createElement("div"); m.className = "mark"; m.title = title;
  m.style.left = (100 * at / D.dur) + "%"; m.style.background = color; bar.appendChild(m);
}
addMark(Math.min(3, D.dur), "#ffd166", "3-second hook deadline");
D.segs.slice(1).forEach(s => addMark(s.at, "#8ecae6", "cut to " + s.label));
let t = 0, playing = false, last = null;
function current(kind, linger) {
  let cur = null;
  for (const it of D.items) if (it.k === kind && it.t <= t && t < it.t + Math.max(it.d, linger)) cur = it;
  return cur;
}
function render() {
  const shot = current("camera", 0.1), act = current("action", 1.2), sfx = current("sfx", 0.8), dl = current("dialogue", 0.35);
  $("shot").textContent = shot ? "🎥 " + shot.x : "";
  $("act").textContent = act ? act.x : "";
  $("sfx").textContent = sfx ? "🔊 " + sfx.x : "";
  $("who").textContent = dl ? dl.s : "";
  $("line").textContent = dl ? "“" + dl.x + "”" : "";
  let seg = D.segs[0]; for (const s of D.segs) if (s.at <= t) seg = s;
  $("ep").textContent = seg ? seg.label : "";
  $("hook").style.display = t < 3 ? "block" : "none";
  $("fill").style.width = (100 * t / D.dur) + "%";
  $("tm").textContent = t.toFixed(1) + " / " + D.dur.toFixed(1) + " s";
  $("cut").style.display = t >= D.dur ? "flex" : "none";
}
function tick(ts) {
  if (!playing) return;
  if (last !== null) t = Math.min(D.dur, t + (ts - last) / 1000);
  last = ts; render();
  if (t >= D.dur) { playing = false; $("pp").textContent = "▶ Play"; return; }
  requestAnimationFrame(tick);
}
$("pp").onclick = () => {
  if (t >= D.dur) t = 0;
  playing = !playing; last = null; $("pp").textContent = playing ? "❚❚ Pause" : "▶ Play";
  if (playing) requestAnimationFrame(tick);
};
$("rs").onclick = () => { t = 0; render(); };
bar.onclick = (e) => { const r = bar.getBoundingClientRect(); t = Math.max(0, Math.min(D.dur, (e.clientX - r.left) / r.width * D.dur)); render(); };
render();
</script>
"""


def player_html(record: dict) -> str:
    segs, at = [], 0.0
    for s in record["segments"]:
        segs.append({"at": round(at, 2), "label": f"E{s['episode']:02d} · {s['start']}"})
        at += s["seconds"]
    data = {
        "dur": record["duration"],
        "cliff": record["cliffhanger"]["script_line"],
        "segs": segs,
        "items": [{"t": x["t"], "d": x["dur"], "k": x["kind"], "s": x["speaker"] or "", "x": x["text"]}
                  for x in record["script"]],
    }
    return _TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))

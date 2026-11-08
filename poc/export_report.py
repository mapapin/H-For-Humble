"""One self-contained HTML file per audit: score, perspectives, replays, barriers, static scan, before/after.

Images are embedded (JPEG, base64) and the replay player / sliders are a few lines of vanilla JS, so the file opens
anywhere, offline, without the app, an H key or Chrome. Used for "Export report" and for the sample reports
judges can open from the repo.
"""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path

from personas import PERSONAS

NAMES = {"baseline": "Baseline", "low_vision": "Low vision", "color_blind": "Colour blindness", "tremor": "Hand tremor",
         "keyboard_only": "Keyboard only"}
SHORT = {"baseline": "Reference user", "low_vision": "200% zoom + blur", "color_blind": "Deuteranopia",
         "tremor": "Imprecise pointer (±10 px)", "keyboard_only": "No mouse"}


def _img(path: Path, width: int = 960, quality: int = 62) -> str | None:
    from PIL import Image

    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return None
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _dur(s) -> str:
    if s is None:
        return "—"
    s = float(s)
    return f"{int(s // 60)}m {int(s % 60):02d}s" if s >= 60 else f"{round(s)}s"


def build(audit: dict, audit_dir: str | Path, after: dict | None = None) -> str:
    """after: the scores of a re-evaluation of this audit with its fixes injected (shown as the verified result)."""
    d, e = Path(audit_dir), html.escape
    scores = audit.get("scores") or {}
    per = scores.get("perspectives") or {}
    results = audit.get("results") or []
    tasks = {t["id"]: t["title"] for t in audit["config"]["tasks"]}
    replays = {}
    tiles = []
    for persona in audit["config"]["personas"]:
        runs = [r for r in results if r.get("persona") == persona]
        p = per.get(persona) or {}
        run = runs[-1] if runs else None
        shot = None
        if run:
            frames = (run.get("replay") or {}).get("frames") or []
            steps = (run.get("replay") or {}).get("steps") or []
            imgs = [_img(d / f["file"], 1100, 60) if f.get("file") else None for f in frames]
            replays[run["tag"]] = {"persona": NAMES.get(persona, persona), "frames": imgs, "steps": [
                {k: s.get(k) for k in ("thought", "frame", "effects")} | {"action": (s.get("action") or {})}
                for s in steps]}
            if run.get("final_screenshot"):
                shot = _img(d / run["final_screenshot"], 640, 60)
            elif imgs and imgs[-1]:
                shot = imgs[-1]
        ok = p.get("completion")
        state = "pass" if ok == 1 else "fail" if ok == 0 else "partial" if ok else "none"
        label = {"pass": "Completed", "fail": "Blocked", "partial": f"{round(100 * (ok or 0))}% completed", "none": "Not run"}[state]
        ratio = p.get("steps_vs_baseline")
        tiles.append(f"""<article class="tile {state}"><header><div><h3>{e(NAMES.get(persona, persona))}</h3><small>{e(SHORT.get(persona, ''))}</small></div>
<span class="grade g-{p.get('grade') or 'none'}">{e(p.get('grade') or '–')}</span></header>
<div class="screen">{f'<img src="{shot}" alt="">' if shot else ''}{f'<button data-replay="{e(run["tag"])}">▶ Replay</button>' if run and replays.get(run["tag"], {}).get("steps") else ''}</div>
<footer><span class="chip {state}">{label}</span><dl><div><dt>Steps</dt><dd>{p.get('steps') or '—'}{f' <em>{ratio:.1f}×</em>' if ratio and ratio >= 1.15 else ''}</dd></div>
<div><dt>Time</dt><dd>{_dur(p.get('time_s'))}</dd></div><div><dt>Barriers</dt><dd>{p.get('barriers', 0)}</dd></div><div><dt>Score</dt><dd><b>{p.get('score') if p.get('score') is not None else '—'}</b></dd></div></dl></footer></article>""")
    sev = {"blocking": 0, "major": 1, "minor": 2}
    barriers = sorted(((o, r) for r in results for o in (r.get("report") or {}).get("obstacles") or []),
                      key=lambda x: (sev.get(x[0].get("severity"), 3), x[1]["persona"] == "baseline"))
    barrier_html = "".join(
        f"""<li class="sev-{e(o.get('severity', ''))}"><span class="sev">{e(o.get('severity', ''))}</span><div><p>{e(o.get('what', ''))}</p>
<small>{e(NAMES.get(r['persona'], r['persona']))} · {e(o.get('where', ''))}</small></div></li>""" for o, r in barriers[:10])
    fixes = audit.get("fixes") or {}
    fix_dir = d / "fixes"
    comps = []
    for c in fixes.get("comparisons") or []:
        if c.get("kind") != "persona" or not c.get("before"):
            continue
        b, a = _img(fix_dir / c["before"], 1100, 70), _img(fix_dir / c["after"], 1100, 70)
        if b and a:
            comps.append(f"""<figure><div class="compare" style="--pos:{c.get('split') or 50}%"><img src="{b}" alt="Before"><img class="after" src="{a}" alt="After">
<span class="line"></span><span class="tag l">Before</span><span class="tag r">After</span><input type="range" min="0" max="100" value="{c.get('split') or 50}" aria-label="Compare before and after"></div>
<figcaption>{e(NAMES.get(c['persona'], c['persona']))} · {e(c.get('page_url', ''))}</figcaption></figure>""")
    fix_list = "".join(f"<li><b>WCAG {e(f.get('wcag', ''))}</b> {e(f.get('title', ''))}<small>{e(f.get('code_change', ''))}</small></li>"
                       for f in (fixes.get("patch") or {}).get("fixes", []))
    static = audit.get("static_scan") or {}
    blocked = [k for k, p in per.items() if k != "baseline" and p.get("completion") == 0]
    covered = {p for v in static.get("violations") or [] for p in v.get("personas", [])}
    missed = [NAMES.get(p, p) for p in blocked if p not in covered]
    static_html = ""
    if static.get("totals"):
        static_html = f"""<section class="card"><h2>Static checker vs real journeys</h2><p class="lead">axe-core flags <b>{static['totals']['rules']} rule(s)</b> on the
{len(static.get('pages') or [])} pages the agents visited. Agents found <b>{len(blocked)} perspective(s) blocked</b>{f", of which no rule detects: {e(', '.join(missed))}" if missed else ''}.</p>
<ul class="static">{''.join(f"<li><span>{e(v.get('impact') or '')}</span>{e(v['help'])} <small>({e(v['id'])}, {v['nodes']} element(s))</small></li>" for v in (static.get('violations') or [])[:6])}</ul></section>"""
    ctx = audit.get("context") or {}
    ctx_html = f"""<section class="card ctx"><div><small>DECLARED (found by Linkup)</small><p>{e(ctx.get('declared_compliance', ''))}</p></div><div><small>MEASURED BY HUMBLE</small>
<p>{scores.get('impaired_blocked', 0)} impaired perspective(s) blocked · score {scores.get('score')}/100</p></div></section>""" if ctx.get("declared_compliance") else ""
    blocked_names = ", ".join(NAMES.get(k, k) for k in blocked)
    headline = (f"<b>{scores.get('can_complete')} of {scores.get('tested_perspectives')}</b> perspectives completed the journey."
                + (f" <span class='bad'>Blocked: {e(blocked_names)}.</span>" if blocked else "")) if scores.get("score") is not None else "Not scored."
    if after and after.get("score") is not None:
        done = sum(1 for p in (after.get("perspectives") or {}).values() if p.get("completion") == 1)
        total = len(after.get("perspectives") or {})
        who = "Devin's" if (audit.get("fixes") or {}).get("source") == "devin" else "the suggested"
        headline += (f"<br><span class='verified'>Re-tested with {who} fixes injected: <b>{scores.get('score')} → "
                     f"{after['score']}</b>, {done} of {total} perspectives complete.</span>")
    data = json.dumps(replays).replace("</", "<\\/")
    title = f"Humble report · {audit['name']}"
    return TEMPLATE.replace("{{TITLE}}", e(title)).replace("{{NAME}}", e(audit["name"])).replace("{{URL}}", e(audit["url"])) \
        .replace("{{TASKS}}", e(" · ".join(tasks.values()))).replace("{{SCORE}}", str(scores.get("score", "–"))) \
        .replace("{{GRADE}}", e(scores.get("grade") or "–")).replace("{{PCT}}", str(scores.get("score") or 0)) \
        .replace("{{HEADLINE}}", headline).replace("{{TILES}}", "".join(tiles)).replace("{{CTX}}", ctx_html) \
        .replace("{{BARRIERS}}", barrier_html or "<li>No barrier reported.</li>").replace("{{STATIC}}", static_html) \
        .replace("{{COMPARE}}", "".join(comps)).replace("{{FIXES}}", fix_list) \
        .replace("{{FIXSECTION}}", "none" if not fix_list else "block").replace("{{DATA}}", data) \
        .replace("{{RING}}", {"A": "#2f7d57", "B": "#2f7d57", "C": "#a8741a"}.get(scores.get("grade") or "", "#b8433b"))


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title><style>
:root{--bg:#f6f5f1;--paper:#fdfcfa;--ink:#292a33;--muted:#6b6573;--line:#e8e7e2;--accent:#5149bd;--good:#2f7d57;--poor:#b8433b;--fair:#a8741a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif}
main{max-width:1180px;margin:auto;padding:36px 22px 60px}h1,h2{font-family:Georgia,serif;font-weight:400;margin:0}h1{font-size:40px}h2{font-size:24px;margin-bottom:12px}
.top small{color:var(--muted)}.hero{display:flex;gap:30px;align-items:center;background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:26px 30px;margin:22px 0}
.ring{width:140px;height:140px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--c) calc({{PCT}}*1%),#ebe8f1 0);flex-shrink:0}
.ring div{width:112px;height:112px;border-radius:50%;background:var(--paper);display:flex;flex-direction:column;align-items:center;justify-content:center}
.ring b{font:400 40px Georgia,serif}.ring small{font-size:11px;font-weight:700;letter-spacing:1px;color:var(--muted)}
.hero p{font:400 26px/1.3 Georgia,serif;margin:0}.verified{display:inline-block;margin-top:10px;font:600 15px/1.4 system-ui,sans-serif;color:#25684a;background:#e3f1e9;padding:6px 12px;border-radius:8px}.hero p b{font-weight:400;color:var(--accent)}.bad{color:var(--poor)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.tile{background:var(--paper);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.tile.pass{border-color:#cfe5d8}.tile.fail{border-color:#efd1cd}.tile header{display:flex;justify-content:space-between;align-items:center;padding:12px 14px}
.tile h3{margin:0;font-size:14px}.tile header small{color:var(--muted);font-size:11px}.grade{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;color:#fff;font:19px Georgia,serif;background:var(--poor)}
.g-A,.g-B{background:var(--good)}.g-C{background:var(--fair)}.g-none{background:#cfcbd8}.screen{position:relative;aspect-ratio:16/10;background:#eee;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.screen img{width:100%;height:100%;object-fit:cover;object-position:top left}.screen button{position:absolute;right:8px;bottom:8px;border:0;border-radius:8px;background:#1f1b2ce0;color:#fff;font-weight:700;padding:7px 12px;cursor:pointer}
.tile footer{padding:12px 14px}.chip{display:inline-block;font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:99px}.chip.pass{background:#e3f1e9;color:var(--good)}.chip.fail{background:#f8e5e2;color:var(--poor)}.chip.partial{background:#f7eedb;color:var(--fair)}
dl{display:grid;grid-template-columns:repeat(4,auto);gap:4px 10px;margin:10px 0 0;justify-content:space-between}dt{font-size:10px;color:var(--muted);text-transform:uppercase}dd{margin:2px 0 0;font-weight:600}em{font-style:normal;color:var(--poor);font-size:11px}
.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-top:22px}.ctx{display:grid;grid-template-columns:1fr 1fr;gap:20px}.ctx small{color:var(--muted);font-weight:700;letter-spacing:1px;font-size:11px}.ctx p{margin:6px 0 0;font-weight:600}
ul.bar{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px}ul.bar li{display:flex;gap:12px;padding:10px 12px;border:1px solid var(--line);border-radius:10px}ul.bar p{margin:0}ul.bar small{color:var(--muted)}
.sev{font-size:10px;font-weight:700;text-transform:uppercase;padding:3px 6px;border-radius:4px;background:#f2ebde;height:max-content}.sev-blocking .sev{background:#f3e3e5;color:var(--poor)}
.lead{font:400 20px/1.35 Georgia,serif;margin:0 0 10px}.lead b{font-weight:400;color:var(--accent)}ul.static li span{font-size:10px;font-weight:700;text-transform:uppercase;margin-right:8px;color:var(--poor)}
.compare{position:relative;overflow:hidden;border-radius:12px;border:1px solid var(--line);line-height:0}.compare img{width:100%;display:block}.compare .after{position:absolute;inset:0;clip-path:inset(0 0 0 var(--pos))}
.compare .line{position:absolute;top:0;bottom:0;left:var(--pos);width:3px;background:var(--accent)}.compare input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:ew-resize;margin:0}
.tag{position:absolute;top:8px;font:700 11px system-ui;padding:3px 8px;border-radius:6px;background:#1f1b2cc0;color:#fff;line-height:1.4}.tag.l{left:8px}.tag.r{right:8px}
figure{margin:0 0 18px}figcaption{color:var(--muted);font-size:12px;margin-top:6px}ul.fixes li{margin-bottom:8px}ul.fixes small{display:block;color:var(--muted)}
.player{position:fixed;inset:0;background:#29243699;display:none;align-items:center;justify-content:center;padding:20px;z-index:9}.player.open{display:flex}
.box{background:var(--paper);border-radius:14px;max-width:1100px;width:100%;max-height:96vh;overflow:auto;padding:16px}.frame{position:relative;line-height:0;border:1px solid var(--line);border-radius:10px;overflow:hidden}.frame img{width:100%}
.dot{position:absolute;width:30px;height:30px;margin:-15px 0 0 -15px;border:3px solid var(--accent);border-radius:50%}.hit{position:absolute;width:12px;height:12px;margin:-6px 0 0 -6px;background:#ff5a36;border:2px solid #fff;border-radius:50%}
.flag{position:absolute;top:10px;left:10px;background:var(--poor);color:#fff;font:700 12px system-ui;padding:6px 10px;border-radius:7px;line-height:1.3}.flag.t{background:#c2601f}
.ctl{display:flex;gap:8px;align-items:center;margin-top:10px}.ctl button{border:1px solid var(--line);background:#fff;border-radius:7px;padding:6px 12px;cursor:pointer}.ctl input{flex:1}.cap{min-height:48px;margin-top:8px}.cap b{color:var(--accent)}
footer.page{margin-top:40px;color:var(--muted);font-size:12px}
</style></head><body><main>
<div class="top"><small>{{URL}} · HUMBLE ACCESSIBILITY REPORT</small><h1>{{NAME}}</h1><small>{{TASKS}}</small></div>
<section class="hero"><div class="ring" style="--c:{{RING}}"><div><b>{{SCORE}}</b><small>GRADE {{GRADE}}</small></div></div><p>{{HEADLINE}}</p></section>
{{CTX}}
<h2 style="margin-top:28px">Perspectives</h2><div class="grid">{{TILES}}</div>
<section class="card"><h2>What stopped users</h2><ul class="bar">{{BARRIERS}}</ul></section>
{{STATIC}}
<section class="card" style="display:{{FIXSECTION}}"><h2>Fixes, before and after</h2>{{COMPARE}}<ul class="fixes">{{FIXES}}</ul></section>
<footer class="page">Generated by Humble: H computer-use agents run each journey in a browser with the impairment applied (Chrome vision emulation, zoom,
input filters for tremor and keyboard-only). Simulated users, not a certification.</footer></main>
<div class="player" role="dialog" aria-modal="true" aria-label="Replay"><div class="box"><div class="ctl"><b id="pt"></b><span style="flex:1"></span><button id="pc">Close</button></div>
<div class="frame"><img id="pi" alt="Replay frame"><span class="dot" id="pd" hidden></span><span class="hit" id="ph" hidden></span><span class="flag" id="pf" hidden></span></div>
<div class="cap" id="pcap"></div><div class="ctl"><button id="pp">⏸</button><button id="pv">◀</button><button id="pn">▶</button><input type="range" id="pr" min="0" value="0" aria-label="Step"></div></div></div>
<script>
const R = {{DATA}};
document.querySelectorAll('.compare input').forEach(i => i.addEventListener('input', () => i.parentElement.style.setProperty('--pos', i.value + '%')));
const P = document.querySelector('.player'); let cur = null, idx = 0, timer = null;
function show() { const s = cur.steps[idx] || {}; let f = null; for (let i = idx; i >= 0 && f == null; i--) if (cur.steps[i] && cur.steps[i].frame != null) f = cur.steps[i].frame;
  if (f != null && cur.frames[f]) document.getElementById('pi').src = cur.frames[f];
  const a = s.action || {}, ef = s.effects || {}, d = document.getElementById('pd'), h = document.getElementById('ph'), fl = document.getElementById('pf');
  if (a.x != null) { d.hidden = false; d.style.left = a.x * 100 + '%'; d.style.top = a.y * 100 + '%'; } else d.hidden = true;
  if (ef.jitter && a.x != null) { const img = document.getElementById('pi'); h.hidden = false; h.style.left = (a.x + ef.jitter[0] / (img.naturalWidth || 1280)) * 100 + '%'; h.style.top = (a.y + ef.jitter[1] / (img.naturalHeight || 800)) * 100 + '%'; } else h.hidden = true;
  fl.hidden = !(ef.blocked || ef.jitter); fl.className = 'flag' + (ef.jitter && !ef.blocked ? ' t' : ''); fl.textContent = ef.blocked ? 'Mouse blocked: this user cannot use a mouse' : ef.jitter ? 'Tremor: the click landed off target' : '';
  document.getElementById('pcap').innerHTML = '<b>Step ' + (idx + 1) + '/' + cur.steps.length + '</b> ' + (s.thought || '').replace(/</g, '&lt;') + (a.label ? '<br><small>→ ' + a.label.replace(/</g, '&lt;') + '</small>' : '');
  document.getElementById('pr').value = idx; }
function play(on) { clearInterval(timer); timer = on ? setInterval(() => { if (idx >= cur.steps.length - 1) return play(false); idx++; show(); }, 1500) : null; document.getElementById('pp').textContent = on ? '⏸' : '▶︎'; }
document.querySelectorAll('[data-replay]').forEach(b => b.addEventListener('click', () => { cur = R[b.dataset.replay]; idx = 0; document.getElementById('pt').textContent = 'Replay · ' + cur.persona; document.getElementById('pr').max = cur.steps.length - 1; P.classList.add('open'); show(); play(true); }));
document.getElementById('pc').onclick = () => { play(false); P.classList.remove('open'); };
document.getElementById('pp').onclick = () => play(!timer); document.getElementById('pn').onclick = () => { play(false); idx = Math.min(idx + 1, cur.steps.length - 1); show(); };
document.getElementById('pv').onclick = () => { play(false); idx = Math.max(idx - 1, 0); show(); }; document.getElementById('pr').oninput = e => { play(false); idx = +e.target.value; show(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape' && P.classList.contains('open')) document.getElementById('pc').click(); });
</script></body></html>"""

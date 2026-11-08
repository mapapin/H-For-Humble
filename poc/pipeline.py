"""Fix workflows on top of audits.

  suggest     audit -> fix patch (Devin, or the canned stand-in) -> before/after images -> fixes report (sliders)
  reevaluate  re-run the same audit with the patch injected in every page -> before/after scores
  auto        audit -> fix -> re-audit -> ... until every persona passes (or max rounds) -> final summary

Usage (demo shop running, see README):
  .venv/bin/python poc/pipeline.py suggest --audit poc/runs/<dir>
  .venv/bin/python poc/pipeline.py reevaluate --audit poc/runs/<dir>
  .venv/bin/python poc/pipeline.py auto --preset poc/presets/demo_shop.yaml --max-rounds 2

The functions are importable (the web app calls them); records/rows are the audit's run records and summary.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from audit import expand_env, load_dotenv, run_audit  # noqa: E402
from fixes import load_results, make_fixer, merge_patches  # noqa: E402
from personas import PERSONAS  # noqa: E402
from render import build_comparisons  # noqa: E402


# ------------------------------------------------------------------------------------------ scoring
def persona_rates(rows: list[dict]) -> dict[str, float | None]:
    rates: dict[str, list[float]] = {}
    for r in rows:
        if r.get("success_rate") is not None:
            rates.setdefault(r["persona"], []).append(r["success_rate"])
    return {p: round(sum(v) / len(v), 2) for p, v in rates.items()}


def score(rows: list[dict]) -> int:
    """0-100: mean success rate over all personas and tasks."""
    vals = [r["success_rate"] for r in rows if r.get("success_rate") is not None]
    return round(100 * sum(vals) / len(vals)) if vals else 0


def passes(rows: list[dict], pass_rate: float = 1.0) -> bool:
    return bool(rows) and all((r.get("success_rate") or 0) >= pass_rate for r in rows)


def compare_rows(before: list[dict], after: list[dict]) -> list[dict]:
    a = {(r["task"], r["persona"]): r for r in after}
    out = []
    for b in before:
        x = a.get((b["task"], b["persona"]), {})
        out.append({"task": b["task"], "persona": b["persona"],
                    "success_before": b.get("success_rate"), "success_after": x.get("success_rate"),
                    "steps_before": b.get("mean_steps"), "steps_after": x.get("mean_steps")})
    return out


def _site(preset_path: str | Path) -> dict:
    load_dotenv(HERE.parent / ".env")
    return expand_env(yaml.safe_load(Path(preset_path).read_text()))["site"]


# ------------------------------------------------------------------------------------------ workflows
def suggest_fixes(records: list[dict], rows: list[dict], base_dir: str | Path, site: dict, fixer,
                  previous: dict | None = None, out_dir: str | Path | None = None) -> dict:
    """Ask the fixer for a patch for what failed, then render before/after images of every fix in it."""
    base_dir = Path(base_dir)
    out_dir = Path(out_dir or base_dir / "fixes")
    out_dir.mkdir(parents=True, exist_ok=True)
    new = fixer.generate(records, rows, base_dir, site, previous)
    patch = merge_patches(previous, new)
    (out_dir / "patch.json").write_text(json.dumps(patch, indent=2))
    headers = (site.get("auth") or {}).get("headers")
    getattr(fixer, "on_progress", lambda **kw: None)(
        fix_status="rendering", message="Fixes are ready. Preparing before/after previews…")
    comparisons = build_comparisons(records, base_dir, patch, out_dir, headers=headers) if patch["fixes"] else []
    suggestions = {"created": time.time(), "source": new.get("source"), "devin_session_url": new.get("devin_session_url"),
                   "fallback_reason": new.get("fallback_reason"),
                   "new_fix_ids": [f["id"] for f in new["fixes"]], "patch": patch, "comparisons": comparisons,
                   "score": score(rows), "rates": persona_rates(rows)}
    (out_dir / "suggestions.json").write_text(json.dumps(suggestions, indent=2))
    return suggestions


def suggest(run_dir: str | Path, fixer_kind: str = "auto") -> Path:
    run_dir = Path(run_dir)
    results = load_results(run_dir)
    site = _site(results["config"]["preset"])
    sug = suggest_fixes(results["runs"], results["summary"], run_dir, site, make_fixer(fixer_kind))
    report = write_fix_report(run_dir / "fixes", "Suggested fixes", results["summary"], sug,
                              reevaluate_cmd=f".venv/bin/python poc/pipeline.py reevaluate --audit {run_dir}")
    print(f"{len(sug['patch']['fixes'])} fixes ({sug['source']}), report: {report}")
    return report


def reevaluate(run_dir: str | Path, patch_path: str | Path | None = None, lanes: int | None = None) -> Path:
    """Re-run the audit's personas and tasks with the patch injected; record before/after scores."""
    run_dir = Path(run_dir)
    results = load_results(run_dir)
    cfg = results["config"]
    patch = json.loads(Path(patch_path or run_dir / "fixes" / "patch.json").read_text())
    after_dir = run_audit(cfg["preset"], cfg["personas"], runs=cfg["runs"], lanes=lanes or cfg["lanes"],
                          tasks=cfg["tasks"], patch=patch, label="patched", wait_for_quota=True)
    after = load_results(after_dir)
    reeval = {"after_run": str(after_dir), "score_before": score(results["summary"]),
              "score_after": score(after["summary"]), "rows": compare_rows(results["summary"], after["summary"])}
    (run_dir / "fixes" / "reevaluation.json").write_text(json.dumps(reeval, indent=2))
    sug = json.loads((run_dir / "fixes" / "suggestions.json").read_text())
    report = write_fix_report(run_dir / "fixes", "Suggested fixes", results["summary"], sug, reevaluation=reeval)
    print(f"score {reeval['score_before']} -> {reeval['score_after']}, report: {report}")
    return report


def auto(preset: str | Path, personas: list[str], runs: int = 1, lanes: int = 3, max_rounds: int = 3,
         fixer_kind: str = "auto", pass_rate: float = 1.0, tasks: list[str] | None = None,
         base_port: int = 9400) -> Path:
    """Audit, fix what failed, re-audit with the fixes, repeat until every persona passes or max_rounds."""
    root = HERE / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}-auto"
    root.mkdir(parents=True)
    site, fixer = _site(preset), make_fixer(fixer_kind)
    patch, rounds, first = None, [], None
    for rnd in range(max_rounds + 1):
        run_dir = run_audit(preset, personas, runs=runs, lanes=lanes, tasks=tasks, patch=patch, label=f"auto-r{rnd}",
                            wait_for_quota=rnd > 0, base_port=base_port)
        res = load_results(run_dir)
        first = first or (res, run_dir)
        entry = {"round": rnd, "run_dir": str(run_dir), "score": score(res["summary"]),
                 "rates": persona_rates(res["summary"]), "fixes_applied": [f["id"] for f in (patch or {}).get("fixes", [])],
                 "passed": passes(res["summary"], pass_rate)}
        rounds.append(entry)
        print(f"round {rnd}: score {entry['score']} rates {entry['rates']} passed={entry['passed']}", flush=True)
        if entry["passed"] or rnd == max_rounds:
            break
        new = fixer.generate(res["runs"], res["summary"], run_dir, site, patch)
        if not new["fixes"]:
            entry["stopped"] = "the fixer had no new fix to suggest"
            break
        patch = merge_patches(patch, new)
        entry["new_fixes"] = [f["id"] for f in new["fixes"]]
        (root / f"patch-r{rnd + 1}.json").write_text(json.dumps(patch, indent=2))
    # Final before/after: round-0 pages (what users first saw) without vs with the final patch.
    res0, dir0 = first
    comparisons = build_comparisons(res0["runs"], dir0, patch, root, headers=(site.get("auth") or {}).get("headers")) \
        if patch else []
    summary = {"preset": str(preset), "personas": personas, "pass_rate": pass_rate, "rounds": rounds,
               "passed": rounds[-1]["passed"], "patch": patch or {"fixes": []}, "comparisons": comparisons,
               "score_before": rounds[0]["score"], "score_after": rounds[-1]["score"]}
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    report = write_fix_report(root, "Automatic fix loop", res0["summary"],
                              {"patch": summary["patch"], "comparisons": comparisons, "source": fixer.source},
                              rounds=rounds)
    print(f"auto: {len(rounds)} round(s), score {summary['score_before']} -> {summary['score_after']}, "
          f"passed={summary['passed']}, report: {report}")
    return report


# ------------------------------------------------------------------------------------------ report
SLIDER_CSS = """
.ba{position:relative;display:block;max-width:100%;overflow:hidden;border:1px solid #d6d3c8;border-radius:8px;--pos:50%}
.ba img{display:block;width:100%;height:auto}
.ba .after{position:absolute;inset:0;clip-path:inset(0 0 0 var(--pos))}
.ba .line{position:absolute;top:0;bottom:0;left:var(--pos);width:3px;margin-left:-1px;background:#3b3fb6;pointer-events:none}
.ba .tag{position:absolute;top:8px;padding:2px 8px;border-radius:4px;background:#1f2137cc;color:#fff;font:600 12px system-ui}
.ba .tag.b{left:8px}.ba .tag.a{right:8px}
.ba input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:ew-resize}
"""


def slider(before: str, after: str, label: str, split: float = 50) -> str:
    e = html.escape
    return (f'<div class="ba" style="--pos:{split}%"><img src="{e(before)}" alt="Before: {e(label)}">'
            f'<img class="after" src="{e(after)}" alt="After: {e(label)}"><div class="line"></div>'
            f'<span class="tag b">Before</span><span class="tag a">After</span>'
            f'<input type="range" min="0" max="100" value="{split}" aria-label="Compare before and after: {e(label)}" '
            f'oninput="this.parentNode.style.setProperty(\'--pos\', this.value + \'%\')"></div>')


def write_fix_report(out_dir: Path, title: str, rows: list[dict], sug: dict, reevaluation: dict | None = None,
                     rounds: list[dict] | None = None, reevaluate_cmd: str | None = None) -> Path:
    e = html.escape
    fixes = sug["patch"]["fixes"]
    comps = {(c["kind"], c["id"]): c for c in sug.get("comparisons", []) if c.get("before")}
    label = lambda p: PERSONAS[p].label if p in PERSONAS else p  # noqa: E731

    def pct(v):
        return "–" if v is None else f"{round(v * 100)}%"

    parts = [f"<h1>{e(title)}</h1>"]
    if rounds:
        trs = "".join(
            f"<tr><td>Round {r['round']}</td><td>{r['score']}</td>"
            + "".join(f"<td>{pct(r['rates'].get(p))}</td>" for p in rounds[0]["rates"])
            + f"<td>{e(', '.join(r['fixes_applied']) or 'none')}</td><td>{'✅' if r['passed'] else '❌'}</td></tr>"
            for r in rounds)
        heads = "".join(f"<th>{e(label(p))}</th>" for p in rounds[0]["rates"])
        parts.append(f"<h2>Rounds</h2><table><tr><th></th><th>Score</th>{heads}<th>Fixes applied</th><th>All pass</th></tr>{trs}</table>")
    elif reevaluation:
        trs = "".join(f"<tr><td>{e(label(r['persona']))}</td><td>{pct(r['success_before'])}</td>"
                      f"<td>{pct(r['success_after'])}</td><td>{r['steps_before'] or '–'} → {r['steps_after'] or '–'}</td></tr>"
                      for r in reevaluation["rows"])
        parts.append(f"<h2>Re-evaluated with the fixes: score {reevaluation['score_before']} → "
                     f"{reevaluation['score_after']}</h2><table><tr><th>Persona</th><th>Before</th><th>After</th>"
                     f"<th>Steps</th></tr>{trs}</table>")
    else:
        trs = "".join(f"<tr><td>{e(label(r['persona']))}</td><td>{pct(r.get('success_rate'))}</td>"
                      f"<td>{r.get('mean_steps') or '–'}</td></tr>" for r in rows)
        parts.append(f"<h2>Audit: score {score(rows)}</h2><table><tr><th>Persona</th><th>Success</th>"
                     f"<th>Mean steps</th></tr>{trs}</table>")
        if reevaluate_cmd:
            parts.append(f'<p class="note"><b>Evaluate again with these fixes</b> (not automatic: it re-runs every '
                         f'persona): <code>{e(reevaluate_cmd)}</code></p>')
    src = sug.get("source")
    if sug.get("devin_session_url"):
        src = f'<a href="{e(sug["devin_session_url"])}">Devin session</a>'
    parts.append(f"<h2>Suggested fixes ({len(fixes)}) <small>from {src or 'fixer'}</small></h2>")
    persona_cards = [c for (k, _), c in comps.items() if k == "persona"]
    if persona_cards:
        parts.append("<h3>Each persona, all fixes applied</h3><div class='grid'>" + "".join(
            f"<figure>{slider(c['before'], c['after'], c['title'], c.get('split', 50))}<figcaption>{e(c['title'])}</figcaption></figure>"
            for c in persona_cards) + "</div>")
    parts.append("<h3>Per criterion</h3>")
    for f in fixes:
        c = comps.get(("fix", f["id"]))
        img = slider(c["before"], c["after"], f["title"], c.get("split", 50)) if c else "<p class='muted'>No snapshot of this page.</p>"
        who = ", ".join(label(p) for p in f.get("personas", []))
        parts.append(f"""<section class="fix"><div class="meta"><span class="wcag">{e(f.get('wcag', ''))}</span>
<h4>{e(f['title'])}</h4><p class="muted">Blocked: {e(who)} · {e(f.get('page_url', ''))}</p>
<p>{e(f.get('description', ''))}</p><details><summary>Code change for developers</summary><p>{e(f.get('code_change', ''))}</p>
<pre>{e(f.get('css') or '')}{chr(10) if f.get('css') and f.get('js') else ''}{e(f.get('js') or '')}</pre></details></div>{img}</section>""")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0 auto;padding:24px 16px;max-width:1100px;background:#fbfaf6;color:#1f2137}}
table{{border-collapse:collapse;margin:8px 0 16px}}td,th{{border:1px solid #d6d3c8;padding:4px 10px;text-align:left}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}}figure{{margin:0}}figcaption{{font-size:13px;color:#555;margin-top:4px}}
.fix{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);gap:20px;padding:16px 0;border-top:1px solid #e3e0d6}}
@media (max-width:760px){{.fix{{grid-template-columns:1fr}}}}
.wcag{{display:inline-block;background:#e8e9fb;color:#3b3fb6;border-radius:4px;padding:1px 8px;font-size:12px;font-weight:600}}
h4{{margin:6px 0}}.muted{{color:#666;font-size:13px}}pre{{white-space:pre-wrap;background:#f1efe8;padding:8px;font-size:12px}}
.note{{background:#eef7ee;border:1px solid #bfe0bf;padding:8px 12px;border-radius:6px}}code{{font-size:12px}}
{SLIDER_CSS}</style></head><body>{''.join(parts)}</body></html>"""
    path = out_dir / ("summary.html" if rounds else "fixes.html")
    path.write_text(doc)
    return path


# ------------------------------------------------------------------------------------------ CLI
def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("suggest", help="fix patch + before/after images for an audit")
    s.add_argument("--audit", required=True)
    s.add_argument("--fixer", choices=["auto", "devin", "canned"], default="auto")
    r = sub.add_parser("reevaluate", help="re-run an audit with its suggested fixes applied")
    r.add_argument("--audit", required=True)
    r.add_argument("--patch", default=None)
    r.add_argument("--lanes", type=int, default=None)
    a = sub.add_parser("auto", help="audit -> fix -> re-audit until every persona passes")
    a.add_argument("--preset", required=True)
    a.add_argument("--personas", default="baseline,low_vision,color_blind,tremor,keyboard_only")
    a.add_argument("--tasks", default="")
    a.add_argument("--runs", type=int, default=1)
    a.add_argument("--lanes", type=int, default=3)
    a.add_argument("--max-rounds", type=int, default=3)
    a.add_argument("--pass-rate", type=float, default=1.0)
    a.add_argument("--fixer", choices=["auto", "devin", "canned"], default="auto")
    a.add_argument("--base-port", type=int, default=9400)
    args = ap.parse_args()
    if args.cmd == "suggest":
        suggest(args.audit, args.fixer)
    elif args.cmd == "reevaluate":
        reevaluate(args.audit, args.patch, args.lanes)
    else:
        auto(args.preset, [p for p in args.personas.split(",") if p], runs=args.runs, lanes=args.lanes,
             max_rounds=args.max_rounds, fixer_kind=args.fixer, pass_rate=args.pass_rate,
             tasks=[t for t in args.tasks.split(",") if t], base_port=args.base_port)


if __name__ == "__main__":
    main()

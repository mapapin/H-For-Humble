"""Run an accessibility / usability audit: every persona tries every task N times, in parallel.

Usage (from the repo root, demo shop running on :8765):
    .venv/bin/python poc/audit.py --preset poc/presets/demo_shop.yaml --personas baseline,low_vision --runs 1

Writes poc/runs/<timestamp>/{results.json, report.html, screenshots}.
"""

from __future__ import annotations

import argparse
import html
import json
import multiprocessing as mp
import os
import re
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from personas import PERSONAS  # noqa: E402


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def expand_env(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def _run_lane(lane: int, specs: list[dict]) -> list[dict]:
    """One lane = one process and one stable H browser channel; its runs go one after another on that channel."""
    sys.path.insert(0, str(HERE))
    load_dotenv(HERE.parent / ".env")
    import logging

    from runner import Lane, run_one

    # Every command the bridge receives (names only) + the SDK's bridge logs, to diagnose stalls.
    logging.basicConfig(filename=str(Path(specs[0]["out_dir"]) / f"lane-{lane}.log"), level=logging.INFO,
                        format="%(asctime)s %(threadName)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    lane_obj = Lane(lane, port=specs[0]["port"], reuse_channel=specs[0].get("reuse_channel", False))
    try:
        records = []
        for spec in specs:
            rec = run_one(spec, lane=lane_obj)
            if rec.get("stalled"):  # H stopped sending commands: retry once on a fresh channel
                print(f"[{rec['tag']}] stalled; retrying once on a fresh channel", flush=True)
                retry = run_one({**spec, "retry": 1}, lane=lane_obj)
                retry["retry_of"] = rec.get("session_id")
                rec = retry
            records.append(rec)
        return records
    finally:
        lane_obj.close()


def summarize(records: list[dict]) -> list[dict]:
    rows = []
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        groups.setdefault((r["task"], r["persona"]), []).append(r)
    for (task, persona), runs in groups.items():
        ok = [r for r in runs if r.get("status") != "harness_error"]
        steps = [r["steps"] for r in ok if isinstance(r.get("steps"), int)]
        acts = [len([a for a in r.get("actions", []) if not a.get("blocked")]) for r in ok]
        rows.append({
            "task": task,
            "persona": persona,
            "runs": len(runs),
            "harness_errors": len(runs) - len(ok),
            "success_rate": round(sum(bool(r.get("success")) for r in ok) / len(ok), 2) if ok else None,
            "mean_steps": round(statistics.mean(steps), 1) if steps else None,
            "mean_actions": round(statistics.mean(acts), 1) if acts else None,
            "mean_agent_time_s": round(statistics.mean(r.get("agent_duration_s") or r["duration_s"] for r in ok), 1) if ok else None,
            "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in runs), 4),
            "blocked_or_failed_actions": sum(
                1 for r in ok for a in r.get("actions", []) if a.get("blocked") or a.get("error")
            ),
        })
    base = {row["task"]: row for row in rows if row["persona"] == "baseline"}
    for row in rows:
        b = base.get(row["task"])
        if b and b["success_rate"] is not None and row["success_rate"] is not None:
            row["gap_vs_baseline"] = round(b["success_rate"] - row["success_rate"], 2)
        if b and b["mean_steps"] and row["mean_steps"]:
            row["effort_vs_baseline"] = round(row["mean_steps"] / b["mean_steps"], 2)
    return rows


def write_report(out_dir: Path, preset: dict, records: list[dict], rows: list[dict]) -> Path:
    e = html.escape

    def pct(v):
        return "" if v is None else f"{v:.0%}"

    table = "".join(
        f"<tr><td>{e(r['task'])}</td><td>{e(PERSONAS[r['persona']].label)}</td><td>{r['runs']}</td>"
        f"<td>{pct(r['success_rate'])}</td><td>{r.get('gap_vs_baseline', '')}</td><td>{r['mean_steps'] or ''}</td>"
        f"<td>{r.get('effort_vs_baseline', '')}</td><td>{r['blocked_or_failed_actions']}</td></tr>"
        for r in rows
    )
    cards = []
    for r in sorted(records, key=lambda r: (r["task"], r["persona"], r["run"])):
        rep = r.get("report") or {}
        obstacles = "".join(
            f"<li><b>{e(o.get('severity', ''))}</b> at {e(o.get('where', ''))}: {e(o.get('what', ''))}</li>"
            for o in rep.get("obstacles", [])
        )
        shot = f'<img src="{e(r["final_screenshot"])}" alt="final screen as this persona saw it">' if r.get("final_screenshot") else ""
        link = f'<a href="{e(r["agent_view_url"])}">Replay in H Agent View</a>' if r.get("agent_view_url") else ""
        cards.append(f"""<section class="card {'ok' if r.get('success') else 'ko'}">
<h3>{e(PERSONAS[r['persona']].label)} · {e(r['task'])} · run {r['run']} · {'SUCCESS' if r.get('success') else 'FAIL'}</h3>
<p>Status {e(str(r.get('status')))} · {r.get('steps')} steps · {r.get('duration_s')}s · {link}</p>
<p><b>Answer:</b> {e(str(rep.get('answer', '')))} &nbsp; <b>Summary:</b> {e(str(rep.get('summary', r.get('error', ''))))}</p>
<ul>{obstacles}</ul>{shot}</section>""")
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Audit report</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#111}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px}}.card{{border-left:6px solid #999;padding:4px 12px;margin:12px 0}}
.ok{{border-color:#2a7}}.ko{{border-color:#c33}}img{{max-width:640px;border:1px solid #ccc}}</style></head><body>
<h1>Audit: {e(preset['site'].get('name', preset['site']['start_url']))}</h1>
<table><tr><th>Task</th><th>Persona</th><th>Runs</th><th>Success</th><th>Gap vs baseline</th><th>Mean steps</th>
<th>Effort vs baseline</th><th>Blocked/failed actions</th></tr>{table}</table>{''.join(cards)}</body></html>"""
    path = out_dir / "report.html"
    path.write_text(doc)
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", required=True)
    ap.add_argument("--personas", default="baseline,low_vision,color_blind,tremor,keyboard_only")
    ap.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--lanes", type=int, default=3,
                    help="parallel lanes; each run needs 2 H session slots (its browser channel + the agent session), "
                         "and a used channel keeps its slot until H's cleanup. With 10 slots, 3 lanes.")
    ap.add_argument("--reuse-channel", action="store_true",
                    help="reuse one browser channel per lane (saves slots, but H sometimes stalls the next session)")
    ap.add_argument("--model", default="")
    ap.add_argument("--headed", action="store_true", help="show the Chrome windows")
    ap.add_argument("--base-port", type=int, default=9400)
    ap.add_argument("--login-mode", choices=["tool", "vault"], default="tool",
                    help="how the agent signs in: fill_credential custom tool, or H driver vault hook (fill_secret_at)")
    args = ap.parse_args()

    load_dotenv(HERE.parent / ".env")
    preset = expand_env(yaml.safe_load(Path(args.preset).read_text()))
    personas = [p.strip() for p in args.personas.split(",") if p.strip()]
    unknown = [p for p in personas if p not in PERSONAS]
    if unknown:
        sys.exit(f"unknown personas: {unknown}; known: {list(PERSONAS)}")
    tasks = preset["tasks"]
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = HERE / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = []
    for task in tasks:
        for persona in personas:
            for i in range(args.runs):
                specs.append({
                    "persona": persona, "task": task, "site": preset["site"], "run_index": i,
                    "port": args.base_port + len(specs), "out_dir": str(out_dir), "headless": not args.headed,
                    "model": args.model or None, "group_id": f"audit-{stamp}", "login_mode": args.login_mode, "reuse_channel": args.reuse_channel,
                })
    lanes = [specs[i::args.lanes] for i in range(args.lanes)]
    print(f"{len(specs)} runs on {args.lanes} lane(s), output {out_dir}", flush=True)
    records = []
    with ProcessPoolExecutor(max_workers=args.lanes, mp_context=mp.get_context("spawn")) as pool:
        futures = {pool.submit(_run_lane, i, lane): i for i, lane in enumerate(lanes) if lane}
        for fut in as_completed(futures):
            try:
                records.extend(fut.result())
            except Exception as exc:  # one broken lane must not lose the other lanes' results
                print(f"lane {futures[fut]} crashed: {type(exc).__name__}: {exc}", flush=True)
    rows = summarize(records)
    (out_dir / "results.json").write_text(json.dumps({"summary": rows, "runs": records}, indent=2, default=str))
    report = write_report(out_dir, preset, records, rows)
    print(json.dumps(rows, indent=2))
    print(f"report: {report}")


if __name__ == "__main__":
    main()

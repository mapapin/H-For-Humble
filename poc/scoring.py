"""Scores that read like a product metric, not just pass/fail.

Per run (0-100):
- a failed journey scores 0: the user did not get what they came for;
- a completed journey starts at 100 and loses points for the extra effort it took compared with the baseline user
  on the same journey (steps, time), for retried or blocked actions, and for every barrier the user reported.

Per perspective: the mean of its runs. Overall: the mean over every perspective tested (each perspective stands
for a group of real users). Grades: A >= 90, B >= 75, C >= 60, D >= 40, else F.
"""

from __future__ import annotations

import statistics
from typing import Any

SEVERITY_POINTS = {"blocking": 15, "major": 8, "minor": 3}
CLICKS = {"click_at", "click", "double_click", "mouse_press"}
KEYS = {"press_and_release_keys", "press_keys", "key_press", "hotkey"}
TYPING = {"write_at", "input_text", "type_text", "fill_secret_at"}
SCROLLS = {"scroll_at", "scroll", "scroll_by", "scroll_page"}


def grade(score: float | None) -> str | None:
    if score is None:
        return None
    return "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"


def valid(record: dict) -> bool:
    return record.get("status") != "harness_error"


def retries(record: dict) -> int:
    """Actions the agent had to repeat right away (same action on the same element): a mis-click, a key that did
    nothing, a field that did not take the text."""
    steps = (record.get("replay") or {}).get("steps") or []
    n = 0
    for prev, cur in zip(steps, steps[1:]):
        a, b = prev.get("action") or {}, cur.get("action") or {}
        if a.get("kind") in {"click", "type"} and a.get("label") and a.get("label") == b.get("label"):
            n += 1
    return n


def run_metrics(record: dict) -> dict[str, Any]:
    actions = [a for a in record.get("actions", []) if not str(a.get("cmd", "")).startswith("tool:")]
    obstacles = (record.get("report") or {}).get("obstacles") or []
    sev = {k: sum(1 for o in obstacles if o.get("severity") == k) for k in SEVERITY_POINTS}
    return {
        "steps": record.get("steps"),
        "time_s": record.get("agent_duration_s"),
        "clicks": sum(1 for a in actions if a["cmd"] in CLICKS and not a.get("blocked")),
        "keys": sum(1 for a in actions if a["cmd"] in KEYS),
        "typing": sum(1 for a in actions if a["cmd"] in TYPING),
        "scrolls": sum(1 for a in actions if a["cmd"] in SCROLLS),
        "blocked": sum(1 for a in actions if a.get("blocked")),
        "errors": sum(1 for a in actions if a.get("error") and not a.get("blocked")),
        "retries": retries(record),
        "barriers": sev,
        "cost_usd": record.get("cost_usd"),
    }


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return statistics.mean(values) if values else None


def baseline_reference(records: list[dict]) -> dict[str, dict[str, float | None]]:
    """Per task: mean steps and time of the baseline's successful runs."""
    out: dict[str, dict[str, float | None]] = {}
    for task in {r["task"] for r in records}:
        ok = [r for r in records if r["task"] == task and r["persona"] == "baseline" and valid(r) and r.get("success")]
        out[task] = {"steps": _mean(r.get("steps") for r in ok), "time_s": _mean(r.get("agent_duration_s") for r in ok)}
    return out


def run_score(record: dict, ref: dict[str, float | None]) -> dict[str, Any]:
    m = run_metrics(record)
    if not record.get("success"):
        return {"score": 0, "penalties": {"not_completed": 100}, "metrics": m}
    pen: dict[str, float] = {}
    if ref.get("steps") and m["steps"]:
        ratio = m["steps"] / ref["steps"]
        if ratio > 1.1:
            pen["extra_steps"] = min(30.0, 15 * (ratio - 1))  # twice the steps: -15, three times: -30
    if ref.get("time_s") and m["time_s"]:
        ratio = m["time_s"] / ref["time_s"]
        if ratio > 1.2:
            pen["extra_time"] = min(15.0, 7.5 * (ratio - 1))
    friction = m["retries"] * 2 + m["blocked"] * 2 + m["errors"]
    if friction:
        pen["friction"] = min(15.0, friction)
    barrier = sum(SEVERITY_POINTS[k] * n for k, n in m["barriers"].items())
    if barrier:
        pen["barriers"] = min(30.0, barrier)
    score = max(5.0, 100 - sum(pen.values()))  # a completed journey never scores below a failed one
    return {"score": round(score), "penalties": {k: round(v, 1) for k, v in pen.items()}, "metrics": m}


def scorecard(records: list[dict], personas: list[str] | None = None) -> dict[str, Any]:
    """Overall + per-perspective scores, with the numbers behind them."""
    records = [r for r in records if "persona" in r and "task" in r]
    ref = baseline_reference(records)
    per: dict[str, Any] = {}
    order = personas or list(dict.fromkeys(r["persona"] for r in records))
    for persona in order:
        runs = [r for r in records if r["persona"] == persona]
        ok = [r for r in runs if valid(r)]
        scored = [run_score(r, ref.get(r["task"], {})) for r in ok]
        metrics = [s["metrics"] for s in scored]
        succ = [r for r in ok if r.get("success")]
        steps = _mean(r.get("steps") for r in succ) or _mean(r.get("steps") for r in ok)
        time_s = _mean(r.get("agent_duration_s") for r in succ) or _mean(r.get("agent_duration_s") for r in ok)
        base_steps = _mean(v["steps"] for v in ref.values())
        base_time = _mean(v["time_s"] for v in ref.values())
        score = round(_mean(s["score"] for s in scored)) if scored else None
        per[persona] = {
            "score": score,
            "grade": grade(score),
            "runs": len(runs),
            "tested": len(ok),
            "errors": len(runs) - len(ok),
            "completed": len(succ),
            "completion": round(len(succ) / len(ok), 2) if ok else None,
            "steps": round(steps, 1) if steps else None,
            "time_s": round(time_s, 1) if time_s else None,
            "steps_vs_baseline": round(steps / base_steps, 2) if steps and base_steps and persona != "baseline" else None,
            "time_vs_baseline": round(time_s / base_time, 2) if time_s and base_time and persona != "baseline" else None,
            "clicks": sum(m["clicks"] for m in metrics),
            "keys": sum(m["keys"] for m in metrics),
            "blocked": sum(m["blocked"] for m in metrics),
            "retries": sum(m["retries"] for m in metrics),
            "barriers": sum(sum(m["barriers"].values()) for m in metrics),
            "blocking": sum(m["barriers"]["blocking"] for m in metrics),
            "penalties": _merge_penalties([s["penalties"] for s in scored]),
        }
    scores = [p["score"] for p in per.values() if p["score"] is not None]
    overall = round(statistics.mean(scores)) if scores else None
    impaired = [p for k, p in per.items() if k != "baseline" and p["completion"] is not None]
    return {
        "score": overall,
        "grade": grade(overall),
        "perspectives": per,
        "can_complete": sum(1 for p in per.values() if p["completion"]),
        "tested_perspectives": sum(1 for p in per.values() if p["completion"] is not None),
        "impaired_blocked": sum(1 for p in impaired if p["completion"] == 0),
        "baseline_ok": bool(per.get("baseline", {}).get("completion")),
    }


def _merge_penalties(items: list[dict[str, float]]) -> dict[str, float]:
    """Mean penalty per kind over the perspective's runs (what cost it points, on average)."""
    if not items:
        return {}
    keys = {k for p in items for k in p}
    return {k: round(sum(p.get(k, 0) for p in items) / len(items), 1) for k in sorted(keys)}

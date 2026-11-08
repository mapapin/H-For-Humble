"""LLM judge (Holo, H Models API): did the user really reach the goal, and what does the audit say?

Two jobs where fixed rules were brittle:
- run verdict: the agent claims it finished; the judge reads its final answer, its last thoughts and the run stats
  and decides whether the goal was reached and the expected facts are there, whatever their format
  ("€98" for "98.00", "Tuesday 8th of December" for "8 Dec"), and catches a claimed success that is not one
  (another date booked, checkout never reached);
- headline: one sentence at the top of the report, grounded in the scorecard and the barriers the agents reported.

The fixed rules stay as the fallback (no key, HUMBLE_JUDGE=off, API error, unparsable answer), and both verdicts
are kept on the run so a disagreement is visible.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

API = os.environ.get("HUMBLE_JUDGE_URL", "https://api.hcompany.ai/v1/chat/completions")
MODEL = os.environ.get("HUMBLE_JUDGE_MODEL", "holo4-27b")
FALLBACK_MODELS = ["holo4-35b-a3b"]


def available() -> bool:
    return bool(os.environ.get("HAI_API_KEY")) and os.environ.get("HUMBLE_JUDGE", "on").lower() not in {"off", "0", "false"}


def _json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def ask(system: str, user: str, max_tokens: int = 700, timeout_s: float = 30, attempts: int = 3) -> dict | None:
    """One JSON answer from Holo, or None. Thinking off: ~1 s, and the answer is in `content`.
    Rate limited (30 requests/min per model on tier 1): the next attempt uses the other model."""
    last = None
    models = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    for attempt in range(attempts):
        model = models[min(attempt, len(models) - 1)] if last and "429" in last else MODEL
        try:
            r = httpx.post(API, timeout=timeout_s, headers={"Authorization": f"Bearer {os.environ['HAI_API_KEY']}"}, json={
                "model": model, "temperature": 0, "max_tokens": max_tokens,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
            msg = (r.json().get("choices") or [{}])[0].get("message") or {}
            out = _json(msg.get("content") or "")
            if out is not None:
                out["_model"] = model
                return out
            last = "unparsable answer"
        except Exception as exc:  # network, quota, 5xx: retry, then the caller falls back to rules
            last = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if attempt + 1 < attempts:
            time.sleep(4 if "429" in last else 1.5)
    print(f"[judge] unavailable ({last})", flush=True)
    return None


RUN_SYSTEM = """You check the result of a usability test. A browser agent played a user and tried to complete a
journey on a website. Decide whether the journey was really completed.

Rules:
- success is true only if the agent reached the goal described in the journey (the end state it names) AND, when
  expected facts are given, its final answer states every one of them.
- Expected facts: facts separated by ";" must ALL be present; alternatives inside a fact are separated by "|".
  Accept any wording or format of the same fact: "€98", "98 EUR", "98.00 €" all equal "98.00"; "Tuesday 8th of
  December", "Dec 8", "08/12" all equal "8 Dec". A different value is NOT the same fact: "18 Dec" is not "8 Dec",
  "€196" is not "98.00".
- The agent's own "completed" flag is a claim, not proof. Use its answer, summary, last thoughts and stats.
  If it says it chose another option than asked, stopped early, or only guessed, success is false.
- Obstacles do not make a completed journey fail; they only matter if they stopped the user.
Reply with JSON only: {"success": true|false, "reason": "<one short sentence, under 25 words>"}"""


def _thoughts(record: dict, n: int = 5) -> list[str]:
    steps = (record.get("replay") or {}).get("steps") or []
    out = []
    for s in steps[-n:]:
        action = (s.get("action") or {}).get("label")
        out.append(" — ".join(x for x in (s.get("thought"), action) if x))
    return out


def judge_run(record: dict, task: dict, persona_label: str = "") -> dict | None:
    """{success, reason, model} from Holo, or None when the judge is unavailable."""
    rep = record.get("report") or {}
    m = record.get("metrics") or {}
    facts = {
        "journey": {"title": task.get("title"), "goal": task.get("goal")},
        "expected_facts": task.get("expect_answer_contains") or None,
        "user": persona_label or record.get("persona"),
        "agent_report": {"completed": rep.get("completed"), "answer": rep.get("answer"), "summary": rep.get("summary"),
                         "obstacles": [f"{o.get('severity')}: {o.get('what')}" for o in rep.get("obstacles") or []][:6]},
        "last_steps": _thoughts(record),
        "stats": {"status": record.get("status"), "steps": record.get("steps"), "blocked_actions": m.get("blocked"),
                  "seconds": record.get("agent_duration_s")},
    }
    out = ask(RUN_SYSTEM, json.dumps(facts, ensure_ascii=False, indent=1), max_tokens=200)
    if not out or not isinstance(out.get("success"), bool):
        return None
    return {"success": out["success"], "reason": str(out.get("reason") or "")[:300], "model": out["_model"]}


def decide(record: dict, task: dict, rule_success: bool, persona_label: str = "") -> None:
    """Sets record['success'] (+ record['judge']): the judge's verdict when the agent claims success, else the rule.
    A run the agent itself reports as not completed, or a technical error, is never turned into a success."""
    rep = record.get("report") or {}
    record["rule_success"] = rule_success
    record["success"] = rule_success
    if record.get("status") == "harness_error" or not rep.get("completed") or not available():
        return
    verdict = judge_run(record, task, persona_label)
    if verdict is None:
        return
    record["judge"] = verdict
    record["success"] = verdict["success"]


HEADLINE_SYSTEM = """You write the headline of an accessibility audit report. Browser agents tried the same journey
as different users (baseline, low vision, colour blind, hand tremor, keyboard only). You get the measured results.

Write:
- "headline": ONE sentence, at most 22 words, plain words. Say who could not finish or struggled most, and the
  concrete cause the agents reported (e.g. "date buttons cannot be reached with the keyboard"). If everyone
  finished easily, say so. No marketing, no exclamation marks, no score letter.
- "next_step": ONE short sentence, at most 18 words: the single fix that would help the most people, from the
  barriers reported. Empty string if nothing needs fixing.
A user with tested_runs 0 was not measured (technical error): never say they failed or did not finish; say
they could not be measured if it matters. Use only facts given below. Do not invent numbers: any number you write
must appear in the data.
Reply with JSON only: {"headline": "...", "next_step": "..."}"""

LABELS = {"baseline": "Baseline user", "low_vision": "Low vision", "color_blind": "Colour blind",
          "tremor": "Hand tremor", "keyboard_only": "Keyboard only"}


def _numbers(text: str) -> set[str]:
    return {n.rstrip(".,").replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text)}


def headline(card: dict, records: list[dict], tasks: list[dict], parent_score: int | None = None) -> dict | None:
    """{headline, next_step, model} grounded in the scorecard, or None (the UI then writes it from rules)."""
    if not available() or card.get("score") is None:
        return None
    per = card.get("perspectives") or {}
    users = []
    for pid, p in per.items():
        runs = [r for r in records if r.get("persona") == pid and r.get("status") != "harness_error"]
        obstacles = [o.get("what") for r in runs for o in ((r.get("report") or {}).get("obstacles") or [])
                     if o.get("severity") in {"blocking", "major"}][:4]
        reasons = [r["judge"]["reason"] for r in runs if not r.get("success") and (r.get("judge") or {}).get("reason")]
        users.append({"user": LABELS.get(pid, pid), "finished_runs": p.get("completed"), "tested_runs": p.get("tested"),
                      "score": p.get("score"), "steps_vs_baseline": p.get("steps_vs_baseline"),
                      "barriers_reported": obstacles, "why_not_finished": reasons[:2] or None})
    facts = {"journeys": [t.get("title") for t in tasks], "overall_score": card.get("score"),
             "users_who_finished": card.get("can_complete"), "users_tested": card.get("tested_perspectives"),
             "score_before_fixes": parent_score, "users": users}
    data = json.dumps(facts, ensure_ascii=False, indent=1)
    allowed = _numbers(data) | {"1", "2", "3", "4", "5"}  # "two of five" style counts are fine
    for _ in range(2):
        out = ask(HEADLINE_SYSTEM, data, max_tokens=250)
        if not out or not isinstance(out.get("headline"), str) or not out["headline"].strip():
            return None
        text = f"{out['headline']} {out.get('next_step') or ''}"
        if _numbers(text) <= allowed:  # grounded: no number that is not in the results
            return {"headline": out["headline"].strip()[:240], "next_step": str(out.get("next_step") or "").strip()[:200],
                    "model": out["_model"], "created": time.time()}
        print(f"[judge] headline rejected (invented number): {text}", flush=True)
    return None

"""Re-judge saved runs with the Holo judge and compare with the fixed rules; optionally write verdicts + headline.

Usage: .venv/bin/python scripts/rejudge.py [--write] [--headline-only] [--user=<id prefix>] [audit-id ...]
(no id: every audit)
Without --write it only prints the runs where the judge and the rule disagree, and the agreement rate.
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poc"))
from audit import load_dotenv, summarize  # noqa: E402

load_dotenv(ROOT / ".env")
from app import store  # noqa: E402
from judge import LABELS, headline, judge_run  # noqa: E402
from runner import answer_matches  # noqa: E402
from scoring import run_metrics, scorecard  # noqa: E402


def rule(record: dict, task: dict) -> bool:
    rep = record.get("report") or {}
    expected = task.get("expect_answer_contains")
    return bool(rep.get("completed")) and (answer_matches(str(rep.get("answer", "")), expected) if expected else True)


def main(args: list[str]) -> None:
    write, headline_only = "--write" in args, "--headline-only" in args
    user = next((a.split("=", 1)[1] for a in args if a.startswith("--user=")), None)
    ids = [a for a in args if not a.startswith("--")]
    if user:  # every audit of one account (id prefix)
        with store.connect() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM audits WHERE user_id LIKE ? AND results!='[]'", (user + "%",))]
    with store.connect() as db:
        q = "SELECT * FROM audits WHERE results!='[]'" + (f" AND id IN ({','.join('?' * len(ids))})" if ids else "")
        audits = [store.serialize(r) for r in db.execute(q + " ORDER BY created", ids).fetchall()]
    total = agree = 0
    for audit in audits:
        tasks = {t["id"]: t for t in audit["config"]["tasks"]}
        records = [r for r in audit["results"] if "persona" in r and "task" in r]
        todo = [r for r in records if r.get("status") != "harness_error" and (r.get("report") or {}).get("completed")]
        if not headline_only:
            for r in records:
                r.setdefault("metrics", run_metrics(r))
            with ThreadPoolExecutor(max_workers=2) as pool:
                verdicts = list(pool.map(lambda r: judge_run(r, tasks.get(r["task"], {}), LABELS.get(r["persona"], "")), todo))
            for r, v in zip(todo, verdicts):
                ruled = rule(r, tasks.get(r["task"], {}))
                if v is None:
                    print(f"  ? {audit['name'][:50]} {r['persona']}: judge unavailable")
                    continue
                total += 1
                agree += v["success"] == ruled
                if v["success"] != ruled:
                    print(f"  ≠ {audit['name'][:60]} · {r['persona']}: rule={ruled} judge={v['success']} — {v['reason']}\n"
                          f"      expected={tasks.get(r['task'], {}).get('expect_answer_contains')!r} "
                          f"answer={(r.get('report') or {}).get('answer', '')[:160]!r}")
                if write:
                    r["rule_success"], r["judge"], r["success"] = ruled, v, v["success"]
        if write:
            card = scorecard(records, audit["config"].get("personas"))
            parent_score = None
            if audit.get("parent_id"):
                with store.connect() as db:
                    row = db.execute("SELECT scores FROM audits WHERE id=?", (audit["parent_id"],)).fetchone()
                parent_score = (json.loads(row["scores"] or "{}") if row else {}).get("score")
            insight = headline(card, records, list(tasks.values()), parent_score) or {}
            store.update(audit["id"], results=records, summary=summarize(records), scores=card, insight=insight)
            old = (audit.get("scores") or {}).get("score")
            print(f"{audit['id'][:8]} {audit['name'][:60]}: {old} -> {card['score']} · {insight.get('headline')}")
    if total:
        print(f"\njudge agrees with the rule on {agree}/{total} claimed-complete runs ({agree / total:.0%})")


if __name__ == "__main__":
    main(sys.argv[1:])

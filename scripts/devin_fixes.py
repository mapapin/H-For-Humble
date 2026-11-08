"""Ask Devin for the fixes of a finished audit, render their before/after images, and make them the audit's fixes.

Devin (API v3, structured output) gets the journey, what each struggling perspective reported, and the rendered
HTML of the pages they saw; it returns one fix per barrier (preview CSS/JS + the real code change). The audit's
previous fixes are kept in fixes/previous-fixes.json. Then "Evaluate again with fixes" in the app re-runs every
agent with Devin's fixes injected.

Usage: .venv/bin/python scripts/devin_fixes.py <audit-id> [render-port] [--from <re-evaluation-id>]
  --from: round 2. Devin gets what still fails in that re-evaluation (pages with the first fixes applied) and the
  list of fixes already applied; the new fixes are merged into the audit's patch.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poc"))
from audit import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
from app import store  # noqa: E402
from autofix import needs_fix  # noqa: E402
from fixes import DevinFixer  # noqa: E402
from render import build_comparisons  # noqa: E402


def load(audit_id: str) -> dict:
    with store.connect() as db:
        return store.serialize(db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone())


def main(audit_id: str, port: str = "9585", from_audit: str | None = None) -> None:
    """from_audit: a re-evaluation of audit_id; Devin then fixes what still fails there, knowing its first patch."""
    audit = load(audit_id)
    base = store.DATA / "audits" / audit_id
    goals = {t["id"]: t["goal"] for t in audit["config"]["tasks"]}
    source = load(from_audit) if from_audit else audit
    source_base = store.DATA / "audits" / source["id"]
    records = [{**r, "goal": goals.get(r.get("task"))} for r in source["results"]]
    previous = (audit.get("fixes") or {}).get("patch") if from_audit else None
    site = {"start_url": audit["url"], "auth": {}}
    personas = needs_fix(records, source["summary"])
    print(f"{audit['name']}: asking Devin to fix {', '.join(personas)}"
          + (f" (round 2, {len(previous['fixes'])} fixes already applied)" if previous else ""), flush=True)
    started = time.time()
    fixer = DevinFixer(max_acu=10, timeout_s=1800)
    new = fixer.generate(records, source["summary"], source_base, site, previous, personas=personas)
    print(f"Devin: {len(new['fixes'])} fixes in {time.time() - started:.0f}s · {new.get('devin_session_url')}", flush=True)
    if previous:
        from fixes import merge_patches
        patch = merge_patches(previous, new)
        patch["summary"] = (previous.get("summary", "") + " " + new.get("summary", "")).strip()
        records = [{**r, "goal": goals.get(r.get("task"))} for r in audit["results"]]  # images on the original pages
    else:
        patch = {"summary": new.get("summary", ""), "fixes": new["fixes"]}
    fix_dir = base / "fixes"
    fix_dir.mkdir(exist_ok=True)
    if audit.get("fixes"):
        (fix_dir / "previous-fixes.json").write_text(json.dumps(audit["fixes"], indent=2))
    (fix_dir / "devin-patch.json").write_text(json.dumps(patch, indent=2))
    comparisons = build_comparisons(records, base, patch, fix_dir, port=int(port))
    old = (audit.get("fixes") or {}) if previous else {}
    sessions = list(dict.fromkeys(filter(None, [*old.get("devin_sessions", []), old.get("devin_session_url"),
                                                new.get("devin_session_url")])))
    store.update(audit_id, fixes={"source": "devin", "devin_session_url": new.get("devin_session_url"),
                                  "devin_sessions": sessions,
                                  "acus_consumed": new.get("acus_consumed"), "patch": patch,
                                  "comparisons": comparisons, "new_fix_ids": [f["id"] for f in new["fixes"]],
                                  "created": time.time()})
    store.event(audit_id, "progress", f"Devin suggested {len(new['fixes'])} fixes, with before/after images.")
    for f in new["fixes"]:
        print(f"- {f['id']} ({f['wcag']}) for {', '.join(f['personas'])} on {f['page_url']}: {f['title']}")
    print("comparisons:", [(c["kind"], c["id"], bool(c.get("before")), c.get("error")) for c in comparisons])


if __name__ == "__main__":
    args = sys.argv[1:]
    src = None
    if "--from" in args:
        i = args.index("--from")
        src = args[i + 1]
        args = args[:i] + args[i + 2:]
    main(*args, from_audit=src)

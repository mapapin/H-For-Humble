"""Re-render the before/after images of audits that already have fixes (no new agent run, no Devin call).

Usage: .venv/bin/python scripts/rerender_previews.py <audit-id-prefix>... | --user=<user-id-prefix> [--skip=<id>,<id>]
Fix loops are skipped: they keep only their last round's pages. A comparison that fails keeps its previous image.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poc"))
from app import store  # noqa: E402
from render import build_comparisons  # noqa: E402


def main(args: list[str]) -> None:
    user = next((a.split("=", 1)[1] for a in args if a.startswith("--user=")), None)
    ids = [a for a in args if not a.startswith("--")]
    skip = [i for a in args if a.startswith("--skip=") for i in a.split("=", 1)[1].split(",") if i]
    with store.connect() as db:
        rows = [store.serialize(r) for r in db.execute("SELECT * FROM audits ORDER BY created DESC")]
    audits = [a for a in rows if (a.get("fixes") or {}).get("patch", {}).get("fixes")
              and (any(a["id"].startswith(i) for i in ids) or (user and a["user_id"].startswith(user)))]
    audits = [a for a in audits if not any(a["id"].startswith(i) for i in skip)]
    for n, audit in enumerate(audits):
        base = store.DATA / "audits" / audit["id"]
        if not base.exists():
            continue
        if (audit.get("config") or {}).get("mode") == "auto":
            print(f"{audit['id'][:8]} {audit['name']}: skipped (a fix loop keeps only its last round's pages)")
            continue
        records = audit["results"]
        started = time.time()
        try:
            comparisons = build_comparisons(records, base, audit["fixes"]["patch"], base / "fixes", port=9650 + n % 20)
        except Exception as exc:
            print(f"{audit['id'][:8]} {audit['name']}: failed ({exc})", flush=True)
            continue
        previous = {(c["kind"], c["id"]): c for c in audit["fixes"].get("comparisons", [])}
        comparisons = [c if c.get("before") or not previous.get((c["kind"], c["id"]), {}).get("before")
                       else previous[(c["kind"], c["id"])] for c in comparisons]  # never lose a working image
        fixes = {**audit["fixes"], "comparisons": comparisons}
        store.update(audit["id"], fixes=fixes)
        hidden = [c["id"] for c in comparisons if c.get("visible") is False]
        print(f"{audit['id'][:8]} {audit['name']}: {len(comparisons)} images in {time.time() - started:.0f}s"
              + (f", no visible change: {', '.join(hidden)}" if hidden else ""), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])

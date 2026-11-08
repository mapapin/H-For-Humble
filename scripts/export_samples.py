"""Export finished audits as self-contained HTML reports into docs/ (sample reports judges can open offline).

Usage: .venv/bin/python scripts/export_samples.py <audit-id>=<file-name> [...]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poc"))

from app import store  # noqa: E402
from export_report import build  # noqa: E402
from scoring import scorecard  # noqa: E402


def main(pairs: list[str]) -> None:
    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)
    for pair in pairs:
        audit_id, _, name = pair.partition("=")
        with store.connect() as db:
            row = db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone()
        if not row:
            raise SystemExit(f"unknown audit {audit_id}")
        audit = store.serialize(row)
        if not audit.get("scores") and audit.get("results"):
            audit["scores"] = scorecard(audit["results"], audit["config"].get("personas"))
        with store.connect() as db:
            child = db.execute("SELECT scores FROM audits WHERE parent_id=? AND status='completed' ORDER BY created DESC "
                               "LIMIT 1", (audit_id,)).fetchone()
        after = json.loads(child["scores"]) if child and child["scores"] else None
        page = build(audit, store.DATA / "audits" / audit_id, after=after)
        target = out_dir / (name or f"report-{audit_id[:8]}.html")
        target.write_text(page)
        print(f"{target.relative_to(ROOT)}: {len(page) // 1024} KB · score {audit['scores'].get('score')}")


if __name__ == "__main__":
    main(sys.argv[1:])

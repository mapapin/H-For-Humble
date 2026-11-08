"""Copy a finished audit with another fix patch, to re-test that patch with "Evaluate again with fixes"
(used to compare Devin modes on the same findings). Prints the new audit id.

Usage: .venv/bin/python scripts/clone_with_patch.py <audit-id> <patch.json> "<name suffix>"
"""
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import store  # noqa: E402


def main(audit_id: str, patch_path: str, suffix: str) -> None:
    patch = json.loads(Path(patch_path).read_text())
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone())
        new_id = str(uuid.uuid4())
        row.update(id=new_id, name=f"{row['name']} · {suffix}"[:100], created=time.time(), parent_id=None,
                   fixes=json.dumps({"source": "devin", "patch": patch, "comparisons": [], "created": time.time()}))
        cols = ",".join(row)
        db.execute(f"INSERT INTO audits({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
    print(new_id)


if __name__ == "__main__":
    main(*sys.argv[1:])

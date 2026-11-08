"""Compare Devin modes on the same fix task: wall time, ACUs and fixes per mode (patches saved for re-testing).

Usage: .venv/bin/python scripts/devin_compare.py <audit-id> <out-dir> fast ultra fusion ...
"""
import json
import sys
import threading
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


def main(audit_id: str, out: str, *modes: str) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with store.connect() as db:
        audit = store.serialize(db.execute("SELECT * FROM audits WHERE id=?", (audit_id,)).fetchone())
    goals = {t["id"]: t["goal"] for t in audit["config"]["tasks"]}
    records = [{**r, "goal": goals.get(r.get("task"))} for r in audit["results"]]
    personas = needs_fix(records, audit["summary"])
    site = {"start_url": audit["url"], "auth": {}}
    results = {}

    def run(mode: str) -> None:
        t = time.time()
        try:
            got = DevinFixer(max_acu=15, timeout_s=1500, mode=mode).generate(
                records, audit["summary"], store.DATA / "audits" / audit_id, site, None, personas=personas)
            results[mode] = {"seconds": round(time.time() - t), "fixes": len(got["fixes"]),
                             "acus": got.get("acus_consumed"), "url": got.get("devin_session_url"),
                             "ids": [f["id"] for f in got["fixes"]]}
            (out_dir / f"{mode}.json").write_text(json.dumps({"summary": got.get("summary", ""), "fixes": got["fixes"]}, indent=2))
        except Exception as exc:
            results[mode] = {"seconds": round(time.time() - t), "error": f"{type(exc).__name__}: {exc}"[:200]}
        print(f"RESULT {mode}: {json.dumps(results[mode])}", flush=True)

    threads = [threading.Thread(target=run, args=(m,)) for m in modes]
    for th in threads:
        th.start()
        time.sleep(2)
    for th in threads:
        th.join()
    (out_dir / "summary.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:])

"""Pre-demo checklist: everything a live Humble demo needs, in 10 seconds. No H session is started.

Usage: .venv/bin/python scripts/preflight.py
"""
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poc"))
from audit import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
OK, BAD = "\033[32m✔\033[0m", "\033[31m✘\033[0m"
problems = 0


def check(label: str, ok: bool, detail: str = "", fix: str = "") -> None:
    global problems
    problems += not ok
    print(f"{OK if ok else BAD} {label}" + (f": {detail}" if detail else "") + ("" if ok or not fix else f"\n    → {fix}"))


def up(url: str, headers: dict | None = None) -> bool:
    try:
        return httpx.get(url, timeout=5, headers=headers or {}, trust_env=False).status_code < 500
    except Exception:
        return False


check("Humble app", up("http://127.0.0.1:8000/api/health"), "http://127.0.0.1:8000", "npm run build && npm start")
check("Lune demo site", up("http://127.0.0.1:8770/"), "http://127.0.0.1:8770",
      ".venv/bin/python poc/demo_startup/server.py --port 8770")
check("Mugs & Co demo shop", up("http://127.0.0.1:8765/login", {"X-Audit-Token": os.environ.get("DEMO_TOKEN", "")}),
      "http://127.0.0.1:8765 (optional)", ".venv/bin/python poc/demo_site/server.py --port 8765")
try:
    from chrome import chrome_binary
    check("Chrome", True, chrome_binary())
except Exception as exc:
    check("Chrome", False, str(exc), "set AUDIT_CHROME in .env")
check("H API key", bool(os.environ.get("HAI_API_KEY")), "", "set HAI_API_KEY in .env")
if os.environ.get("HAI_API_KEY"):
    try:
        from hai_agents import Client
        with Client() as c:
            q = c.sessions.get_session_quota()
            check("H slots", q.available >= 2, f"{q.available} of {q.limit} free → "
                  f"{min(5, q.available // 2)} agents can start at once",
                  "wait for H to release slots (finished browser channels are kept ~25-30 min)")
            stuck = [s for s in c.sessions.list_sessions(size=50).items if str(s.status) == "running"]
            check("No stuck H sessions", not stuck, f"{len(stuck)} running" if stuck else "",
                  "stop them in H Agent View: " + ", ".join(f"https://platform.hcompany.ai/agents/sessions/{s.id}" for s in stuck))
    except Exception as exc:
        check("H API", False, type(exc).__name__, "check the network and HAI_API_KEY")
check("Linkup key", bool(os.environ.get("LINKUP_API_KEY")), "journey suggestions + declared accessibility",
      "set LINKUP_API_KEY in .env (optional)")
check("axe-core", (ROOT / "node_modules" / "axe-core" / "axe.min.js").exists(), "static comparison", "npm install")
check("Frontend build", (ROOT / "dist" / "index.html").exists(), "", "npm run build")
print("\nReady for a live demo." if not problems else f"\n{problems} item(s) to fix before a live demo.")
sys.exit(1 if problems else 0)

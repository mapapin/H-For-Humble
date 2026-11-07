"""Static accessibility scan (axe-core) of every page the agents visited, to compare with what they lived.

A static checker lists every rule violation it can detect, often dozens per site, with no notion of which ones
actually stop someone. Humble's agents show which journeys break, for whom. Putting both side by side ranks the
checker's findings: violations related to a perspective that was blocked or struggled come first.

We load each page snapshot captured during the audit (rendered DOM, scripts stripped, <base> to the real site)
in a headless Chrome, inject axe-core (node_modules/axe-core, MPL-2.0) and keep the violations.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from chrome import ChromeProcess, DevToolsController
from personas import PERSONAS
from render import prepare_snapshot

ROOT = Path(__file__).resolve().parents[1]
AXE = ROOT / "node_modules" / "axe-core" / "axe.min.js"

# Which perspective an axe rule mostly affects (for ranking against what the agents lived).
RULE_PERSONAS = {
    "color-contrast": ["low_vision"], "color-contrast-enhanced": ["low_vision"], "meta-viewport": ["low_vision"],
    "meta-viewport-large": ["low_vision"], "avoid-inline-spacing": ["low_vision"],
    "link-in-text-block": ["color_blind"],
    "target-size": ["tremor"],
    "scrollable-region-focusable": ["keyboard_only"], "nested-interactive": ["keyboard_only"],
    "focus-order-semantics": ["keyboard_only"], "tabindex": ["keyboard_only"], "accesskeys": ["keyboard_only"],
    "frame-focusable-content": ["keyboard_only"], "bypass": ["keyboard_only"], "skip-link": ["keyboard_only"],
    "button-name": ["keyboard_only"], "link-name": ["keyboard_only"], "aria-command-name": ["keyboard_only"],
    "label": ["keyboard_only", "low_vision"], "select-name": ["keyboard_only"], "image-alt": ["low_vision"],
}
IMPACT = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}


def _path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


def scan_snapshots(records: list[dict], base_dir: str | Path, port: int = 9470,
                   headers: dict[str, str] | None = None, max_pages: int = 8) -> dict:
    """axe-core on one snapshot per distinct page (latest one), then rank violations by the perspectives that had
    trouble. Returns {pages, violations, totals}."""
    if not AXE.exists():
        return {"error": "axe-core is not installed (npm install)"}
    base_dir = Path(base_dir)
    pages: dict[str, dict] = {}
    for r in records:
        for snap in r.get("snapshots", []):
            pages[_path(snap["url"])] = snap  # latest snapshot of each page
    pages = dict(list(pages.items())[:max_pages])
    troubled = {r["persona"] for r in records if not r.get("success") and r.get("status") != "harness_error"}
    struggled = {r["persona"] for r in records if (r.get("report") or {}).get("obstacles")}
    axe_src = AXE.read_text()
    chrome = ChromeProcess(port, PERSONAS["baseline"], headless=True)
    tmp = Path(tempfile.mkdtemp(prefix="humble-axe-"))
    violations: dict[str, dict] = {}
    scanned = []
    try:
        dt = DevToolsController(port, PERSONAS["baseline"], headers=headers)
        try:
            for i, (path, snap) in enumerate(pages.items()):
                try:
                    html = (base_dir / snap["file"]).read_text()
                    page = tmp / f"page-{i}.html"
                    page.write_text(prepare_snapshot(html, snap["url"], []))
                    dt.navigate(page.as_uri())
                    dt.wait_loaded()
                    dt.evaluate(axe_src)
                    res = dt.call("Runtime.evaluate", {
                        "expression": "axe.run(document, {resultTypes: ['violations']}).then(r => JSON.stringify("
                                      "r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help, helpUrl: v.helpUrl,"
                                      " tags: v.tags.filter(t => /^wcag\\d|^best/.test(t)), nodes: v.nodes.length,"
                                      " sample: (v.nodes[0] && v.nodes[0].target || []).join(' ')}))))",
                        "awaitPromise": True, "returnByValue": True}, dt.page_sessions[-1], timeout=60)
                    found = json.loads(res.get("result", {}).get("value") or "[]")
                except Exception:
                    continue
                scanned.append({"url": snap["url"], "violations": len(found)})
                for v in found:
                    entry = violations.setdefault(v["id"], {**v, "nodes": 0, "pages": []})
                    entry["nodes"] += v["nodes"]
                    entry["pages"].append(snap["url"])
        finally:
            dt.close()
    finally:
        chrome.close()
    ranked = []
    for v in violations.values():
        personas = RULE_PERSONAS.get(v["id"], [])
        v["personas"] = personas
        v["blocked_someone"] = bool(set(personas) & troubled)
        v["related_to_barrier"] = bool(set(personas) & (troubled | struggled))
        v["wcag"] = [t for t in v["tags"] if t.startswith("wcag") and any(c.isdigit() for c in t[4:5])]
        ranked.append(v)
    ranked.sort(key=lambda v: (not v["blocked_someone"], not v["related_to_barrier"], IMPACT.get(v["impact"] or "", 4), -v["nodes"]))
    return {"engine": "axe-core 4", "pages": scanned, "violations": ranked,
            "totals": {"rules": len(ranked), "nodes": sum(v["nodes"] for v in ranked),
                       "related": sum(1 for v in ranked if v["related_to_barrier"]),
                       "blocking": sum(1 for v in ranked if v["blocked_someone"])}}

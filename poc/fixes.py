"""Fix suggestions: from audit findings to a *patch* we can preview and re-test.

A patch is a list of fixes. Each fix targets one problem (a WCAG criterion, the personas it blocked, the page) and
carries two things:
- a **preview**: CSS and/or JS injected into the live page by our Chrome, so we can show before/after images and
  re-run the same personas on the fixed page without touching the customer's site;
- the **real fix**: `code_change`, what a developer (or Devin, in a PR) should change in the source code.

Fixers:
- DevinFixer: Devin API v3. Devin gets the findings and the rendered HTML of the pages where users got stuck, and
  returns the fixes as structured output. Needs DEVIN_API_KEY (service user key, cog_...) and DEVIN_ORG_ID.
- CannedFixer: hand-written fixes from a JSON file, returned for the personas that failed. Stand-in for Devin
  until we have a key, and a no-wait fallback for live demos.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Two or three sentences on what was fixed and why"},
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "short kebab-case id, unique in this patch"},
                    "title": {"type": "string"},
                    "wcag": {"type": "string", "description": "WCAG 2.2 criterion, e.g. '1.4.1 Use of Color'"},
                    "personas": {"type": "array", "items": {"type": "string"}},
                    "page_url": {"type": "string", "description": "URL of the page this fix applies to"},
                    "description": {"type": "string", "description": "The problem and the fix, for a product team"},
                    "css": {"type": "string", "description": "CSS injected in the page (may be empty)"},
                    "js": {"type": "string",
                           "description": "JS function body run after DOMContentLoaded; idempotent, no dependencies"},
                    "code_change": {"type": "string", "description": "The proper change to make in the source code"},
                },
                "required": ["id", "title", "wcag", "personas", "page_url", "description", "css", "js", "code_change"],
            },
        },
    },
    "required": ["summary", "fixes"],
}


# --------------------------------------------------------------------------------------------- patch -> JS
def patch_script(fixes: list[dict]) -> str:
    """One self-contained script applying the fixes on every page: CSS as a <style>, JS bodies after the DOM is
    ready, each guarded so one broken fix does not stop the others. Fixes only run on their own page path."""
    items = [
        {"id": f["id"], "path": "" if f.get("scope") == "site" else urlparse(f.get("page_url") or "").path or "",
         "css": f.get("css") or "",
         "js": f.get("js") or ""}
        for f in fixes
    ]
    funcs = ",\n".join(
        f"{{id: {json.dumps(i['id'])}, path: {json.dumps(i['path'])}, css: {json.dumps(i['css'])}, "
        f"run: function () {{\n{i['js']}\n}}}}"
        for i in items
    )
    return f"""(() => {{
  if (window.__auditPatchApplied) return;
  window.__auditPatchApplied = true;
  const fixes = [
{funcs}
  ];
  // __auditPagePath: set when re-rendering a saved snapshot, whose own location is a local file
  const here = (window.__auditPagePath || location.pathname).replace(/\\/$/, '') || '/';
  const onPage = f => !f.path || f.path === '/' || here === (f.path.replace(/\\/$/, '') || '/');
  const apply = () => {{
    for (const f of fixes) {{
      if (!onPage(f)) continue;
      if (f.css && !document.querySelector('style[data-audit-fix="' + f.id + '"]')) {{
        const s = document.createElement('style');
        s.dataset.auditFix = f.id;
        s.textContent = f.css;
        (document.head || document.documentElement).appendChild(s);
      }}
      try {{ f.run(); }} catch (e) {{ console.warn('audit fix ' + f.id + ' failed', e); }}
    }}
  }};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', apply);
  else apply();
}})();"""


# ------------------------------------------------------------------------------------------ findings
def load_results(run_dir: str | Path) -> dict:
    return json.loads((Path(run_dir) / "results.json").read_text())


def failing_personas(rows: list[dict], pass_rate: float = 1.0) -> list[str]:
    """Personas whose success rate is below pass_rate (rows = audit summary rows)."""
    return sorted({r["persona"] for r in rows if (r.get("success_rate") or 0) < pass_rate})


def collect_findings(records: list[dict], include_struggling: bool = False) -> list[dict]:
    """Failed runs (not harness errors) with what the agent reported and the pages it saw; with include_struggling,
    also completed runs that reported barriers (non-baseline)."""
    out = []
    for r in records:
        if r.get("status") == "harness_error":
            continue
        struggling = include_struggling and r.get("success") and r.get("persona") != "baseline" and \
            (r.get("report") or {}).get("obstacles")
        if r.get("success") and not struggling:
            continue
        rep = r.get("report") or {}
        out.append({
            "persona": r["persona"], "task": r["task"], "tag": r["tag"], "steps": r.get("steps"),
            "completed": bool(r.get("success")),
            "answer": rep.get("answer"), "summary": rep.get("summary"), "obstacles": rep.get("obstacles", []),
            "snapshots": r.get("snapshots", []),
        })
    return out


def _strip_scripts(html: str) -> str:
    """The page for an LLM: no scripts, no inline SVG artwork, no runs of whitespace."""
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"(<svg\b[^>]*>).*?</svg>", r"\1…</svg>", html, flags=re.S | re.I)
    html = re.sub(r"\s+", " ", html)
    return html


# ------------------------------------------------------------------------------------------ fixers
class CannedFixer:
    """Hand-written fixes (JSON with the patch shape); returns those for the personas that failed and not yet
    applied. Stand-in for Devin, same output shape."""

    source = "canned"

    def __init__(self, path: str | Path) -> None:
        """path: one fixes JSON file, or a folder of them (one per known site; matched by host and port)."""
        path = Path(path)
        files = sorted(path.glob("*.json")) if path.is_dir() else [path]
        patches = [json.loads(f.read_text()) for f in files]
        self.patch = {"summary": " ".join(p.get("summary", "") for p in patches[:1]),
                      "fixes": [f for p in patches for f in p.get("fixes", [])],
                      "summaries": {urlparse(p["fixes"][0]["page_url"]).netloc: p.get("summary", "")
                                    for p in patches if p.get("fixes")}}

    def generate(self, records: list[dict], rows: list[dict], base_dir: str | Path, site: dict,
                 previous: dict | None = None, personas: list[str] | None = None) -> dict:
        failing = set(personas if personas is not None else failing_personas(rows))
        done = {f["id"] for f in (previous or {}).get("fixes", [])}
        origin = urlparse(site["start_url"]).netloc  # canned fixes are written for one site only
        new = [f for f in self.patch["fixes"] if f["id"] not in done and failing & set(f["personas"])
               and urlparse(f["page_url"]).netloc == origin]
        summary = self.patch.get("summaries", {}).get(origin, self.patch.get("summary", ""))
        return {"source": self.source, "summary": summary, "fixes": new}


class DevinFixer:
    """Devin API v3: one session per round, structured output = the patch. Untested until we have a key."""

    source = "devin"

    def __init__(self, api_key: str | None = None, org_id: str | None = None, max_acu: int = 10,
                 timeout_s: float = 1800, base_url: str = "https://api.devin.ai/v3") -> None:
        self.api_key = api_key or os.environ["DEVIN_API_KEY"]
        self.org_id = org_id or os.environ["DEVIN_ORG_ID"]
        self.max_acu, self.timeout_s = max_acu, timeout_s
        self.base = f"{base_url}/organizations/{self.org_id}"
        self.http = httpx.Client(headers={"Authorization": f"Bearer {self.api_key}"}, timeout=60)

    def build_prompt(self, records: list[dict], base_dir: str | Path, site: dict, previous: dict | None,
                     personas: list[str] | None = None) -> str:
        """Everything Devin needs: who struggled and how, what the journey was, and the pages as those users saw them.
        personas: perspectives to fix (default: those that failed); struggling ones are included too."""
        findings = [f for f in collect_findings(records, include_struggling=True)
                    if personas is None or f["persona"] in personas]
        pages: dict[str, str] = {}
        for f in findings:
            for snap in reversed(f["snapshots"]):  # the pages where they ended up first
                if snap["url"] not in pages and len(pages) < 4:
                    html = _strip_scripts((Path(base_dir) / snap["file"]).read_text())
                    pages[snap["url"]] = html[:24_000]
        goals = {r["task"]: r.get("goal") for r in records if r.get("goal")}
        lines = [
            f"You are fixing accessibility barriers on {site['start_url']}. AI agents (H computer-use agents) tried a real "
            "user journey in a browser that simulates an impairment, and some of them failed or struggled. For each "
            "barrier, write ONE fix with:",
            "- a PREVIEW that our tool injects into the page: `css` (a stylesheet) and/or `js` (the BODY of a function run "
            "after DOMContentLoaded, idempotent, no external dependencies, robust selectors taken from the HTML below). "
            "The preview runs (a) on the live page, where the agents are re-run to check the fix, and (b) on a static copy "
            "of the page with its <script> tags removed (for before/after images): so the JS must not rely on the "
            "page's own scripts and must work in both cases (e.g. replace a clickable <div onclick=\"f()\"> with a real "
            "<button> that calls the same global function if it exists, or performs the same visible change);",
            "- `page_url`: the exact URL (from the list below) of the page the fix applies to; the preview only runs there;",
            "- `personas`: the ids of the perspectives it unblocks, among: baseline, low_vision (page zoomed to 200% "
            "with blurred vision), color_blind (deuteranopia: red and green look the same), tremor (every click lands up "
            "to 10 px off target), keyboard_only (no mouse: Tab, Shift+Tab, Enter, Space, arrow keys);",
            "- `wcag` (WCAG 2.2 criterion), `title`, `description` (for a product team) and `code_change` (the proper fix "
            "a developer should make in the source).",
            "Keep the page working for everyone; do not change the content, prices or dates. Do not open pull requests "
            "and do not modify any repository: only return the structured output.",
            "",
            "## The journey",
        ]
        lines += [f"- `{t}`: {g}" for t, g in goals.items()] or ["- (see the agents' reports below)"]
        lines += ["", "## Perspectives that failed or struggled, in the agents' own words"]
        for f in findings:
            lines.append(f"- `{f['persona']}` ({'completed with difficulty' if f.get('completed') else 'could not complete'}"
                         f", {f.get('steps')} steps): {f['summary']}")
            for o in f["obstacles"]:
                lines.append(f"  - [{o.get('severity')}] at {o.get('where')}: {o.get('what')}")
        if previous and previous.get("fixes"):
            lines += ["", "## Fixes already applied (still failing for the personas above; do not repeat them)"]
            lines += [f"- {f['id']}: {f['title']} ({f['wcag']})" for f in previous["fixes"]]
        lines += ["", "## Rendered HTML of the pages they saw (scripts stripped)"]
        for url, html in pages.items():
            lines += [f"### {url}", "```html", html, "```"]
        return "\n".join(lines)

    def generate(self, records: list[dict], rows: list[dict], base_dir: str | Path, site: dict,
                 previous: dict | None = None, personas: list[str] | None = None) -> dict:
        if personas is None:
            from autofix import needs_fix
            personas = needs_fix(records, rows)
        prompt = self.build_prompt(records, base_dir, site, previous, personas=personas)
        body = {
            "prompt": prompt,
            "title": f"Accessibility fixes for {urlparse(site['start_url']).netloc}",
            "tags": ["h-for-humble", "a11y-fix"],
            "structured_output_schema": PATCH_SCHEMA,
            "structured_output_required": True,
            "max_acu_limit": self.max_acu,
            "resumable": False,
        }
        # devin_mode: "fast" (~2x faster, same intelligence), "ultra" (most capable), "normal"... (DEVIN_MODE)
        mode = os.environ.get("DEVIN_MODE", "fast")
        r = self.http.post(f"{self.base}/sessions", json={**body, "devin_mode": mode} if mode else body)
        if r.status_code in (400, 403, 422) and mode:  # mode not available on this org: use its default
            r = self.http.post(f"{self.base}/sessions", json=body)
        r.raise_for_status()
        session = r.json()
        sid, url = session["session_id"], session.get("url")
        print(f"Devin session {url}", flush=True)
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            time.sleep(15)
            s = self.http.get(f"{self.base}/sessions/{sid}").json()
            out = s.get("structured_output")
            status, detail = s.get("status"), s.get("status_detail")
            print(f"Devin {sid}: {status}/{detail} · structured output: {bool(out)}", flush=True)
            if out and out.get("fixes") is not None and (detail in ("finished", "waiting_for_user")
                                                         or status in ("exit", "suspended")):
                return {"source": self.source, "devin_session_url": url, "summary": out.get("summary", ""),
                        "fixes": out["fixes"], "acus_consumed": s.get("acus_consumed")}
            if status == "error" or (status in ("exit", "suspended") and not out):
                raise RuntimeError(f"Devin session {url} ended without fixes ({status}/{detail})")
        raise TimeoutError(f"Devin session {url} gave no fixes within {self.timeout_s:.0f}s")


class FallbackFixer:
    """Devin first; if it fails or times out, hand-written fixes for known sites + the rule engine, so an audit always
    gets fixes."""

    def __init__(self, primary, fallback) -> None:
        self.primary, self.fallback = primary, fallback
        self.source = primary.source
        self.error: str | None = None

    def generate(self, records, rows, base_dir, site, previous=None) -> dict:
        try:
            out = self.primary.generate(records, rows, base_dir, site, previous)
            if out.get("fixes"):
                self.source = self.primary.source
                return out
            self.error = "Devin returned no fix"
        except Exception as exc:
            self.error = f"{type(exc).__name__}"
        out = self.fallback.generate(records, rows, base_dir, site, previous)
        self.source = out.get("source") or self.fallback.source
        return {**out, "fallback_reason": self.error}


def make_fixer(kind: str, canned_path: str | Path | None = None):
    if kind == "devin":
        return DevinFixer()
    if kind == "canned":
        return CannedFixer(canned_path or Path(__file__).parent / "fixes" / "demo_shop.json")
    if kind == "rules":
        from autofix import RuleFixer
        return RuleFixer()
    if kind == "auto":  # Devin when configured, else hand-written fixes for known sites + rules for any site
        from autofix import CompositeFixer
        local = CompositeFixer(CannedFixer(canned_path or Path(__file__).parent / "fixes"))
        if os.environ.get("DEVIN_API_KEY") and os.environ.get("DEVIN_ORG_ID"):
            return FallbackFixer(DevinFixer(timeout_s=1200), local)
        return local
    raise ValueError(f"unknown fixer {kind!r}")


def merge_patches(previous: dict | None, new: dict) -> dict:
    """Cumulative patch: earlier fixes stay, new ones are added (same id = replaced)."""
    fixes = {f["id"]: f for f in (previous or {}).get("fixes", [])}
    for f in new.get("fixes", []):
        fixes[f["id"]] = f
    merged = dict(new)
    merged["fixes"] = list(fixes.values())
    return merged

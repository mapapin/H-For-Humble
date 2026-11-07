"""Before/after images of suggested fixes, as a given persona sees the page.

We re-render the HTML snapshot captured during the audit (the page as the user saw it, scripts stripped, with a
<base> pointing at the real site for styles and images) in a Chrome set up like the persona's (zoom, vision
filter), once without and once with the fix patch.

Some barriers are invisible in a plain screenshot, so personas can add an overlay:
- keyboard_only: green numbered outlines on everything reachable with Tab, red label on clickable things that
  are not;
- tremor: click targets outlined red when smaller than 24x24 CSS px (WCAG 2.5.8), green otherwise.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from chrome import ChromeProcess, DevToolsController
from fixes import patch_script
from personas import PERSONAS

_BADGE = ("position:absolute;z-index:2147483647;font:bold 11px/1.2 Arial,sans-serif;padding:1px 4px;"
          "border-radius:3px;color:#fff;pointer-events:none;")

OVERLAYS = {
    "keyboard_only": """
setTimeout(function () {
  var focusable = 'a[href],button:not([disabled]),input:not([type=hidden]):not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])';
  var n = 0;
  function badge(el, text, color) {
    var r = el.getBoundingClientRect();
    var b = document.createElement('div');
    b.textContent = text;
    b.style.cssText = '%(badge)s' + 'background:' + color + ';left:' + (r.left + scrollX) + 'px;top:' + Math.max(0, r.top + scrollY - 14) + 'px';
    document.body.appendChild(b);
  }
  document.querySelectorAll(focusable).forEach(function (el) {
    if (!el.getClientRects().length) return;
    n += 1;
    el.style.outline = '3px solid #12a150';
    badge(el, 'Tab ' + n, '#12a150');
  });
  document.querySelectorAll('[onclick]').forEach(function (el) {
    if (el.matches(focusable) || !el.getClientRects().length) return;
    el.style.outline = '3px dashed #d0021b';
    badge(el, 'not reachable by keyboard', '#d0021b');
  });
}, 150);
""" % {"badge": _BADGE},
    "tremor": """
setTimeout(function () {
  var targets = 'a[href],button,input:not([type=hidden]),select,textarea,[onclick],[role=button]';
  document.querySelectorAll(targets).forEach(function (el) {
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    var small = r.width < 24 || r.height < 24;
    el.style.outline = (small ? '3px solid #d0021b' : '2px solid #12a150');
    if (small) {
      var b = document.createElement('div');
      b.textContent = Math.round(r.width) + 'x' + Math.round(r.height) + 'px';
      b.style.cssText = '%(badge)s' + 'background:#d0021b;left:' + (r.right + scrollX + 4) + 'px;top:' + (r.top + scrollY) + 'px';
      document.body.appendChild(b);
    }
  });
}, 150);
""" % {"badge": _BADGE},
}


def prepare_snapshot(html: str, url: str, scripts: list[str]) -> str:
    """Static copy of a captured page: original scripts removed (the DOM is already rendered), <base> to the real
    site so relative styles and images load, then our scripts (patch, overlay) in order."""
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    page_path = json.dumps(urlparse(url).path or "/")
    inject = f'<base href="{url}"><script>window.__auditPagePath = {page_path};</script>' + "".join(
        f"<script>document.addEventListener('DOMContentLoaded', function () {{ {s} }});</script>"
        if "__auditPatchApplied" not in s else f"<script>{s}</script>"
        for s in scripts
    )
    m = re.search(r"<head[^>]*>", html, flags=re.I)
    return html[:m.end()] + inject + html[m.end():] if m else inject + html


class PersonaRenderer:
    """One Chrome set up like the persona's; renders any number of snapshots."""

    def __init__(self, persona_id: str, port: int, headers: dict[str, str] | None = None) -> None:
        self.persona = PERSONAS[persona_id]
        self.chrome = ChromeProcess(port, self.persona, headless=True)
        try:
            self.devtools = DevToolsController(port, self.persona, headers=headers)
        except Exception:
            self.chrome.close()
            raise
        self.tmp = Path(tempfile.mkdtemp(prefix="audit-render-"))

    def render(self, html: str, url: str, out_png: Path, fixes: list[dict] | None = None,
               overlay: bool = True) -> Path:
        scripts = [patch_script(fixes)] if fixes else []
        if overlay and self.persona.id in OVERLAYS:
            scripts.append(OVERLAYS[self.persona.id])
        page = self.tmp / f"{out_png.stem}.html"
        page.write_text(prepare_snapshot(html, url, scripts))
        self.devtools.navigate(page.as_uri())
        self.devtools.wait_loaded()
        time.sleep(0.6)  # fixes and overlays run after DOMContentLoaded (+150 ms)
        out_png.write_bytes(self.devtools.screenshot())
        return out_png

    def close(self) -> None:
        try:
            self.devtools.close()
        finally:
            self.chrome.close()


def crop_to_content(before: Path, after: Path, pad_x: int = 160, pad_y: int = 120, min_width: int = 640,
                    min_height: int = 360) -> float | None:
    """Crop both images to the same box around what the fix changed (with margins, keeping the top of the page
    for context). Returns where the change starts, in % of the cropped width: the slider starts there, so the
    divider sits at the change instead of on empty page."""
    from PIL import Image, ImageChops

    a, b = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
    if a.size != b.size:
        return None
    changed = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
    if not changed:
        return None
    left, right = max(0, changed[0] - pad_x), min(a.width, changed[2] + pad_x)
    if right - left < min_width:  # widen around the change's centre
        centre = (changed[0] + changed[2]) // 2
        left = max(0, min(centre - min_width // 2, a.width - min_width))
        right = min(a.width, left + min_width)
    bottom = min(a.height, max(changed[3] + pad_y, min_height))
    box = (left, 0, right, bottom)
    a.crop(box).save(before)
    b.crop(box).save(after)
    # Start just left of the change: the "after" side then shows the whole fix; dragging right reveals "before".
    return round(max(5.0, min(95.0, 100 * (changed[0] - 12 - left) / (right - left))), 1)


def _path(url: str) -> str:
    return urlparse(url).path.rstrip("/") or "/"


def find_snapshot(records: list[dict], page_url: str, persona: str | None = None) -> dict | None:
    """Latest snapshot of this page, preferring the given persona's runs (what that user actually saw)."""
    want = _path(page_url)
    ordered = sorted(records, key=lambda r: (r.get("persona") != persona, bool(r.get("success"))))
    for r in ordered:
        for snap in reversed(r.get("snapshots", [])):
            if _path(snap["url"]) == want:
                return snap
    return None


def build_comparisons(records: list[dict], base_dir: str | Path, patch: dict, out_dir: str | Path,
                      headers: dict[str, str] | None = None, port: int = 9480) -> list[dict]:
    """Before/after image pairs:
    - one per fix, on its page, as its first persona sees it, with only that fix applied (per-criterion view);
    - one per persona the patch targets, on the page of its first fix, with all fixes applied (overall view)."""
    base_dir, out_dir = Path(base_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fixes = patch.get("fixes", [])
    jobs: list[dict] = []
    for f in fixes:
        persona = (f.get("personas") or ["baseline"])[0]
        jobs.append({"kind": "fix", "id": f["id"], "title": f["title"], "wcag": f.get("wcag"), "persona": persona,
                     "page_url": f["page_url"], "fixes_after": [f]})
    seen = set()
    for f in fixes:
        for persona in f.get("personas", []):
            if persona in seen:
                continue
            seen.add(persona)
            jobs.append({"kind": "persona", "id": persona, "title": f"{PERSONAS[persona].label}: all fixes",
                         "persona": persona, "page_url": f["page_url"], "fixes_after": fixes})
    by_persona: dict[str, list[dict]] = {}
    for j in jobs:
        by_persona.setdefault(j["persona"], []).append(j)
    results = []
    for i, (persona, items) in enumerate(by_persona.items()):
        renderer = PersonaRenderer(persona, port + i, headers=headers)
        try:
            for j in items:
                snap = find_snapshot(records, j["page_url"], persona)
                if snap is None:
                    j["error"] = "no snapshot of this page in the audit"
                    results.append({k: v for k, v in j.items() if k != "fixes_after"})
                    continue
                html = (base_dir / snap["file"]).read_text()
                stem = f"{j['kind']}-{j['id']}"
                renderer.render(html, snap["url"], out_dir / f"{stem}-before.png")
                renderer.render(html, snap["url"], out_dir / f"{stem}-after.png", fixes=j["fixes_after"])
                split = crop_to_content(out_dir / f"{stem}-before.png", out_dir / f"{stem}-after.png")
                results.append({"kind": j["kind"], "id": j["id"], "title": j["title"], "wcag": j.get("wcag"),
                                "persona": persona, "page_url": snap["url"],
                                "before": f"{stem}-before.png", "after": f"{stem}-after.png",
                                "split": split if split is not None else 50})
        finally:
            renderer.close()
    return results

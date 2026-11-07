"""Rule-based fix previews that work on any website (no knowledge of its code).

When Devin is not configured and we have no hand-written fixes for a site, this fixer still turns each failing
perspective into a concrete, previewable patch, derived from WCAG techniques and applied to the live DOM:

- keyboard_only  2.1.1 / 2.4.7: clickable elements that are not focusable get tabindex, a role and Enter/Space
                 activation; every focusable element gets a strong visible focus ring.
- tremor         2.5.8: interactive targets under 24x24 CSS px are enlarged to 44x44 (inline text links excepted).
- low_vision     1.4.3 / 1.4.10 / 1.4.4: text below the contrast minimum is darkened (or lightened), tiny text is
                 enlarged, and content wider than the viewport reflows instead of being cut off.
- color_blind    1.4.1: colour swatches explained by a legend ("● In stock") get the legend's words next to every
                 swatch of the same colour, so meaning no longer depends on hue alone.

These are previews (what the page would be like with the fix), used for before/after images and re-runs. Each
fix also carries the proper source change for developers.
"""

from __future__ import annotations

from urllib.parse import urlparse

from fixes import failing_personas

KEYBOARD_JS = r"""
var focusable = 'a[href],button,input,select,textarea,summary,[tabindex]';
var n = 0;
document.querySelectorAll('[onclick],[role=button],[role=link],[role=tab],[role=checkbox],[role=option],div,span,li,img,svg').forEach(function (el) {
  if (n > 400 || el.dataset.humbleKeyboard) return;
  var clickable = el.hasAttribute('onclick') || /^(button|link|tab|checkbox|option)$/.test(el.getAttribute('role') || '');
  if (!clickable) {
    var cs = getComputedStyle(el);
    if (cs.cursor !== 'pointer' || (el.parentElement && getComputedStyle(el.parentElement).cursor === 'pointer')) return;
  }
  if (el.matches(focusable) || el.closest('a[href],button,label') || el.querySelector(focusable)) return;
  if (!el.getClientRects().length) return;
  el.dataset.humbleKeyboard = '1';
  n += 1;
  el.tabIndex = 0;
  if (!el.getAttribute('role')) el.setAttribute('role', 'button');
  el.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.click(); }
  });
});
"""
KEYBOARD_CSS = """
:focus-visible, [data-humble-keyboard]:focus {
  outline: 3px solid #2563eb !important; outline-offset: 2px !important;
  box-shadow: 0 0 0 6px rgba(37, 99, 235, .28) !important;
}"""

TARGET_JS = r"""
var sel = 'a[href],button,input[type=checkbox],input[type=radio],input[type=button],input[type=submit],select,[role=button],[onclick],[tabindex="0"]';
var n = 0;
document.querySelectorAll(sel).forEach(function (el) {
  if (n > 300 || el.dataset.humbleTarget) return;
  var r = el.getBoundingClientRect();
  if (!r.width || !r.height || (r.width >= 24 && r.height >= 24)) return;
  // WCAG 2.5.8 exception: links inside a sentence
  var p = el.closest('p,li');
  if (el.tagName === 'A' && p && (p.textContent || '').trim().length > (el.textContent || '').trim().length + 20) return;
  el.dataset.humbleTarget = '1';
  n += 1;
  var cs = getComputedStyle(el);
  if (cs.display === 'inline') el.style.display = 'inline-flex';
  el.style.minWidth = '44px';
  el.style.minHeight = '44px';
  el.style.alignItems = 'center';
  el.style.justifyContent = 'center';
  el.style.boxSizing = 'border-box';
  el.style.margin = '4px';
});
"""

LOW_VISION_JS = r"""
function lum(c) {
  var m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null;
  var v = m[1].split(',').map(function (x) { return parseFloat(x); });
  if (v.length > 3 && v[3] < 0.1) return null;
  var a = v.slice(0, 3).map(function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
  return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
}
function bg(el) {
  for (var e = el; e && e.nodeType === 1; e = e.parentElement) {
    var cs = getComputedStyle(e);
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;  // text on an image or gradient: skip
    var l = lum(cs.backgroundColor); if (l !== null) return l;
  }
  return 1;
}
var n = 0;
document.querySelectorAll('body *').forEach(function (el) {
  if (n > 800 || el.dataset.humbleText) return;
  var own = Array.prototype.some.call(el.childNodes, function (c) { return c.nodeType === 3 && c.textContent.trim().length > 1; });
  if (!own || !el.getClientRects().length) return;
  var cs = getComputedStyle(el), fg = lum(cs.color), b = bg(el);
  if (fg === null || b === null) return;
  var size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
  var ratio = (Math.max(fg, b) + 0.05) / (Math.min(fg, b) + 0.05);
  var changed = false;
  if (ratio < (large ? 3 : 4.5)) { el.style.setProperty('color', b > 0.4 ? '#1f2328' : '#ffffff', 'important'); changed = true; }
  if (size < 13) { el.style.setProperty('font-size', '15px', 'important'); changed = true; }
  if (changed) { el.dataset.humbleText = '1'; n += 1; }
});
// Reflow (1.4.10): nothing wider than the viewport, no clipped horizontal overflow.
var vw = document.documentElement.clientWidth;
document.documentElement.style.setProperty('overflow-x', 'auto', 'important');
document.body.style.setProperty('overflow-x', 'auto', 'important');
document.querySelectorAll('body *').forEach(function (el) {
  var r = el.getBoundingClientRect();
  if (r.width <= vw + 2 || el.dataset.humbleReflow) return;
  var cs = getComputedStyle(el);
  if (cs.position === 'fixed' || el.tagName === 'IMG' || el.tagName === 'VIDEO') return;
  el.dataset.humbleReflow = '1';
  el.style.setProperty('max-width', '100%', 'important');
  el.style.setProperty('width', 'auto', 'important');
  el.style.setProperty('min-width', '0', 'important');
  if (cs.display.indexOf('flex') >= 0) el.style.setProperty('flex-wrap', 'wrap', 'important');
  if (cs.display.indexOf('grid') >= 0) el.style.setProperty('grid-template-columns', '1fr', 'important');
});
document.querySelectorAll('body *').forEach(function (el) {
  var cs = getComputedStyle(el);
  if (cs.position === 'absolute' && el.getBoundingClientRect().right > vw + 2 && !el.closest('[role=dialog]')) {
    el.style.setProperty('position', 'static', 'important');
  }
});
"""

COLOR_JS = r"""
function sat(c) {
  var m = c.match(/rgba?\(([^)]+)\)/); if (!m) return 0;
  var v = m[1].split(',').map(function (x) { return parseFloat(x) / 255; });
  if (v.length > 3 && v[3] * 255 < 0.5) return 0;
  var mx = Math.max(v[0], v[1], v[2]), mn = Math.min(v[0], v[1], v[2]);
  return mx === 0 ? 0 : (mx - mn) / mx;
}
function swatches() {
  var out = [];
  document.querySelectorAll('body *').forEach(function (el) {
    var r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || r.width > 48 || r.height > 48 || (el.textContent || '').trim().length > 3) return;
    if (sat(cs.backgroundColor) < 0.35) return;
    out.push(el);
  });
  return out;
}
var all = swatches(), legend = {};
all.forEach(function (el) {
  // A legend entry: a swatch followed by a short explanation ("In stock", "Sold out").
  var text = '';
  var next = el.nextSibling;
  while (next && !text) { text = (next.textContent || '').trim(); next = next.nextSibling; }
  var color = getComputedStyle(el).backgroundColor;
  if (text && text.length <= 60 && !legend[color]) legend[color] = text.split(/[(,.;]/)[0].trim();
});
var n = 0;
all.forEach(function (el) {
  var color = getComputedStyle(el).backgroundColor, label = legend[color];
  if (!label || el.dataset.humbleColor || n > 200) return;
  var next = el.nextSibling, nextText = next ? (next.textContent || '').trim() : '';
  if (nextText.indexOf(label) === 0) return;  // this is the legend itself
  el.dataset.humbleColor = '1';
  n += 1;
  el.setAttribute('aria-label', label);
  el.title = label;
  var tag = document.createElement('span');
  tag.textContent = label;
  tag.style.cssText = 'display:inline-block;margin:0 6px;padding:1px 6px;border:1px solid currentColor;border-radius:4px;font:600 11px/1.4 system-ui,sans-serif;color:#1f2328;background:#fff;vertical-align:middle;white-space:nowrap';
  el.insertAdjacentElement('afterend', tag);
});
"""

RULES = {
    "keyboard_only": {
        "id": "rules-keyboard-access", "title": "Make every clickable control keyboard operable",
        "wcag": "2.1.1 Keyboard · 2.4.7 Focus Visible",
        "description": "Some controls only react to a mouse click (clickable <div>/<span>, missing focus), and focus "
                       "is hard or impossible to see. Keyboard users get stuck. The preview makes those controls "
                       "focusable and operable with Enter/Space, and draws a clear focus ring.",
        "css": KEYBOARD_CSS, "js": KEYBOARD_JS,
        "code_change": "Use native <button> (actions) and <a href> (navigation) elements instead of clickable "
                       "<div>/<span>; if a custom control is unavoidable, give it tabindex=\"0\", the right role and "
                       "Enter/Space handlers. Never remove the focus outline without a visible :focus-visible style.",
    },
    "tremor": {
        "id": "rules-target-size", "title": "Enlarge small click targets to 44 px",
        "wcag": "2.5.8 Target Size (Minimum)",
        "description": "Buttons and links smaller than 24×24 px, close to each other, make imprecise pointers hit "
                       "the wrong control. The preview gives every small target a 44×44 px hit area with spacing.",
        "css": "", "js": TARGET_JS,
        "code_change": "Give interactive controls a minimum 44×44 CSS px hit area (padding, min-width/min-height) "
                       "and at least 8 px between adjacent targets.",
    },
    "low_vision": {
        "id": "rules-low-vision", "title": "Readable text and reflow at 200% zoom",
        "wcag": "1.4.3 Contrast (Minimum) · 1.4.10 Reflow · 1.4.4 Resize Text",
        "description": "At 200% zoom, content wider than the screen is cut off instead of reflowing, and some text "
                       "is too small or too low-contrast to read. The preview reflows wide content into the "
                       "viewport, enlarges tiny text and raises text contrast to at least 4.5:1.",
        "css": "", "js": LOW_VISION_JS,
        "code_change": "Use fluid layouts (max-width: 100%, flex-wrap, responsive breakpoints) instead of fixed "
                       "widths and overflow: hidden; size text in rem with at least 14-16 px body copy; keep text "
                       "contrast at 4.5:1 (3:1 for large text).",
    },
    "color_blind": {
        "id": "rules-color-meaning", "title": "Do not convey meaning with colour alone",
        "wcag": "1.4.1 Use of Color",
        "description": "Status is shown only by colour (red / green swatches explained by a colour legend). Users "
                       "with colour blindness cannot tell them apart. The preview writes the legend's words next "
                       "to each swatch.",
        "css": "", "js": COLOR_JS,
        "code_change": "Pair every colour-coded status with text or an icon (e.g. \"In stock\" / \"Sold out\"), "
                       "and expose it to assistive technologies; keep the colour as a secondary cue.",
    },
}


def stuck_page(records: list[dict], persona: str, fallback: str) -> str:
    """The last page a persona saw in a failed run: where the before/after image is taken."""
    for r in records:
        if r.get("persona") == persona and not r.get("success") and r.get("snapshots"):
            return r["snapshots"][-1]["url"]
    return fallback


class RuleFixer:
    source = "rules"

    def generate(self, records: list[dict], rows: list[dict], base_dir, site: dict, previous: dict | None = None,
                 personas: list[str] | None = None) -> dict:
        done = {f["id"] for f in (previous or {}).get("fixes", [])}
        failing = personas if personas is not None else failing_personas(rows)
        fixes = []
        for persona in failing:
            rule = RULES.get(persona)
            if not rule or rule["id"] in done:
                continue
            fixes.append({**rule, "personas": [persona], "scope": "site", "source": "rules",
                          "page_url": stuck_page(records, persona, site["start_url"])})
        host = urlparse(site["start_url"]).netloc
        return {"source": self.source, "fixes": fixes,
                "summary": f"Rule-based previews for {host}, one per perspective that could not complete the "
                           "journey (WCAG techniques applied to the live page)."}


class CompositeFixer:
    """Hand-written fixes when we have them for this site, rules for every other failing perspective."""

    source = "canned+rules"

    def __init__(self, canned, rules: RuleFixer | None = None) -> None:
        self.canned, self.rules = canned, rules or RuleFixer()

    def generate(self, records, rows, base_dir, site, previous=None) -> dict:
        first = self.canned.generate(records, rows, base_dir, site, previous) if self.canned else {"fixes": []}
        covered = {p for f in first["fixes"] for p in f["personas"]}
        covered |= {p for f in (previous or {}).get("fixes", []) for p in f.get("personas", [])}
        rest = [p for p in failing_personas(rows) if p not in covered]
        second = self.rules.generate(records, rows, base_dir, site, previous, personas=rest)
        fixes = first["fixes"] + second["fixes"]
        sources = {f.get("source", "canned") for f in fixes}
        self.source = "rules" if sources == {"rules"} else "canned" if "rules" not in sources else "canned+rules"
        summary = " ".join(s for s in (first.get("summary") if first["fixes"] else "", second.get("summary") if second["fixes"] else "") if s)
        return {"source": self.source, "summary": summary, "fixes": fixes}

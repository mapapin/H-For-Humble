"""Lune: a polished, deliberately inaccessible demo site (book a pottery workshop in Paris).

It looks like a real, well-funded startup, and hides one accessibility trap per impaired persona, all on the
booking journey "book 2 seats for Wheel throwing for beginners on the first available December date, report the
checkout total" (expected answer: Total EUR 98.00).

Pages:
- /                          landing page (responsive, keyboard focus styled on its own links and cards).
- /workshops/<slug>          workshop page: December date strip, seats stepper, booking card, "Reserve".
- POST /book                 stores the selection in the cookie session, then 303 -> /checkout.
- /checkout                  final summary with the total and a disabled "Pay" button.
- /login                     only used when LUNE_REQUIRE_LOGIN=1 (checkout then needs the test account).

Traps (all on /workshops/<slug> unless noted):
- Colour blind (WCAG 1.4.1): date availability is shown ONLY by the chip colour, green #419f51 = available,
  red #cf5f59 = sold out. The pair has the same luminance and maps to the same colour under deuteranopia
  (Chrome's Machado matrix). No text, icon or pattern; the legend is colour-only too. The first eight December
  sessions are sold out (first available: Tue 8 Dec). Sold-out chips still look clickable and can be selected;
  the booking card does not say anything, and the checkout then shows a waitlist request with
  "Total due today EUR 0.00" instead of EUR 98.00.
- Hand tremor (WCAG 2.5.8): the seats stepper "- +" buttons are 14x14 px with a 1 px divider, and the date chips
  are 18x18 px with 2 px gaps. A +/-10 px jitter misses often, and can hit "-" instead of "+".
- Keyboard only (WCAG 2.1.1 / 2.4.7): date chips and the "Reserve" button are <div onclick> (not focusable, no
  key handler), and `*:focus { outline: none }` removes focus rings site-wide. The landing page styles its own
  links and cards on :focus, so a keyboard user gets to the workshop page, then cannot pick a date or reserve.
- Low vision (WCAG 1.4.10): the workshop page uses a fixed 1240 px grid inside an `overflow-x: clip` wrapper,
  with `overflow-x: hidden` on html/body; the booking card (seats, total, Reserve) sits in the right column.
  At 200% zoom (CSS viewport about 640 px) it is cut off and cannot be scrolled, focused or found.
- Low vision (WCAG 1.4.3), /checkout: the price details, including the total, are 12 px light grey (#cdc6bb on
  #fffcf7, about 1.7:1).

Server state: an in-memory cookie session (`lune_sid`) holds the booking (and the signed-in user). Deterministic:
availability is fixed, December 2026.

Optional header auth: set LUNE_TOKEN and every request must carry `X-Audit-Token: <token>` (403 otherwise).
Optional login: LUNE_REQUIRE_LOGIN=1 makes /checkout require the test account below (local fake account).

Run:  python poc/demo_startup/server.py --port 8770
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import secrets
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSS = (HERE / "lune.css").read_text()
BOOKING_JS = (HERE / "booking.js").read_text()

# Test account for this local demo app only (also listed in poc/presets/lune.yaml).
LUNE_USER = os.environ.get("LUNE_USER", "camille@lune-atelier.test")
LUNE_PASSWORD = os.environ.get("LUNE_PASSWORD", "terre-cuite-2026")
LUNE_TOKEN = os.environ.get("LUNE_TOKEN", "")
REQUIRE_LOGIN = os.environ.get("LUNE_REQUIRE_LOGIN", "") in ("1", "true", "yes")

YEAR, MONTH = 2026, 12
MAX_SEATS = 6
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

SESSIONS: dict[str, dict] = {}


# ----------------------------------------------------------------------------------------------- catalogue
def _studio_closed(day: int) -> bool:
    d = dt.date(YEAR, MONTH, day)
    return d.weekday() == 0 or day in (24, 25, 31)  # closed on Mondays, Christmas Eve/Day and NYE


WORKSHOPS: dict[str, dict] = {
    "wheel-throwing": {
        "title": "Wheel throwing for beginners", "title_html": "Wheel throwing <em>for beginners</em>",
        "price": 49, "duration": "2h30", "level": "Beginner", "group": "Max 8 people", "rating": "4.9",
        "reviews": 612, "art": "wheel", "host": "Inès", "host_full": "Inès Marchand",
        "host_bio": "Ceramicist, trained in Limoges. Has taught 4,000 first-timers to centre clay.",
        "card_meta": "2h30 · Max 8 people",
        "lede": "Two and a half hours at the wheel to centre clay, pull your first walls and throw two bowls "
                "to take home. Small group, two teachers, no experience needed.",
        "times": ("18:30", "21:00", "10:30", "13:00"),
        # the whole first week sold out; first open date: Tue 8 Dec, first cell of the second calendar row
        "full": {1, 2, 3, 4, 5, 6, 12, 13, 19, 20, 26, 27},
        "closed": _studio_closed,
    },
    "hand-building": {
        "title": "Hand-building: coil & pinch", "title_html": "Hand-building: <em>coil &amp; pinch</em>",
        "price": 39, "duration": "2h", "level": "All levels", "group": "Max 10 people", "rating": "4.8",
        "reviews": 344, "art": "coil", "host": "Malo", "host_full": "Malo Ferrand",
        "host_bio": "Sculptor and slow-craft enthusiast. Believes every wonky pot has character.",
        "card_meta": "2h · Max 10 people",
        "lede": "No wheel, just your hands. Learn pinching, coiling and slab work and make a planter and a "
                "little dish of your own design.",
        "times": ("19:00", "21:00", "11:00", "13:00"),
        "full": {1, 2, 5, 6, 12, 13, 19},
        "closed": _studio_closed,
    },
    "glaze-lab": {
        "title": "Glaze lab: colour & texture", "title_html": "Glaze lab: <em>colour &amp; texture</em>",
        "price": 55, "duration": "3h", "level": "Intermediate", "group": "Max 6 people", "rating": "5.0",
        "reviews": 128, "art": "glaze", "host": "Ana", "host_full": "Ana Duval",
        "host_bio": "Glaze chemist turned potter. Keeps a notebook of 600 recipes.",
        "card_meta": "3h · Max 6 people",
        "lede": "Mix, layer and test glazes on your own bisque pieces. Leave with test tiles, recipes and a "
                "much better eye for colour.",
        "times": ("18:00", "21:00", "14:00", "17:00"),
        "full": {2, 3, 5, 9, 12},
        "closed": lambda day: _studio_closed(day) or dt.date(YEAR, MONTH, day).weekday() in (1, 3),
    },
    "date-night": {
        "title": "Date night at the wheel", "title_html": "Date night <em>at the wheel</em>",
        "price": 65, "duration": "2h", "level": "Beginner", "group": "Couples · Max 6 pairs", "rating": "4.9",
        "reviews": 287, "art": "pair", "host": "Inès", "host_full": "Inès Marchand",
        "host_bio": "Ceramicist, trained in Limoges. Has taught 4,000 first-timers to centre clay.",
        "card_meta": "2h · Evenings · For two",
        "lede": "Natural wine, a shared wheel and a little friendly competition. Make a pair of cups together "
                "on a Friday or Saturday night.",
        "times": ("19:30", "21:30", "19:30", "21:30"),
        "full": {4, 5, 11, 12, 18},
        "closed": lambda day: _studio_closed(day) or dt.date(YEAR, MONTH, day).weekday() not in (4, 5),
    },
}


def day_status(w: dict, day: int) -> str:
    if w["closed"](day):
        return "closed"
    return "full" if day in w["full"] else "open"


def session_time(w: dict, day: int) -> tuple[str, str]:
    weekend = dt.date(YEAR, MONTH, day).weekday() >= 5
    t = w["times"]
    return (t[2], t[3]) if weekend else (t[0], t[1])


def short_label(w: dict, day: int) -> str:
    d = dt.date(YEAR, MONTH, day)
    return f"{DOW[d.weekday()]} {day} Dec · {session_time(w, day)[0]}"


# ----------------------------------------------------------------------------------------------- artwork
def logo_svg() -> str:
    return ('<svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="16" fill="#172038"/>'
            '<circle cx="14.5" cy="16.5" r="9" fill="#f3dfc0"/><circle cx="18.6" cy="13.2" r="8" fill="#172038"/></svg>')


FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' "
           "cy='16' r='16' fill='%23172038'/%3E%3Ccircle cx='14.5' cy='16.5' r='9' fill='%23f3dfc0'/%3E%3Ccircle "
           "cx='18.6' cy='13.2' r='8' fill='%23172038'/%3E%3C/svg%3E")


def _grain(pid: str, w: int, h: int, opacity: float = 0.10) -> str:
    return (f'<filter id="{pid}g" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" '
            f'baseFrequency=".85" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/>'
            f'<feComponentTransfer><feFuncA type="table" tableValues="0 {opacity}"/></feComponentTransfer></filter>',
            f'<rect width="{w}" height="{h}" filter="url(#{pid}g)"/>')


def _sparkle(x: float, y: float, s: float = 6, fill: str = "#fff8ea") -> str:
    k = s / 4
    return (f'<path d="M{x} {y - s}L{x + k} {y - k}L{x + s} {y}L{x + k} {y + k}L{x} {y + s}L{x - k} {y + k}'
            f'L{x - s} {y}L{x - k} {y - k}Z" fill="{fill}"/>')


def hero_svg() -> str:
    p = "h"
    gdef, grect = _grain(p, 540, 600, 0.12)
    return f"""<svg viewBox="0 0 540 600" role="img" aria-label="Hand-thrown vase, bowl and cup under a crescent moon">
<defs>
 <linearGradient id="{p}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#efcfb8"/><stop offset=".62" stop-color="#e8b596"/><stop offset="1" stop-color="#dd9f7e"/></linearGradient>
 <radialGradient id="{p}halo" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#fff7ea" stop-opacity=".6"/><stop offset="1" stop-color="#fff7ea" stop-opacity="0"/></radialGradient>
 <linearGradient id="{p}moon" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fffbf2"/><stop offset="1" stop-color="#f7dfb4"/></linearGradient>
 <mask id="{p}cres"><rect width="540" height="600" fill="#fff"/><circle cx="408" cy="122" r="62" fill="#000"/></mask>
 <linearGradient id="{p}vase" x1="0" x2="1"><stop offset="0" stop-color="#18213f"/><stop offset=".42" stop-color="#3a4d86"/><stop offset=".7" stop-color="#2a3966"/><stop offset="1" stop-color="#141b33"/></linearGradient>
 <linearGradient id="{p}glz" x1="0" x2="1"><stop offset="0" stop-color="#d9c7a6"/><stop offset=".45" stop-color="#f4ead7"/><stop offset="1" stop-color="#cdb895"/></linearGradient>
 <linearGradient id="{p}bowl" x1="0" x2="1"><stop offset="0" stop-color="#6a8466"/><stop offset=".45" stop-color="#a7bd9f"/><stop offset="1" stop-color="#5f785b"/></linearGradient>
 <linearGradient id="{p}cup" x1="0" x2="1"><stop offset="0" stop-color="#97412a"/><stop offset=".45" stop-color="#d27b57"/><stop offset="1" stop-color="#8b3b25"/></linearGradient>
 <linearGradient id="{p}tbl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d99d7a"/><stop offset="1" stop-color="#c47c58"/></linearGradient>
 <filter id="{p}blur" x="-30%" y="-100%" width="160%" height="300%"><feGaussianBlur stdDeviation="5"/></filter>
 <clipPath id="{p}cupclip"><path d="M74 420L146 420L142 484C141 490 136 494 130 494L90 494C84 494 79 490 78 484Z"/></clipPath>
 {gdef}
</defs>
<rect width="540" height="600" fill="url(#{p}bg)"/>
<circle cx="372" cy="150" r="150" fill="url(#{p}halo)"/>
<circle cx="372" cy="150" r="74" fill="url(#{p}moon)" mask="url(#{p}cres)"/>
{_sparkle(468, 236, 7)}{_sparkle(300, 92, 5)}{_sparkle(446, 64, 4)}{_sparkle(258, 170, 3.5)}
<path d="M62 318C52 226 150 184 236 212C300 232 334 192 404 210C484 232 506 326 478 396C448 466 336 476 254 466C152 454 72 412 62 318Z" fill="#c8d3bb" opacity=".55"/>
<rect x="0" y="474" width="540" height="126" fill="url(#{p}tbl)"/>
<rect x="0" y="474" width="540" height="2" fill="#f4d3bd" opacity=".7"/>
<ellipse cx="215" cy="490" rx="74" ry="9" fill="#6e331c" opacity=".35" filter="url(#{p}blur)"/>
<ellipse cx="372" cy="494" rx="86" ry="10" fill="#6e331c" opacity=".35" filter="url(#{p}blur)"/>
<ellipse cx="112" cy="497" rx="46" ry="7" fill="#6e331c" opacity=".35" filter="url(#{p}blur)"/>
<path d="M169 486C119 478 111 392 137 330C153 292 189 282 191 256C192 240 179 226 179 214L251 214C251 226 238 240 239 256C241 282 277 292 293 330C319 392 311 478 261 486Z" fill="url(#{p}vase)"/>
<path d="M179 214L251 214C251 226 238 240 239 256C241 282 270 290 286 318C278 330 270 316 262 330C254 344 246 322 237 338C228 352 220 328 210 342C200 354 194 326 184 338C175 348 166 322 156 334C150 340 146 330 143 322C158 290 189 282 191 256C192 240 179 226 179 214Z" fill="url(#{p}glz)"/>
<ellipse cx="215" cy="214" rx="36" ry="7" fill="#b9a27d"/><ellipse cx="215" cy="215" rx="28" ry="4.5" fill="#3b301f"/>
<path d="M150 372C142 410 145 446 164 472" stroke="#fff" stroke-opacity=".22" stroke-width="7" fill="none" stroke-linecap="round"/>
<path d="M284 412C288 458 322 490 372 490C422 490 456 458 460 412Z" fill="url(#{p}bowl)"/>
<path d="M291 438C330 454 414 454 453 438" stroke="#eef3e8" stroke-opacity=".75" stroke-width="2.5" fill="none"/>
<path d="M296 452C334 466 410 466 448 452" stroke="#eef3e8" stroke-opacity=".45" stroke-width="1.5" fill="none"/>
<ellipse cx="372" cy="412" rx="88" ry="14" fill="#d9e3d1"/><ellipse cx="372" cy="414" rx="81" ry="10" fill="#58704f"/>
<path d="M304 420C320 440 350 446 372 446" stroke="#fff" stroke-opacity=".18" stroke-width="5" fill="none" stroke-linecap="round"/>
<path d="M146 438C174 436 174 476 143 472" stroke="#a54a2d" stroke-width="9" fill="none" stroke-linecap="round"/>
<path d="M74 420L146 420L142 484C141 490 136 494 130 494L90 494C84 494 79 490 78 484Z" fill="url(#{p}cup)"/>
<rect x="60" y="470" width="100" height="30" fill="#ebc6a3" clip-path="url(#{p}cupclip)"/>
<path d="M78 470C92 476 128 476 142 470" stroke="#7e3420" stroke-opacity=".35" stroke-width="1.5" fill="none"/>
<ellipse cx="110" cy="420" rx="36" ry="6" fill="#7b321f"/>
{grect}
</svg>"""


def card_svg(kind: str, pid: str, w: int = 400, h: int = 340) -> str:
    """Small illustration for a workshop card (and checkout / workshop page)."""
    gdef, grect = _grain(pid, w, h, 0.10)
    cx = w / 2
    if kind == "wheel":
        bg = ("#f3dcc8", "#e3a888")
        body = f"""
<circle cx="{w - 70}" cy="70" r="30" fill="#fff6e6" opacity=".9"/><circle cx="{w - 58}" cy="62" r="26" fill="#efc5a6"/>
{_sparkle(70, 70, 6)}{_sparkle(110, 40, 3.5)}
<ellipse cx="{cx}" cy="{h - 70}" rx="156" ry="36" fill="#5b4a40"/>
<ellipse cx="{cx}" cy="{h - 76}" rx="146" ry="30" fill="#8a7465"/>
<ellipse cx="{cx}" cy="{h - 78}" rx="120" ry="22" fill="none" stroke="#fff" stroke-opacity=".25" stroke-width="2" stroke-dasharray="30 18"/>
<path d="M{cx - 74} {h - 84}C{cx - 70} {h - 150} {cx - 34} {h - 168} {cx - 30} {h - 206}L{cx + 30} {h - 206}C{cx + 34} {h - 168} {cx + 70} {h - 150} {cx + 74} {h - 84}Z" fill="url(#{pid}c)"/>
<ellipse cx="{cx}" cy="{h - 206}" rx="30" ry="7" fill="#8e3f27"/><ellipse cx="{cx}" cy="{h - 205}" rx="20" ry="4" fill="#5f2716"/>
<path d="M{cx - 52} {h - 120}C{cx - 48} {h - 150} {cx - 30} {h - 166} {cx - 22} {h - 190}" stroke="#fff" stroke-opacity=".25" stroke-width="5" fill="none" stroke-linecap="round"/>"""
        extra = f'<linearGradient id="{pid}c" x1="0" x2="1"><stop offset="0" stop-color="#9c4429"/><stop offset=".45" stop-color="#d27a56"/><stop offset="1" stop-color="#8f3d25"/></linearGradient>'
        if w > 600:  # wide version (workshop page): a shelf of drying pots on the left
            pots = ""
            for x, pw, ph, c in [(70, 46, 58, "#2f3f72"), (130, 60, 40, "#9db494"), (204, 34, 70, "#f0e3cc")]:
                pots += (f'<path d="M{x} {150}C{x - 4} {150 - ph * .6} {x + 4} {150 - ph} {x + pw * .3} {150 - ph}L{x + pw * .7} {150 - ph}'
                         f'C{x + pw - 4} {150 - ph} {x + pw + 4} {150 - ph * .6} {x + pw} {150}Z" fill="{c}"/>')
            body += (f'<rect x="48" y="150" width="210" height="8" rx="3" fill="#b9805f"/>'
                     f'<rect x="48" y="158" width="210" height="4" fill="#9b6448" opacity=".5"/>{pots}'
                     f'<path d="M{w - 210} {h - 40}C{w - 190} {h - 70} {w - 150} {h - 72} {w - 120} {h - 50}" stroke="#fff" stroke-opacity=".35" stroke-width="2" fill="none"/>')
    elif kind == "coil":
        bg = ("#e6ecdd", "#bccbb1")
        rows = [(150, "#efe2cc"), (172, "#e4d1b4"), (186, "#efe2cc"), (190, "#e4d1b4"), (184, "#efe2cc"), (168, "#e4d1b4")]
        y0 = h - 92 - len(rows) * 22
        coils = "".join(
            f'<rect x="{cx - rw / 2}" y="{y0 + i * 22}" width="{rw}" height="24" rx="12" fill="{c}" stroke="#c9b18e" stroke-width="1"/>'
            for i, (rw, c) in enumerate(rows))
        body = f"""
<path d="M40 {h - 40}C60 {h - 160} 140 {h - 230} {w - 60} {h - 250}" stroke="#a8bb9c" stroke-width="2" fill="none" opacity=".8"/>
{_sparkle(w - 70, 64, 6)}{_sparkle(80, 80, 4)}
<ellipse cx="{cx}" cy="{h - 66}" rx="118" ry="12" fill="#5d7356" opacity=".35"/>
{coils}
<path d="M{cx - 70} {y0 + 30}C{cx - 74} {y0 + 70} {cx - 72} {y0 + 110} {cx - 60} {y0 + 128}" stroke="#fff" stroke-opacity=".35" stroke-width="5" fill="none" stroke-linecap="round"/>"""
        extra = ""
    elif kind == "glaze":
        bg = ("#dfe3f2", "#aeb8de")
        tiles = ""
        for i, (dx, rot, glaze) in enumerate([(-96, -8, "#2b3c72"), (0, 0, "#c96d3e"), (96, 8, "#7f9a78")]):
            x, y = cx + dx - 38, h - 236
            tiles += (f'<g transform="rotate({rot} {x + 38} {y + 70})"><rect x="{x}" y="{y}" width="76" height="140" rx="10" fill="#f1e8da"/>'
                      f'<path d="M{x} {y + 10}Q{x} {y} {x + 10} {y}L{x + 66} {y}Q{x + 76} {y} {x + 76} {y + 10}L{x + 76} {y + 74}'
                      f'C{x + 66} {y + 84} {x + 60} {y + 66} {x + 50} {y + 80}C{x + 42} {y + 94} {x + 34} {y + 70} {x + 24} {y + 84}'
                      f'C{x + 16} {y + 94} {x + 8} {y + 76} {x} {y + 80}Z" fill="{glaze}"/></g>')
        body = f"""
{_sparkle(70, 60, 6)}{_sparkle(w - 80, 50, 4)}
<ellipse cx="{cx}" cy="{h - 80}" rx="150" ry="12" fill="#4b5893" opacity=".25"/>
{tiles}"""
        extra = ""
    else:  # pair of cups
        bg = ("#f2d8d3", "#d7a19f")
        body = f"""
<circle cx="{w - 80}" cy="76" r="34" fill="#fff6ea"/><circle cx="{w - 66}" cy="66" r="30" fill="#e6b9b5"/>
{_sparkle(70, 70, 6)}{_sparkle(120, 40, 3.5)}{_sparkle(w - 140, 120, 3)}
<ellipse cx="{cx}" cy="{h - 70}" rx="140" ry="12" fill="#7a3c3a" opacity=".3"/>
<path d="M{cx - 120} {h - 196}L{cx - 20} {h - 196}L{cx - 26} {h - 82}C{cx - 27} {h - 74} {cx - 34} {h - 70} {cx - 42} {h - 70}L{cx - 98} {h - 70}C{cx - 106} {h - 70} {cx - 113} {h - 74} {cx - 114} {h - 82}Z" fill="#1f2b4f"/>
<ellipse cx="{cx - 70}" cy="{h - 196}" rx="50" ry="8" fill="#121a33"/>
<path d="M{cx + 10} {h - 170}L{cx + 104} {h - 170}L{cx + 98} {h - 82}C{cx + 97} {h - 74} {cx + 90} {h - 70} {cx + 82} {h - 70}L{cx + 32} {h - 70}C{cx + 24} {h - 70} {cx + 17} {h - 74} {cx + 16} {h - 82}Z" fill="#f3e8d8"/>
<ellipse cx="{cx + 57}" cy="{h - 170}" rx="47" ry="8" fill="#d8c6aa"/>
<path d="M{cx + 104} {h - 150}C{cx + 136} {h - 150} {cx + 136} {h - 104} {cx + 100} {h - 106}" stroke="#e7d8c2" stroke-width="10" fill="none" stroke-linecap="round"/>"""
        extra = ""
    return f"""<svg viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
<defs><linearGradient id="{pid}bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{bg[0]}"/><stop offset="1" stop-color="{bg[1]}"/></linearGradient>{extra}{gdef}</defs>
<rect width="{w}" height="{h}" fill="url(#{pid}bg)"/>{body}{grect}</svg>"""


def icon(name: str) -> str:
    paths = {
        "lock": '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
        "flame": '<path d="M12 3c1 4 5 5.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-4 2.5-5 .3 2 1.3 3 2.5 3-.5-3 0-5.5 0-8z"/>',
    }
    return (f'<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths[name]}</svg>')


# ----------------------------------------------------------------------------------------------- layout
def head(title: str, html_class: str = "") -> str:
    cls = f' class="{html_class}"' if html_class else ""
    return f"""<!doctype html>
<html lang="en"{cls}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="icon" href="{FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..600;1,9..144,300..600&amp;family=Inter:wght@400;500;600;700&amp;display=swap">
<style>{CSS}</style></head>"""


def nav() -> str:
    return f"""<header class="nav"><div class="nav-inner">
<a class="logo" href="/">{logo_svg()}Lune<small>atelier céramique</small></a>
<nav class="nav-links" aria-label="Main"><a href="/#workshops">Workshops</a><a href="/#how">How it works</a><a href="/#gift">Gift cards</a><a href="/login">Sign in</a></nav>
<a class="nav-cta" href="/#workshops">Book a class</a>
</div></header>"""


def footer() -> str:
    links = "".join(f'<li><a href="/workshops/{slug}">{html.escape(w["title"])}</a></li>' for slug, w in WORKSHOPS.items())
    return f"""<footer class="footer" id="gift"><div class="inner">
<div><a class="logo" href="/">{logo_svg()}Lune</a><p>Pottery workshops in Paris since 2019. Clay, wheels, tea and good company, in a sunlit atelier in the 11th.</p></div>
<div><h4>Workshops</h4><ul>{links}</ul></div>
<div><h4>Studio</h4><ul><li><a href="/#how">How it works</a></li><li><a href="/#gift">Gift cards</a></li><li><a href="/#gift">Private events</a></li><li><a href="/#gift">Journal</a></li></ul></div>
<div><h4>Gift a workshop</h4><p style="margin-top:0">Gift cards from €39, valid 12 months, delivered by email in a minute.</p></div>
</div><div class="legal"><span>© 2026 Lune Atelier SAS · Paris</span><span>Privacy · Terms · Accessibility</span></div></footer>"""


def page(title: str, body: str, html_class: str = "", script: str = "") -> bytes:
    js = f"<script>{script}</script>" if script else ""
    return f"{head(title, html_class)}<body>{body}{js}</body></html>".encode()


# ----------------------------------------------------------------------------------------------- pages
def landing_page() -> bytes:
    avatars = "".join(f'<span style="background:{bg}">{ini}</span>' for ini, bg in [
        ("L", "linear-gradient(135deg,#d98a68,#8e3f28)"), ("M", "linear-gradient(135deg,#8ea487,#4f6a4a)"),
        ("S", "linear-gradient(135deg,#55679c,#1f2b4f)"), ("A", "linear-gradient(135deg,#e2b56b,#a8742a)")])
    cards = []
    for slug, w in WORKSHOPS.items():
        cards.append(f"""<a class="card" href="/workshops/{slug}">
<div class="art">{card_svg(w["art"], "c" + slug[:3])}</div>
<div class="body"><div class="tagrow"><span class="tag">{w["level"]}</span></div>
<h3>{html.escape(w["title"])}</h3><div class="meta">{w["card_meta"]}</div>
<div class="foot"><span class="price">€{w["price"]} <small>/ seat</small></span><span class="rating">★ <b>{w["rating"]}</b> ({w["reviews"]})</span></div></div></a>""")
    body = f"""{nav()}
<section class="hero">
 <div>
  <span class="eyebrow"><span class="dot"></span>Winter season · December workshops are open</span>
  <h1>Slow down.<br>Make it <em>by hand.</em></h1>
  <p class="lede">Small-group pottery workshops in a sunlit atelier in Paris 11e. No experience needed: we bring the clay,
  the wheels and the aprons, then fire and glaze your pieces for you.</p>
  <div class="hero-ctas"><a class="btn btn-dark" href="#workshops">Explore workshops <span class="arrow">→</span></a>
  <a class="btn btn-ghost" href="#gift">Gift a workshop</a></div>
  <div class="proof"><div class="avatars">{avatars}</div><div><span class="stars">★★★★★</span><br><strong>4.9/5</strong> from 2,300+ makers</div></div>
 </div>
 <div class="hero-art">
  <div class="frame">{hero_svg()}</div>
  <div class="float float-quote"><p>“The calmest two hours of my month. I left with two wonky bowls and a huge smile.”</p>
   <div class="who"><span></span>Léa · booked 3 times</div></div>
  <div class="float float-badge"><i>{icon("flame")}</i><div><b>Fired &amp; glazed for you</b>Ready to collect in 3 weeks</div></div>
 </div>
</section>
<section class="press" aria-label="Press"><span class="label">As featured in</span><div class="marks">
<span class="m1">Le Carnet</span><span class="m2">MAISON</span><span class="m3">Paris Weekend</span><span class="m4">atelier revue</span><span class="m5">Le Quotidien</span></div></section>
<section class="section" id="workshops">
 <div class="section-head"><div><div class="kicker">This December</div><h2>Pick your <em>workshop</em></h2></div>
 <p>Eight people at most, two teachers per class and all the tea you can drink. Every piece you make is fired,
 glazed and ready three weeks later.</p></div>
 <div class="cards">{''.join(cards)}</div>
</section>
<section class="section" id="how">
 <div class="section-head"><div><div class="kicker">How it works</div><h2>From a lump of clay <em>to your table</em></h2></div></div>
 <div class="steps">
  <div class="step"><div class="n">01</div><h3>Book a seat</h3><p>Choose a date that suits you. Come alone, with a friend or with your whole team.</p></div>
  <div class="step"><div class="n">02</div><h3>Get your hands dirty</h3><p>A ceramicist guides you at the wheel, step by step, from centring to trimming.</p></div>
  <div class="step"><div class="n">03</div><h3>Collect your pieces</h3><p>We fire and glaze everything in our kiln. Your pieces are ready three weeks later.</p></div>
 </div>
</section>
<section class="quote-band"><div class="inner"><blockquote>“I've tried every pottery class in Paris. <em>Lune</em> is the one where you
actually leave with something you're proud of.”</blockquote><div class="who">Margaux · 6 workshops at Lune</div></div></section>
{footer()}"""
    return page("Lune · Pottery workshops in Paris", body)


def date_strip(w: dict, selected: int | None) -> str:
    """A month calendar: weekday header, then one cell per day (the first one placed on its weekday column)."""
    cols = [f'<span class="dow dow-head">{c}</span>' for c in "MTWTFSS"]
    for day in range(1, 32):
        d = dt.date(YEAR, MONTH, day)
        status = day_status(w, day)
        end = " week-end" if d.weekday() == 6 else ""
        start = f' style="grid-column-start:{d.weekday() + 1}"' if day == 1 else ""
        dow = ""
        if status == "closed":
            chip = f'<span class="day is-closed">{day}</span>'
        else:
            sel = " is-selected" if day == selected else ""
            chip = (f'<div class="day is-{status}{sel}" data-date="{YEAR}-{MONTH:02d}-{day:02d}" '
                    f'data-label="{html.escape(short_label(w, day))}" onclick="pickDate(this)">{day}</div>')
        cols.append(f'<div class="day-col{end}"{start}>{dow}{chip}</div>')
    return "".join(cols)


def workshop_page(slug: str, booking: dict | None, error: str = "") -> bytes:
    w = WORKSHOPS[slug]
    mine = booking if booking and booking.get("slug") == slug else None
    selected = mine["day"] if mine else None
    seats = mine["seats"] if mine else 1
    price = w["price"]
    sum_date = short_label(w, selected) if selected else "Choose a date"
    seat_word = "seats" if seats > 1 else "seat"
    t = w["times"]
    body = f"""{nav()}
<div class="clip"><div class="booking-shell">
<main class="booking-main">
 <div class="crumbs"><a href="/#workshops">Workshops</a> &nbsp;/&nbsp; {html.escape(w["title"])}</div>
 <section class="title-block">
  <div class="tagrow"><span class="tag">{w["level"]}</span><span>{w["duration"]}</span><span>·</span><span>{w["group"]}</span></div>
  <h1>{w["title_html"]}</h1>
  <p class="lede">{w["lede"]}</p>
  <div class="facts"><span>★ <b>{w["rating"]}</b> ({w["reviews"]} reviews)</span><span>Hosted by <b>{w["host"]}</b></span><span>Atelier Lune, Paris 11e</span><span>Clay, firing &amp; glazing included</span></div>
 </section>
 <section class="date-section" aria-label="Choose a date">
  <div class="date-head"><h2>Choose a date</h2><span class="month">December 2026</span>
   <div class="legend"><span><span class="sw open"></span>Available</span><span><span class="sw full"></span>Sold out</span></div></div>
  <div class="date-strip">{date_strip(w, selected)}</div>
  <p class="date-note">Weekday sessions {t[0]}–{t[1]} · weekends {t[2]}–{t[3]} · closed on Mondays and over Christmas</p>
 </section>
 <section class="details">
  <div class="art-panel">{card_svg(w["art"], "wp", 760, 300)}</div>
  <h2>About this workshop</h2>
  <p>{w["lede"]}</p>
  <p>You'll work on your own wheel in a group of eight at most, with two teachers walking the room. Bring clothes you don't
  mind getting a little muddy; we provide aprons, tools, clay and a steady supply of tea.</p>
  <h2>What you'll do</h2>
  <ul class="todo">
   <li><span class="n">1</span><div><b>Wedge and centre</b>Prepare your clay and learn the move everything else depends on.</div></li>
   <li><span class="n">2</span><div><b>Open and pull</b>Shape the walls of two bowls, at your own pace, with one-to-one help.</div></li>
   <li><span class="n">3</span><div><b>Choose your glaze</b>Pick from our eight house glazes. We fire your pieces twice for you.</div></li>
  </ul>
  <h2>Your host</h2>
  <div class="host"><div class="face">{w["host"][0]}</div><div><b>{w["host_full"]}</b><span>{w["host_bio"]}</span></div></div>
 </section>
</main>
<aside class="booking-aside">
 <form class="booking-card" id="booking-form" method="post" action="/book" data-price="{price}" data-max-seats="{MAX_SEATS}">
  <input type="hidden" name="workshop" value="{slug}">
  <input type="hidden" name="date" value="{f'{YEAR}-{MONTH:02d}-{selected:02d}' if selected else ''}">
  <input type="hidden" name="seats" value="{seats}">
  <div class="from"><div class="price">€{price} <small>/ seat</small></div><div class="rating">★ <b>{w["rating"]}</b> · {w["reviews"]} reviews</div></div>
  <div class="card-rows">
   <div class="card-row"><span class="k">Date</span><span class="v{'' if selected else ' is-empty'}" id="sum-date">{html.escape(sum_date)}</span></div>
   <div class="card-row"><span class="k">Seats</span><span class="seats"><span class="seat-count" id="seat-count">{seats}</span><span class="stepper"><button type="button" onclick="stepSeats(-1)" aria-label="Remove a seat">−</button><button type="button" onclick="stepSeats(1)" aria-label="Add a seat">+</button></span></span></div>
  </div>
  <div class="sum">
   <div><span id="sum-line">€{price} × {seats} {seat_word}</span><span id="sum-subtotal">€{price * seats}</span></div>
   <div><span>Booking fee</span><span>€0</span></div>
   <div class="total"><span>Total</span><span id="sum-total">€{price * seats}</span></div>
  </div>
  <div class="reserve-cta" onclick="reserve()">Reserve <span class="arrow">→</span></div>
  <div class="reserve-error" id="reserve-error"{'' if error else ' hidden'}>Please choose a date first.</div>
  <p class="fine">You won't be charged yet</p>
  <div class="perks"><span>Free cancellation up to 48 h before</span><span>Apron, clay and tea included</span><span>Pieces fired &amp; glazed for you</span></div>
 </form>
</aside>
</div></div>
{footer()}"""
    return page(f"{w['title']} · Lune", body, html_class="page-booking", script=BOOKING_JS)


def checkout_page(booking: dict, user: str | None) -> bytes:
    w = WORKSHOPS[booking["slug"]]
    day, seats = booking["day"], booking["seats"]
    d = dt.date(YEAR, MONTH, day)
    start, end = session_time(w, day)
    waitlist = booking["status"] == "full"
    seat_word = "seats" if seats > 1 else "seat"
    if waitlist:
        prices = (f'<div><span>Waitlist request · {seats} {seat_word}</span><span>€0.00</span></div>'
                  '<div class="price-total"><span>Total due today</span><span>€0.00</span></div>')
        status_row = '<dt>Status</dt><dd>Waitlist</dd>'
        notice = ('<div class="notice">This session is full. Confirming adds you to the waitlist; nothing is charged '
                  'unless a seat opens up.</div>')
        pill = '<span class="pill">Waitlist</span>'
    else:
        subtotal = w["price"] * seats
        prices = (f'<div><span>€{w["price"]:.2f} × {seats} {seat_word}</span><span>€{subtotal:.2f}</span></div>'
                  f'<div class="price-total"><span>Total (EUR)</span><span>€{subtotal:.2f}</span></div>')
        status_row, notice, pill = "", "", ""
    who = f'<dt>Booked by</dt><dd>{html.escape(user)}</dd>' if user else ""
    body = f"""<header class="nav nav--checkout"><div class="nav-inner">
<a class="logo" href="/">{logo_svg()}Lune</a><span class="secure">{icon("lock")}Secure checkout</span></div></header>
<main class="checkout">
 <a class="back" href="/workshops/{booking["slug"]}">← Back to the workshop</a>
 <h1>Confirm <em>and pay</em></h1>
 <div class="checkout-grid">
  <section>
   <div class="panel"><h2>Your booking</h2>
    <dl class="kv"><dt>Workshop</dt><dd>{html.escape(w["title"])}</dd>
    <dt>Date</dt><dd>{DOW_LONG[d.weekday()]} {day} December 2026</dd>
    <dt>Time</dt><dd>{start} – {end}</dd><dt>Seats</dt><dd>{seats}</dd>{status_row}
    <dt>Where</dt><dd>Atelier Lune, Paris 11e (address in your confirmation email)</dd>{who}</dl>{notice}
   </div>
   <div class="panel"><h2>Cancellation policy</h2><p>Free cancellation up to 48 hours before your session. After that,
   we'll happily move you to another date.</p></div>
   <div class="panel"><h2>Payment</h2><p>You'll enter your card details on the secure payment step.</p>
    <button class="pay" type="button" disabled>Pay securely</button>
    <p class="fine">Payments are disabled in this preview environment.</p></div>
  </section>
  <aside class="summary-card"><div class="art">{card_svg(w["art"], "co")}</div>
   <div class="body"><h3>{html.escape(w["title"])}</h3>
    <div class="when">{DOW[d.weekday()]} {day} Dec · {start} · {seats} {seat_word}{pill}</div>
    <div class="price-details">{prices}</div>
   </div></aside>
 </div>
</main>"""
    return page("Checkout · Lune", body)


def login_page(next_url: str, error: str = "") -> bytes:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    body = f"""<header class="nav nav--checkout"><div class="nav-inner"><a class="logo" href="/">{logo_svg()}Lune</a>
<span class="secure">{icon("lock")}Secure sign-in</span></div></header>
<main class="auth"><h1>Welcome back</h1><p>Sign in to finish your booking.</p>{err}
<form method="post" action="/login"><input type="hidden" name="next" value="{html.escape(next_url)}">
<label>Email<input name="email" type="email" autocomplete="username"></label>
<label>Password<input name="password" type="password" autocomplete="current-password"></label>
<button class="btn btn-dark" type="submit">Sign in</button></form></main>"""
    return page("Sign in · Lune", body)


def not_found_page() -> bytes:
    body = f"""{nav()}<main class="auth" style="text-align:center"><h1>Lost in the kiln</h1>
<p>This page doesn't exist. <a href="/" style="text-decoration:underline">Back to the workshops</a>.</p></main>"""
    return page("Not found · Lune", body)


# ----------------------------------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "Lune/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[lune] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def _session(self) -> tuple[str, dict]:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        sid = cookie["lune_sid"].value if "lune_sid" in cookie else ""
        if sid not in SESSIONS:
            sid = secrets.token_hex(8)
            SESSIONS[sid] = {"user": None, "booking": None}
        return sid, SESSIONS[sid]

    def _send(self, status: int, body: bytes, sid: str | None = None, location: str | None = None,
              ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        if sid:
            self.send_header("Set-Cookie", f"lune_sid={sid}; Path=/; HttpOnly; SameSite=Lax")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, location: str, sid: str) -> None:
        self._send(303, b"", sid=sid, location=location)

    def _authorized(self) -> bool:
        if not LUNE_TOKEN or self.headers.get("X-Audit-Token") == LUNE_TOKEN:
            return True
        self._send(403, page("Forbidden", "<main class='auth'><h1>403</h1><p>Missing or wrong X-Audit-Token header.</p></main>"))
        return False

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = urllib.parse.parse_qs(self.rfile.read(length).decode())
        return {k: v[0] for k, v in data.items()}

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._authorized():
            return
        sid, sess = self._session()
        url = urllib.parse.urlparse(self.path)
        path, query = url.path.rstrip("/") or "/", urllib.parse.parse_qs(url.query)
        if path == "/favicon.ico":
            return self._send(404, b"", sid=sid)
        if path == "/healthz":
            return self._send(200, b"ok", sid=sid, ctype="text/plain")
        if path == "/":
            return self._send(200, landing_page(), sid=sid)
        if path == "/workshops":
            return self._redirect("/#workshops", sid)
        if path.startswith("/workshops/") and path.split("/", 2)[2] in WORKSHOPS:
            slug = path.split("/", 2)[2]
            return self._send(200, workshop_page(slug, sess["booking"], error=query.get("error", [""])[0]), sid=sid)
        if path == "/checkout":
            if not sess["booking"]:
                return self._redirect("/#workshops", sid)
            if REQUIRE_LOGIN and not sess["user"]:
                return self._redirect("/login?next=/checkout", sid)
            return self._send(200, checkout_page(sess["booking"], sess["user"]), sid=sid)
        if path == "/login":
            nxt = query.get("next", ["/"])[0]
            return self._send(200, login_page(nxt if nxt.startswith("/") else "/"), sid=sid)
        return self._send(404, not_found_page(), sid=sid)

    def do_POST(self) -> None:
        if not self._authorized():
            return
        sid, sess = self._session()
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        form = self._form()
        if path == "/book":
            slug = form.get("workshop", "")
            if slug not in WORKSHOPS:
                return self._redirect("/#workshops", sid)
            w = WORKSHOPS[slug]
            try:
                d = dt.date.fromisoformat(form.get("date", ""))
                assert (d.year, d.month) == (YEAR, MONTH) and day_status(w, d.day) != "closed"
            except (ValueError, AssertionError):
                return self._redirect(f"/workshops/{slug}?error=date", sid)
            try:
                seats = max(1, min(MAX_SEATS, int(form.get("seats", "1"))))
            except ValueError:
                seats = 1
            sess["booking"] = {"slug": slug, "day": d.day, "seats": seats, "status": day_status(w, d.day)}
            return self._redirect("/checkout", sid)
        if path == "/login":
            nxt = form.get("next", "/")
            nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
            if form.get("email", "").strip().lower() == LUNE_USER and form.get("password") == LUNE_PASSWORD:
                sess["user"] = LUNE_USER
                return self._redirect(nxt, sid)
            return self._send(200, login_page(nxt, "Wrong email or password."), sid=sid)
        return self._send(404, not_found_page(), sid=sid)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"Lune demo site on http://{args.host}:{args.port}  (token required: {bool(LUNE_TOKEN)}, "
          f"login required: {REQUIRE_LOGIN})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

# Lune: demo target site (pottery workshops in Paris)

A polished, startup-looking booking site that hides one accessibility barrier per impaired persona. It replaces
the Mugs & Co demo shop for the pitch. Python stdlib only, everything inline (Google Fonts are the only external
request, with system fallbacks).

```bash
.venv/bin/python poc/demo_startup/server.py --port 8770
.venv/bin/python poc/audit.py --preset poc/presets/lune.yaml --runs 1 --lanes 3
# fixes (canned, same shape as Devin's): poc/fixes/lune.json
```

Options (env): `LUNE_TOKEN=tok` requires `X-Audit-Token: tok` on every request (403 otherwise; the preset sends
`${LUNE_TOKEN}`). `LUNE_REQUIRE_LOGIN=1` makes checkout ask for the test account in the preset
(`camille@lune-atelier.test`, local fake account). State is an in-memory cookie session; availability is fixed.

## Journey

Task `book-wheel-throwing` (poc/presets/lune.yaml): book 2 seats for "Wheel throwing for beginners" on the first
available December date, go to checkout, report the total. **Expected answer: Total €98.00** (2 × €49).
Baseline path, about 6 actions: landing → workshop card → first green date (Thu 10 Dec) → `+` → Reserve → read the
total on `/checkout`. The workshop page shows the total as "€98" (no decimals), so only the checkout matches
`98.00`.

Pages: `/` landing, `/workshops/<slug>` (wheel-throwing, hand-building, glaze-lab, date-night), `POST /book`,
`/checkout`, `/login`.

## Traps

| Persona | Barrier (WCAG) | Where | What happens |
|---|---|---|---|
| Colour blind | 1.4.1 Use of Color | date strip | Available `#419f51` and sold out `#cf5f59` differ only by hue (same luminance, identical under Chrome's deuteranopia filter); no text, icon or pattern, colour-only legend. Dec 1–9 are sold out. A sold-out date can still be selected and the booking card says nothing; checkout then shows a **waitlist request, "Total due today €0.00"**. |
| Hand tremor | 2.5.8 Target Size | booking card, date strip | Seat stepper `−` `+` are 14×14 px touching each other (1 px divider); date chips are 18×18 px with 2 px gaps. ±10 px jitter misses often or hits `−` instead of `+`. Date chips are `<div>`s, so there is no keyboard fallback for them. |
| Keyboard only | 2.1.1 Keyboard, 2.4.7 Focus Visible | date strip, Reserve | Date chips and Reserve are `<div onclick>`: never focused, Enter does nothing. `*:focus { outline: none }` site-wide (the landing page styles its own links and cards on `:focus`, so the user does reach the workshop page). |
| Low vision | 1.4.10 Reflow | workshop page | Fixed 1240 px grid inside `overflow-x: clip`, `overflow-x: hidden` on html/body. At 200% zoom (viewport 640 px) the whole booking card (seats, total, Reserve) is cut off; it cannot be scrolled to, and focusing `+` does not scroll either. |
| Low vision | 1.4.3 Contrast | `/checkout` | Price details and total are 12 px `#cdc6bb` on `#fffcf7` (about 1.7:1). |

## Fixes (poc/fixes/lune.json)

Five fixes, one per barrier, each with CSS/JS preview + `code_change`: `date-status-labels` (Open / Sold out text,
stripes, calendar grid), `keyboard-booking-controls` (real `<button>`s, roving tabindex starting on the first open
date, arrow keys, submit button for Reserve, `:focus-visible` ring), `booking-target-size` (44 px stepper,
40 px dates), `booking-reflow` (fluid layout, booking card stacked under the dates below 1000 px),
`checkout-total-contrast`. The JS does not rely on the page's own scripts, so it also works on stripped snapshots.

Checked with real CDP input events (no H sessions): baseline reaches €98.00 before and after the patch; keyboard
only can never focus a date or Reserve before, and books in 9 + 11 key presses after; at 200% zoom the Reserve
button sits at x = 847 in a 640 px viewport before (no scroll possible) and the full journey works after; the
first chip leads to the €0.00 waitlist checkout. Tremor, 200 jittered clicks (±10 px, like the bridge) each:
`+` lands 44% of the time, hits `−` 7.5%, misses 48.5%; the Dec 10 chip lands 70%, misses 30%. After the patch:
100% on both. Expect tremor to cost extra clicks more often than it fails the task.

# Humble: demo script

Target length: 2:30 video, 3 min live pitch. Every number below comes from real runs (see "Recorded results").

## Setup before recording (5 minutes)

```bash
cd H-For-Humble
.venv/bin/python poc/demo_startup/server.py --port 8770 &      # Lune, the designed demo startup
.venv/bin/python poc/demo_site/server.py --port 8765 &         # Mugs & Co, the minimal demo shop (optional)
npm run build && npm start                                     # app on http://127.0.0.1:8000
```

- Run `.venv/bin/python scripts/preflight.py`: app, demo sites, Chrome, H slots, and any H session stuck in
  "running" (stop those in H Agent View first, they hold slots). 10 free slots = 5 agents in parallel.
- Keep finished audits open in tabs as a fallback (a Lune audit, its re-evaluation with fixes, mousquetaires.com),
  so the story never depends on a live run finishing on time.

## Plan B (if H is slow or sessions hang)

Overnight, H sometimes never started the first step of sessions launched seconds apart (they stay "running" at
step 0 and hold slots). Humble starts agents one after another, detects a session without a first step, retries
once and lists stuck sessions with their Agent View links. If it happens on stage:
1. Say it: "H didn't start this agent; Humble detected it and retried" (that is the reliability story).
2. Switch to a finished audit tab: score, replays and before/after work fully offline.
3. The sample reports in `docs/` open in any browser without the app.

## The story

**1. Problem (15 s).** One adult in four in the EU lives with a disability. Since June 2025 the European
Accessibility Act requires e-commerce, banking and transport services to be accessible. Teams run static checkers
that list rule violations, but nobody can tell them the thing that matters: *can a person with low vision, a tremor
or no mouse actually complete a purchase?*

**2. What Humble does (15 s).** Paste a URL and a user journey. Humble sends H computer-use agents to try it as five
different people *at the same time*: a baseline user, low vision (200% zoom + blur), colour blindness
(deuteranopia), a hand tremor (every click lands up to 10 px off) and keyboard only. The impairment is applied to the
browser itself (Chrome rendering and input filtering), not just described in a prompt.

**3. Launch (20 s).** New audit on Lune (`http://127.0.0.1:8770/`), journey "Book 2 seats for the wheel throwing
workshop on the first available date in December and report the total". Show the perspective picker and the
"5 agents can run in parallel" note. Launch.

**4. Mission control (25 s).** Five live browsers side by side, each with the agent's current thought
("Focus is on the + button, pressing Enter…"). Point at keyboard-only tabbing through the page, low vision zoomed in
and blurred, colour blind looking at red/green dates that look the same to it.

**5. The verdict (25 s).** Inclusion score and the one-sentence verdict ("2 of 5 perspectives completed… Blocked:
keyboard only, colour blindness, low vision"). Effort chart: low vision needed 3× the steps. Then the static checker
card: *axe-core flags N rules on these pages; our agents found 3 perspectives blocked, 2 of which no rule detects.*

**6. Replays (20 s).** Open the keyboard-only replay: keycaps, "Mouse blocked", the agent's own words when it gives
up. Open the tremor replay: the target ring and the red dot where the click actually landed.

**7. Fixes, written by Devin (25 s).** Fixes tab: drag the before/after slider "through their eyes" (dates with "Sold out" labels as
the colour-blind user sees them; Reserve button reachable and focused for keyboard users). Click "Evaluate again
with fixes" (or open the finished re-evaluation): with Devin's fixes Lune goes 49 → 86, every perspective
completes; a second Devin round fixed what the first one missed (the calendar was 25 Tab stops long).

**8. Real website (15 s).** mousquetaires.com: "Suggest journeys" (Linkup finds what visitors do there and the
site's own accessibility statement: *partially compliant, RGAA 4, self-assessed*), then the finished audit with the
declared-vs-measured card.

**9. Close (10 s).** "H agents don't just click: they let you *feel* your product through someone else's hands and
eyes. Humble turns that into a score, replays and fixes your team can ship."

## Tech in one breath (for Q&A)

- H Agents API with `host: user_device`: the agent drives a Chrome we host through H's local bridge. We subclass the
  bridge: tremor jitter on every pointer command, mouse commands refused for keyboard-only, coordinates scaled for
  zoom; Chrome DevTools emulates blur and deuteranopia and injects fix previews.
- Structured output (`answer_schema`) for each run's report; live captions from the session changes feed; replays
  rebuilt from H's session history (screenshots, thoughts, actions) plus what our bridge did to each action.
- Parallel lanes, one H browser channel each; watchdog and one retry for stalled sessions; our own pending channels
  are recycled while H still holds their slots.
- Linkup: journey suggestions and the site's declared accessibility. axe-core: the static comparison.
  Fixes: Devin API when configured, hand-written for the demo sites, a rule engine for any site.

## Recorded results

- **Devin's fixes, verified by the agents**: Lune 49 → 74 (round 1) → **86, all five complete** (round 2);
  mousquetaires.com 58 → **79, all five complete** (keyboard-only and low vision unblocked on a real site).
- **Lune** (final version, first open date Tue 8 Dec): **49/D**. Baseline 6 steps, tremor completes; colour blind books a
  sold-out date, discovers the waitlist at checkout and needs 15 steps; low vision and keyboard-only are blocked.
  axe-core: 4 rules, none for those blockers. **With the 4 fixes: 85/B, all five perspectives complete.**
- **Mugs & Co**: 39/F with 5 agents in parallel; with fixes 40 → 100; "Fix until it passes" 33 → 100 in 2 rounds.
  axe-core flags 1 rule while 3 perspectives are blocked.
- **mousquetaires.com**: declared "partially compliant with RGAA 4.1.2 (69%)", self-assessed (Linkup);
  measured: everyone finds the Roady figure, but low vision needs 4.7× the steps.

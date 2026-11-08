# Humble

**See your site through five different users.** Humble sends H computer-use agents to run your key user journeys
*in parallel*, each in a real Chrome with an impairment applied to the browser itself: low vision (200% zoom +
blur), colour blindness (deuteranopia), a hand tremor (every click lands up to 10 px off) and keyboard only
(the mouse is refused). A baseline agent runs the same journey as the reference. You get an inclusion score,
a step-by-step replay of where each user got stuck, the barriers they reported, what a static checker would
(and would not) have told you, and fixes you can preview "through their eyes" and re-test with the same agents.

Built by **H For Humble (Théophile + Mattéo)** for the H Company × 42AI Computer Use Agents Hackathon, Paris,
November 2026.

> Why now: one adult in four in the EU lives with a disability, and since June 2025 the **European Accessibility
> Act** requires e-commerce, banking and transport services to be accessible. Static checkers list rule
> violations; they cannot tell a team whether a person with low vision, a tremor or no mouse can actually finish
> a purchase. Humble answers exactly that, with agents that operate the real UI.

## What it does

| | |
|---|---|
| **5 agents in parallel** | One H agent and one dedicated Chrome per perspective, side by side. While they run, a live grid shows each browser and the agent's current thought ("Focus is on the + button, pressing Enter…"). Pause, resume and stop at any time, or steer one agent with a hint (the run is then marked as assisted). |
| **Impairments enforced, not role-played** | Chrome DevTools emulates blur and deuteranopia and zooms the page to 200% (media queries react); our subclass of H's browser bridge jitters every pointer action for tremor and refuses mouse commands for keyboard-only. |
| **A score, not just pass/fail** | Each run scores 0 if the user could not finish; a finished journey loses points for extra steps and time compared with the baseline user, retried or blocked actions, and reported barriers. Perspectives and audits are averaged and graded A–F. |
| **Replays** | Every run is rebuilt from H's session history: the screenshot the agent decided on, its thought, its action, the click target and where a tremor click actually landed, blocked mouse actions, key presses. Plays like a video. |
| **Static checker vs real journeys** | axe-core runs on every page the agents visited. On our demo shop it flags 1 rule; the agents find 3 perspectives that cannot check out, 2 of which no rule detects. |
| **Linkup research** | Suggests the journeys visitors come for on any public site, and reads the site's own accessibility statement (e.g. mousquetaires.com: *partially compliant with RGAA 4.1.2, 69% of criteria met, self-assessed*, with its known keyboard issues) to show **declared vs measured**. |
| **Fixes, before/after, re-test** | One fix per barrier (WCAG criterion, preview CSS/JS, the real code change). A large before/after slider renders the page as the blocked user sees it. **Evaluate again with fixes** re-runs every agent with the fixes injected; **Fix until it passes** loops audit → fix → audit automatically. Fixes come from Devin (API) when configured, hand-written fixes for the demo sites, or Humble's rule engine for any site. |
| **Shareable report** | One self-contained HTML file per audit (screenshots, replays, before/after embedded) that opens anywhere, offline. |

## Quick start

Requires Python 3.10+, Node.js 20.19+ (or 22.12+), Google Chrome (or Chrome for Testing) and an H Agents API key.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
cp .env.example .env          # then set HAI_API_KEY=hk-... (and optionally LINKUP_API_KEY, DEVIN_API_KEY + DEVIN_ORG_ID)
# scripts/devin_fixes.py <audit-id>  asks Devin for the fixes of a finished audit (--from <re-evaluation-id> for round 2)
npm run build
npm start                     # http://127.0.0.1:8000
```

Chrome is detected automatically (Chrome for Testing cache, macOS/Linux system paths); otherwise set
`AUDIT_CHROME=/path/to/chrome` in `.env`. Never commit `.env`.

Two deliberately inaccessible demo sites are included:

```bash
.venv/bin/python poc/demo_startup/server.py --port 8770   # Lune: pottery workshops in Paris (designed startup site)
.venv/bin/python poc/demo_site/server.py --port 8765      # Mugs & Co: minimal shop with a login
```

## How judges can test it live

1. Do the quick start, start Lune (`poc/demo_startup/server.py --port 8770`) and open http://127.0.0.1:8000.
   Create a local account.
2. Click **Try it on Lune, our demo startup** under the home URL field: the journey is filled in (*book 2 seats
   for "Wheel throwing for beginners" on the first available date in December and report the total*; expected
   answer `98.00; 8 Dec|8 December|December 8`, the total *and* the first open date). Keep the five perspectives.
   Launch.
3. Watch the five agents work side by side (click a live tile to watch one full size, or open it in H Agent View).
   An audit takes 2 to 6 minutes.
4. Read the verdict, open the replays (keyboard-only gives up on the date picker; low vision cannot reach the
   booking card at 200% zoom; colour blind books a sold-out date), then the **Fixes** tab: drag the slider, and
   click **Evaluate again with fixes**.
5. Try a real site: **Audit this site** with `https://www.mousquetaires.com/` and **Suggest journeys** (Linkup).
6. No time to run it? Open the sample reports in [`docs/`](docs/) in a browser (real audits exported from Humble,
   with their replays), or watch [`docs/montage-lune.mp4`](docs/montage-lune.mp4) /
   [`docs/montage-demo-shop.mp4`](docs/montage-demo-shop.mp4): five agents' replays side by side (`scripts/montage.py <audit-id>` makes one from any finished audit).

Before a live demo, run `.venv/bin/python scripts/preflight.py`: it checks the app, the demo sites, Chrome, the
H slots and lists any H session stuck in "running" (stop those in H Agent View).

Each perspective uses 2 H concurrency slots (its browser channel and its agent), so 10 free slots run all five at
once; with fewer, the remaining agents wait for a slot and start automatically.

## How it works

```text
React/TS/Vite UI ── same-origin API + Server-Sent Events ──► FastAPI + SQLite (accounts, audits, events, vault)
                                                                 │ spawned worker per audit
                                                                 ▼
                         one lane per perspective, in parallel (threads), each lane:
   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
   │ Chrome (own profile, own port) ◄── DevTools: zoom, blur/deuteranopia, headers, fix previews,  │
   │                                     page snapshots, live screenshots                          │
   │ PersonaBridge (subclass of H's local browser bridge): tremor jitter, keyboard-only filter,    │
   │                                     zoom coordinate scaling, action log                       │
   │ H agent session (Agents API, host: user_device): structured RunReport answer, custom tool     │
   │                                     for logins (the model never sees secrets)                 │
   └───────────────────────────────────────────────────────────────────────────────────────────────┘
        after each run: replay from H's session history · scores · axe-core scan · Linkup context
        after the audit: fixes (Devin / hand-written / rule engine) → before/after renders → re-test
```

Code map: `poc/runner.py` (one run: Chrome + bridge + H session, watchdog, channel pool), `poc/personas.py`,
`poc/chrome.py`, `poc/replay.py`, `poc/scoring.py`, `poc/research.py` (Linkup), `poc/static_scan.py` (axe-core),
`poc/fixes.py` + `poc/autofix.py` (fixers), `poc/render.py` (before/after), `poc/export_report.py`,
`app/jobs.py` (parallel lanes, rounds), `app/main.py` (API), `web/` (UI). The original command-line pipeline still
works: `poc/audit.py`, `poc/pipeline.py`.

## Reliability

Tested end to end with real H sessions (5 agents in parallel on Lune, Mugs & Co and mousquetaires.com).

- **Watchdogs**: a session with no first step after 90 s, or no new step for 3 minutes, is stopped and retried
  once on a fresh browser channel. Two failing cases stop the audit so it never burns more H slots.
- **H slots**: H keeps a finished browser channel (and its slot) for ~25–30 minutes. Humble checks the quota before
  each run, waits (visibly) for free slots, and recycles its own channels released in the last 10 minutes, so a
  re-evaluation right after an audit can start immediately.
- **Isolation**: one Chrome profile and port per lane; a crash in one lane does not stop the others; every cleanup
  step runs on its own; nothing the agent types is a secret (logins go through a custom tool).
- **Tests**: `npm test` (API, worker, scoring, research, fixes: no H calls) and `scripts/browser_smoke.py`
  (UI flows, mobile layout and axe-core WCAG checks of Humble's own interface).

## Partner integrations

- **H Company** (core): Agents API sessions on our own Chrome (`host: user_device`), structured output, custom
  tools, live changes feed, session history for replays, Agent View links.
- **Linkup**: journey suggestions (`/v1/search`, structured output) and the declared accessibility of the site
  (`/v1/fetch` of its accessibility statement).
- **Devin** writes the fixes (`DevinFixer`, API v3 with structured output, `devin_mode: fast` by default): it gets
  the journey, what each struggling perspective reported and the pages they saw, and returns one previewable fix per
  barrier plus the real code change. A second round gets what still fails after the first fixes. On Lune, Devin's
  first 6 fixes took the score from 49 to 74 (low vision and colour blind unblocked) in about 5 minutes. If Devin is
  not configured or fails, hand-written fixes (demo sites) and the rule engine (any site) are used.
- **axe-core** (open source) for the static comparison.

## Privacy and interpretation

Humble accounts use scrypt-hashed passwords and HttpOnly SameSite session cookies; the API checks ownership for
every report, image, control and export. Run it on loopback. One-time website credentials stay in process memory;
credentials saved in the **Credential vault** are Fernet-encrypted in SQLite (`data/vault.key`, or
`HUMBLE_VAULT_KEY`). Logins are typed by our code through a custom tool, so the H model never receives secret
values; headers are added by the browser. Screenshots and reports can contain site content: use test accounts.

These are **simulated user journeys, not an accessibility certification or user research**. Agents are not people;
they show where real people are likely to struggle and give a repeatable, comparable measure. Technical harness
errors are excluded from scores and shown separately.

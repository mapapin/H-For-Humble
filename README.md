# Humble

**A different perspective on your product.** Humble is a local web application for synthetic usability and
accessibility testing. Give it a website, a test account and a user journey. H computer-use agents attempt
that journey in real Chrome browsers with different visual and motor constraints, then compare outcomes.

Built by **H For Humble — Théophile + Mattéo**, for the H Company × 42AI Computer Use Agents Hackathon.

## What works

- Personal accounts with private project and audit history, stored in SQLite.
- Three-step audit setup: website and access, user journeys, personas and repetitions.
- A separate personal credential vault: encrypted logins/headers, website binding and reusable audit links.
- Five perspectives: baseline, low vision (200% zoom + blur), deuteranopia, hand tremor (±10px), keyboard only.
- Real H sessions driving Chrome on the local machine. No installation on the audited website.
- Live browser snapshots, action count, activity feed and H Agent View links.
- Pause/resume the run queue; stop requests cancellation of the active H session and skips remaining runs.
- Statistics first: passed/failed counts, reported barriers, navigation steps and agent time.
- Click any perspective to inspect its individual journeys and repetitions, with the matching live/final preview.
- One audit at a time, one perspective at a time, to respect the H concurrency quota.
- **Suggested fixes** after each audit: one fix per barrier (WCAG criterion, page, preview CSS/JS and the real
  code change), with **before/after sliders** rendered as each perspective sees the page. **Evaluate again with
  fixes** re-runs every perspective with the fixes injected and compares scores (manual, it takes a full audit).
- **Fix until it passes** (auto mode): audit → fix → re-audit, until every perspective passes or the round limit.

The English interface uses an ivory and indigo theme, responsive layouts and keyboard navigation.

## Setup

Requires Node.js 20.19+ (or 22.12+), Python 3.10+, Google Chrome, and an H Agents API key.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
cp .env.example .env
```

Set `HAI_API_KEY=hk-...` in `.env`. Keep the `HAI_API_KEY=` prefix; a bare key is not loaded.
Never commit `.env`. The key stays on the server.

Chrome is detected at the existing POC Linux path, the standard macOS Google Chrome path, or Linux system paths.
For a different installation, set `AUDIT_CHROME=/absolute/path/to/chrome` in `.env`.
Selenium Manager downloads the matching ChromeDriver on first use; allow network access for that setup.
If an old driver is on your PATH, set `SE_SKIP_DRIVER_IN_PATH=true` in `.env`.

## Run locally

```bash
npm run build
npm start
```

Open **http://127.0.0.1:8000** and create an account. Use a new password for this local application.
Do not run multiple API workers: the local audit coordinator is intentionally a single process.

For frontend development, run `npm start` and `npm run dev` in separate terminals, then open
http://127.0.0.1:5173. Vite proxies API requests to port 8000. Rebuild and restart the API after changing
the production frontend or server code.

## How judges can test it live

1. Complete setup, build and start the application as above.
2. In another terminal, start the deliberately inaccessible demo shop:
   ```bash
   .venv/bin/python poc/demo_site/server.py --port 8765
   ```
   Leave `DEMO_TOKEN` unset for this walkthrough. If the shop was started with a token, include its
   `X-Audit-Token` under advanced access headers.
3. Create a Humble account, click **New audit**, and enter:
   - Audit name: `Checkout from every perspective`
   - Website: `http://127.0.0.1:8765/`
   - Enable website login. Use the **demo-only** account `alex@demo-shop.test` / `mug-lover-2026`.
4. Add a journey named `Buy an in-stock blue mug` with this goal:
   > Sign in, add one Blue Mug that is currently in stock to your cart, go to the checkout page,
   > and report the order total shown there. Stop before placing an order.
   Set the expected answer to `19.90`.
5. Keep **Baseline** and **Keyboard only** selected, deselect the others, and use one repetition.
   This makes two **sequential** runs, sharing one browser channel.
6. Launch the audit. Watch browser snapshots, open H Agent View, and inspect the activity tab.
7. When it finishes, compare success and effort, open **Evidence**, inspect screenshots and export JSON.
   The demo checkout button cannot be reached by keyboard, so this persona should reveal a barrier.
8. Return to **Audit history**; the report remains available after signing out and back in.
   A different account has its own empty history.

For broader exploration, select all five personas. Each run allows 35 steps and up to 6 minutes,
plus browser startup and H scheduling overhead. Use more repetitions to assess variability.

## Fix workflows

Each run saves the rendered HTML of every page the perspective saw. When an audit finishes with failures, a
fixer turns the barriers and those pages into a patch: one fix per barrier, with a **preview** (CSS and/or JS
injected into the page by our Chrome, never into the real site) and the **code change** a developer should make.

- **Fixer**: Devin (API v3, structured output) when `DEVIN_API_KEY` and `DEVIN_ORG_ID` are set in `.env`;
  otherwise hand-written fixes for the demo shop in the same format (`poc/fixes/demo_shop.json`), only for
  perspectives that failed and only on that site.
- **Before/after**: the saved page is re-rendered as the persona sees it, without and with the fix, cropped
  around what changed. Keyboard-only and tremor views add an overlay (Tab order and unreachable controls;
  targets under 24x24px), since those barriers are invisible in a plain screenshot.
- **Evaluate again with fixes** creates a linked audit with the patch injected in every page and shows
  before → after scores. Manual credentials are never stored, so they are asked again.
- **Fix until it passes** runs rounds in one audit; the Fixes tab shows the score per round and the final fixes.

The same pipeline runs from the command line: `.venv/bin/python poc/pipeline.py --help`.

## H session quota and stopping

The tested free-plan quota is **3 concurrent slots**. A self-hosted browser channel consumes one,
and its agent consumes another. Humble runs **one lane**, keeping the channel for subsequent personas.
It checks quota before opening a channel. Other audits on this local server are rejected while one is active.

**Pause queue** allows the current run to finish and prevents the next one from starting. **Stop** requests
cancellation through the H SDK; the bridge continues serving while the session finishes. Stopping can take
time, and partial results are retained. If the server is restarted during an audit, the history records an
interrupted audit as a technical error; check its H Agent View before retrying.

H browser channels can remain pending until H's periodic cleanup after the audit. A successful agent
cancellation does not guarantee that the channel slot is released immediately. If the quota is occupied,
wait for H cleanup rather than repeatedly launching new audits. See [POC notes](poc/README.md) for details.

## Privacy and interpretation

Passwords for Humble accounts are salted and hashed with scrypt. Login sessions use random, HttpOnly,
SameSite cookies; only token hashes are stored. The API checks ownership for reports, events, controls,
exports and images. Run the app on loopback; this hackathon setup is intended for local use.

One-time website credentials travel to the local backend and spawned worker in memory. They are not stored
in project configuration or reports. Credentials explicitly saved in **Credential vault** are encrypted with
Fernet authenticated encryption in SQLite. Only metadata (name, origin and credential types) is returned to
the interface. A custom tool types credentials into the browser; the H model
does not receive their values as prompt text. Headers are injected by the browser. Site content, usernames
shown on pages, screenshots and reports can still contain sensitive information: use test accounts.

Data lives in ignored `data/`: SQLite plus audit screenshots and run records. The vault encryption key is
generated as `data/vault.key` with owner-only permissions; preserve this key alongside database backups.
You can instead provide a Fernet key through `HUMBLE_VAULT_KEY` before the vault's first use. Existing vault
entries require the same key to be unlocked.

To reuse a login, open **Credential vault → Add credentials**, enter its website URL and test login, then
choose **Use credential vault** in the audit setup. The selected entry must match the website's exact origin
(protocol, hostname and port). **Run again** retains the link. Entries can be replaced or deleted; old reports
keep only the entry ID/name and never its password. One-time manual access remains available.

H processes the agent session in its cloud; the app and Chrome are local.

These are **simulated user journeys, not accessibility certification or human user research**. Rates are
observed outcomes for the selected sample. Technical harness errors are excluded from success rates and
shown separately. A failed baseline makes comparisons harder to interpret.

## Architecture

```text
React / TypeScript / Vite
        │ same-origin API + SSE
FastAPI ├── SQLite: users, sessions, projects, audits, events
        └── spawned worker (one audit at a time)
                └── runner.Lane → runner.run_one()
                        ├── dedicated Chrome + DevTools persona emulation
                        ├── PersonaBridge: input constraints + action log
                        └── H Agents SDK: session, structured answer, cancellation
```

The original CLI remains available: `.venv/bin/python poc/audit.py --help`.

## Validation

```bash
npm test           # isolated API tests; no H calls or credits
npm run build      # TypeScript and production bundle
.venv/bin/python scripts/browser_smoke.py
```

The browser smoke test requires the local app to be running. It creates an isolated review account,
checks desktop/mobile UI, vault creation/reuse, multi-journey case selection, report tabs, search, export and sign-out, and saves screenshots under
ignored `data/qa/`. It seeds a clearly labelled **UI test fixture** report only in that review account and
runs axe WCAG checks. It creates no H run.

Optional paid integration test, with both app and demo shop running:

```bash
.venv/bin/python scripts/live_smoke.py
```

This creates one real audit with two sequential personas using the configured H API key.

## Hackathon partner integrations

H computer use is implemented in the product. Devin-generated fixes are wired in (`poc/fixes.py`,
`DevinFixer`) and used when a Devin API key is configured; until then the demo stand-in fixes are used.
Linkup journey suggestions are planned, not wired in yet. Before submission, integrate one required partner or document
the team's actual Devin development session/PR; using Codex alone does not satisfy that partner rule.

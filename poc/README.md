# Proof of concept: H agents as simulated users with impairments

Goal: check that the core of the product works before building the web portal. The core is: paste a site and its
tasks, then H agents try them as different users (baseline, low vision, colour blind, tremor, keyboard only), and
we compare how each persona does against the baseline.

## How it works

```
audit.py ── runs spread over N lanes (one process each, in parallel) ──► runner.run_one() per run
            each run: fresh Chrome + fresh bridge + fresh H channel   │
   1. Chrome for Testing, own profile, own port  │  chrome.ChromeProcess
      zoom = smaller window + device scale factor│  (CSS viewport shrinks like Ctrl+, media queries react)
   2. DevTools connection kept for the whole run │  chrome.DevToolsController
      - vision deficiency emulation (blur, deuteranopia…) on every tab, before first paint
      - extra HTTP headers on every request (staging gateways, X-Audit-Token, Bearer…)
   3. H "your browser" bridge on that port       │  runner.PersonaBridge (subclass of hai_agents_local.SeleniumBrowserBridge)
      - every agent action passes through _dispatch: tremor jitter, keyboard-only blocking, zoom coordinate scaling,
        action log for metrics
   4. H agent session (Agents API, Holo model)   │  environment {"kind": "web", "host": "user_device", "session_id": <bridge>}
      - structured answer (RunReport: completed, answer, obstacles[], summary)
      - login: `fill_credential` custom tool; our code types the secret, the model never sees it
```

Everything runs on our machine or server, and the person being audited installs nothing. The H agent and the Holo
model run in H's cloud. Only the browser is ours, because H's cloud browser can't be zoomed, filtered or given
headers.

## Run it

```bash
.venv/bin/pip install -r poc/requirements.txt
# Chrome for Testing + matching chromedriver (no sudo), cached in ~/.cache/selenium:
.venv/lib/python3.12/site-packages/selenium/webdriver/common/linux-x86_64/selenium-manager --browser chrome --force-browser-download
# Demo shop (deliberately inaccessible), with header auth on:
DEMO_TOKEN=tok123 .venv/bin/python poc/demo_site/server.py --port 8765 &
# Audit (HAI_API_KEY from .env):
DEMO_TOKEN=tok123 .venv/bin/python poc/audit.py --preset poc/presets/demo_shop.yaml --runs 2 --lanes 3
```

`--lanes`: parallel runs. Each run needs 2 H session slots, and a used channel keeps its slot until H's cleanup,
so with our 10 slots use 3 lanes (the runner waits for free slots on its own). On the free plan (3 slots), use 1.

Output: `poc/runs/<timestamp>/report.html` (table + one card per run with obstacles, final screenshot as the persona
saw it, and an Agent View replay link) and `results.json`.

Presets (`poc/presets/*.yaml`) hold the site, how to get in (form login and/or headers, `${ENV}` expansion) and the
customer's own tasks, with an optional expected answer for automatic grading.

## Fix workflows (pipeline.py)

```bash
.venv/bin/python poc/pipeline.py suggest --audit poc/runs/<dir>      # patch + before/after images + fixes.html
.venv/bin/python poc/pipeline.py reevaluate --audit poc/runs/<dir>   # same audit with the patch injected
.venv/bin/python poc/pipeline.py auto --preset poc/presets/demo_shop.yaml --max-rounds 2   # fix until it passes
```

`fixes.py` (patch format, Devin and canned fixers), `render.py` (snapshot re-rendering, overlays, crop) and
`pipeline.py` (workflows, scores, HTML report with sliders) are also what the web app's worker calls.

## Results (2026-11-07, demo shop, task "buy an in-stock blue mug and report the checkout total")

Expected answer €19.90. Each trap in the demo shop is described at the top of `demo_site/server.py`.
Reference audit: `--runs 2 --lanes 3`, 10 sessions, 11 minutes, $0.47 total. All 10 runs went through our bridge:
no stall, no harness error, no session left running.

| Persona | Success (2 runs) | Mean steps (×baseline) | What the agent reported |
|---|---|---|---|
| Baseline | ✅ 2/2 (€19.90) | 8 (×1) | Picked the in-stock "Large" mug, no obstacles |
| Colour blind (deuteranopia) | ❌ 0/2 | 8 (×1) | "Stock status only by coloured dots… indistinguishable to a deuteranope", so it ordered the backordered mug (WCAG 1.4.1) |
| Low vision (200% zoom + blur) | ❌ 0/2 | 26 (×3.3) | "Checkout button off-screen to the right at 200% zoom": scroll, arrows and Tab all failed (WCAG 1.4.10 reflow) |
| Keyboard only | ❌ 0/2 | 23.5 (×2.9) | "Proceed to checkout is not keyboard-accessible" (a `<div onclick>`, WCAG 2.1.1). Did everything else with Tab/Enter; its mouse attempts were blocked by our bridge |
| Hand tremor (±10 px) | ✅ 2/2 | 8 (×1) | Every click jittered and still landed: ±10 px is not enough for these targets |

Each persona fails at the trap built for it, and the agent's own words name the cause well enough to map it to a
WCAG criterion. Across all our valid runs the baseline succeeded every time.

## What is de-risked

- ✅ **H agent driving a Chrome we host** (`host: user_device` + `SeleniumBrowserBridge` on our own port). The
  person being audited installs nothing.
- ✅ **Impairments the agent actually perceives**: Chrome's vision-deficiency emulation shows up in the agent's
  screenshots. 200% zoom is a smaller window plus device scale factor 2, so responsive layouts react like
  real zoom, and we scale click coordinates back. Tremor and keyboard-only are enforced in the bridge, not just
  asked of the agent.
- ✅ **Header auth** (staging behind a gateway): `Network.setExtraHTTPHeaders` on every tab. The demo shop
  returns 401 without it.
- ✅ **Form login without the model seeing the password**: `fill_credential` custom tool; our code types the
  value into the focused field.
- ✅ **Structured results** (`answer_schema`) and automatic grading (`expect_answer_contains`).
- ✅ **Customer-defined tasks** as YAML presets.
- ⚠️ **H's vault itself**: the docs say it only works on H's cloud browser. The driver has a vault hook
  (`attach_vault`, `fill_secret_at`, `has_vault`), and `--login-mode vault` tests whether the agent uses it on
  our browser. Not confirmed yet. The `fill_credential` tool gives the same guarantee meanwhile.
- ❌ Not built yet: Linkup task suggestions (we need a Linkup key), fix suggestions + Devin, and the web portal.

## H platform gotchas (cost us hours, read this)

1. **Each self-hosted browser channel is itself a trajectory that holds a session slot.** A run needs 2 slots
   (channel + agent session). Opening a channel at capacity returns **429**, which looks like a rate limit but is
   the slot limit, and every retry counts. `runner.wait_for_slots` waits for 2 free slots before opening one.
2. **Channels are not closed when the session ends.** They stay `pending` (holding the slot) until H's periodic
   cleanup marks them `failed` (between about 5 and 30 minutes later). There is no close endpoint: v2 cancel and
   v1 PATCH accept the request and change nothing. H raised our limit from 3 to 10 slots for the hackathon.
3. **Use a fresh channel per run.** Reusing one channel for the next session (`--reuse-channel`, kept for
   slot-starved plans) works most of the time, but H sometimes stops sending commands to the new session right
   after its first screenshot. We saw it in about 1 of 9 reuses, even after waiting for the previous session to
   end plus a 20 s grace period. It never happened on a fresh channel. A `failed` channel delivers nothing to a
   new session, and two sessions on one channel break both.
4. **A session waiting on a dead bridge can't be cancelled through the API**, and its own `max_time_s` doesn't
   fire either: it stays `running` and holds a slot until someone stops it from the H web UI. So: never stop a
   bridge before its session is terminal (`wait_terminal`), and close finished sessions (they end `idle`, holding
   a slot) with an explicit cancel. The watchdog (`wait_with_watchdog`) flags a run with no new step for 3
   minutes, prints the session id to stop in the web UI, and retries the run once on a fresh channel.
5. **The SDK's `wait_for_completion` long-polls for 20 s.** Polling with a shorter timeout raises
   `httpx.ReadTimeout`, which means "no news yet", not a failure.
6. **If you reuse a bridge, clear its result cache.** It dedups commands by `command_uid`; we clear it as a
   precaution.
7. Agent names: lowercase letters, digits and hyphens only (no `_`).
8. Parallel processes must not all ask Selenium Manager for a chromedriver at once ("Text file busy"), so we put
   ours on PATH. A Chrome left on a lane's port by a crashed run is killed before the next run starts.
9. `runs/<timestamp>/lane-N.log` lists every command the bridge received (names only, never arguments). That
   log is how you tell "H stopped sending" from "our side hung".

Session cleanup: each run requests cancellation and verifies a terminal state before releasing its browser.
Channel cancellation is also attempted and checked; H may still retain channel slots until its own cleanup.

### Night of 2026-11-07/08 (5 agents in parallel in the app)

- **Sessions stuck at step 0.** From about 00:50 CET, many sessions started a few seconds apart received their
  first observation and then never got a model step: status `running`, `steps: 0`, for hours. Neither
  `cancel_session`, `pause_session` nor `force_session_answer` ends them; they hold their slot until stopped from
  the H web UI. Single sessions started fine at the same time (both `holo4-27b` and `holo4-35b-a3b`). Taking over the stuck session's browser channel with a new session (which H's docs say cancels the earlier one) did not end it either. Humble now
  starts agents one after another (the next waits for the previous one's first step, at most 40 s), detects a
  session without a first step after 90 s, retries it once on a fresh channel, and lists stuck sessions with their
  Agent View links in the report so they can be stopped.
- **Channel slots still leak** (~25–30 min after the run). Humble recycles its own channels: a pending channel can
  be bound again by a new bridge + session (1 new slot instead of 2). Channels released less than 10 minutes ago
  worked; 3 of 5 channels idle for ~30 min served 2 commands and went silent, so older ones are not reused.

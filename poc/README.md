# Proof of concept: H agents as simulated users with impairments

Goal: check that the core of the product works before building the web portal. The core is: paste a site and its
tasks, then H agents try them as different users (baseline, low vision, colour blind, tremor, keyboard only), and
we compare how each persona does against the baseline.

## How it works

```
audit.py ── for each (task × persona × run) ──► runner.run_one()  (one process per run)
                                                 │
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
DEMO_TOKEN=tok123 .venv/bin/python poc/audit.py --preset poc/presets/demo_shop.yaml --runs 1 --lanes 1
```

Output: `poc/runs/<timestamp>/report.html` (table + one card per run with obstacles, final screenshot as the persona
saw it, and an Agent View replay link) and `results.json`.

Presets (`poc/presets/*.yaml`) hold the site, how to get in (form login and/or headers, `${ENV}` expansion) and the
customer's own tasks, with an optional expected answer for automatic grading.

## Results (2026-11-07, demo shop, task "buy an in-stock blue mug and report the checkout total")

Expected answer €19.90. Each trap in the demo shop is described at the top of `demo_site/server.py`.

| Persona | Result | Steps | Cost | What the agent reported |
|---|---|---|---|---|
| Baseline | ✅ €19.90 | 8 | $0.02 | Picked the in-stock "Large" mug, no obstacles |
| Colour blind (deuteranopia) | ❌ €16.90 | 8 | $0.02 | "Stock status only by coloured dots… indistinguishable to a deuteranope", so it ordered the backordered mug (WCAG 1.4.1) |
| Low vision (200% zoom + blur) | ❌ blocked | 26–35 | $0.09 | "Checkout button off-screen to the right at 200% zoom": scroll, arrows and Tab all failed (WCAG 1.4.10 reflow). Misread "€" as "£" through the blur |
| Keyboard only | ❌ blocked | 20 | $0.07 | "Proceed to checkout is not in the Tab order, likely a `<div>` with onclick" (WCAG 2.1.1). Did everything else with Tab/Enter; its one mouse attempt was blocked by our bridge |
| Hand tremor (±10 px) | ✅ €19.90 | 8 | $0.02 | Clicks drifted 1–9 px and still landed. **One run is not enough**: tremor needs several runs, as planned for the baseline |

Each persona fails at the trap built for it, and the agent's own words name the cause well enough to map it to a
WCAG criterion. The baseline succeeds every time (4 of 4 runs so far).

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

1. **Each self-hosted browser channel is itself a trajectory that holds a session slot.** A run therefore needs 2
   slots (channel + agent session). Free plan = 3 slots = **1 lane**. Builder ($20/month, 10 slots) = up to 5
   lanes.
2. **Channels are not closed when the session ends.** They stay `pending` (holding the slot) until H's periodic
   cleanup marks them `failed` (seen at roughly 25-minute intervals). There is no close endpoint: v2 cancel and
   v1 PATCH accept the request and change nothing. Hence **lanes**: one channel per lane per audit, with one
   bridge kept alive and reused by every run (`runner.Lane`). Only the Chrome behind the bridge is swapped.
3. **A `failed` channel delivers no commands to a new session.** Never reuse channel ids across audits.
   **Never let two sessions share a channel**: a new session on a channel whose previous session is still
   running freezes after its first screenshot.
4. **A session waiting on a dead bridge can't be cancelled**, and its own `max_time_s` doesn't fire either: the
   cancel is accepted but the session stays `running` (two of ours stayed stuck for more than 30 minutes, each
   holding a slot). So never kill a bridge while its session runs. Close finished sessions (they end `idle`,
   holding a slot) with an explicit cancel.
5. **Opening a channel at capacity returns 429** ("rate-limited"), which looks like a rate limit but is the
   slot limit. Every retry counts.
6. Agent names: lowercase letters, digits and hyphens only (no `_`).
7. Parallel processes must not all ask Selenium Manager for a chromedriver at once ("Text file busy"), so we put
   ours on PATH.

Questions for the H team on site: how to close a `user_device` channel, and whether they can raise our session
limit for the hackathon.

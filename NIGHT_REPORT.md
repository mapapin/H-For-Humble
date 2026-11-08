# Night report: 2026-11-07 → 08 (branch `humble-v2`)

All work is on the `humble-v2` branch (not merged into `main`; Mattéo did not push to `main` overnight).
To try it: `git checkout humble-v2 && npm install && npm run build && npm start`, plus the demo sites
(see README). Every feature below was exercised with real H sessions unless noted.

## What changed

**Product**
- **5 agents in parallel in the app**, one H agent + one Chrome per perspective, with live screens and the agent's
  current thought on each tile, a full-size live view, pause/resume/stop, and **steer** (send one agent a hint;
  the run is marked as assisted).
- **New report**: inclusion score (0–100, A–F) with a one-sentence verdict; per-perspective tiles (outcome, steps
  ×baseline, time, barriers, score); "what stopped users" (worst first, each with a replay); effort chart vs baseline.
- **Replays** of every run, rebuilt from H's session history: screenshot per step, the agent's thought, its action,
  the click target, where a tremor click landed, blocked mouse actions, keycaps.
- **Static checker vs real journeys**: axe-core on every visited page, ranked against what the agents lived.
  Demo shop: axe flags 1 rule, agents find 3 blocked perspectives (2 that no rule detects).
- **Linkup**: journey suggestions for public sites in the new-audit form; the site's own accessibility statement
  read via Linkup fetch (mousquetaires.com: "Partially compliant with RGAA 4.1.2 (69% of criteria met)",
  self-assessed, 6 known issues incl. keyboard traps) shown as **declared vs measured**.
- **Fixes for any site**: a rule engine (keyboard access + focus ring, 44 px targets, contrast + reflow at 200%,
  colour legends turned into text labels) when Devin isn't configured; hand-written fixes for both demo sites;
  struggling perspectives (completed with a poor score) also get fixes. One large before/after slider per
  perspective.
- **Lune**, a new designed demo startup (pottery workshops in Paris) with one barrier per perspective, its
  hand-written fixes and preset (built with a Claude sub-agent, verified per persona in headless Chrome).
- **HTML report export** (one self-contained file) and sample reports in `docs/`.
- Simpler shell: URL-first home, audits list with score and per-perspective outcome, environment page.
- README rewritten for judges; `DEMO.md` script.

**Reliability**
- Watchdogs: no first step after 90 s or no progress for 3 min → retried once on a fresh channel; two failing cases
  stop the audit. Agents start one after another (the next waits for the previous one's first step).
- H slot management: quota checked before each run; lanes wait visibly for a free slot; our own recently released
  browser channels are recycled (1 slot instead of 2).
- Stuck H sessions are listed in the report with their Agent View links.
- Tests: 41 unit tests (`npm test`), browser smoke test with axe checks of Humble's own UI (it caught and I fixed
  two contrast issues and invalid markup in the new UI).

## H platform problems seen tonight (please tell the H team)

1. **Sessions stuck at step 0** from ~00:50: several sessions started seconds apart got their first observation,
   then never a model step. `cancel`, `pause` and `force_answer` don't end them; they hold slots until stopped in the
   web UI. You stopped 5 at ~04:20; more got stuck at 04:22 (session ids in the app's report banner).
2. **Browser channel slots still leak** for ~25–30 min after each run (the global fix wasn't live overnight).

Workarounds are in place (see Reliability), but a fully parallel live demo depends on (1) being fixed. Plan B:
show finished audits and replays (the report is fully usable offline).

## Results (real H runs)

| Audit | Outcome |
|---|---|
| Mugs & Co, 5 agents in parallel (00:16) | 5/5 runs done in ~5 min. Score **39/F**: baseline and tremor complete; colour blind buys the backorder mug, keyboard-only can't reach checkout, low vision can't find checkout at 200% (29 steps). axe-core flags **1** rule (contrast). Sample: `docs/sample-report-demo-shop.html`, montage `docs/montage-demo-shop.mp4`. |
| Mugs & Co re-evaluation with fixes (yesterday) | **40 → 100**: every perspective completes with the fixes injected. |
| Mugs & Co "Fix until it passes" (yesterday) | **33 → 100** in 2 rounds. |
| mousquetaires.com (real site, 00:30) | Score **79/B** on 4 perspectives (keyboard-only didn't get a slot): all complete, but **low vision needed 4.7× the steps** (33 vs 7, score 36). Linkup: *partially compliant with RGAA 4.1.2 (69%), self-assessed*, known issues include keyboard traps in the mega-menu. axe: 5 rules. Sample: `docs/sample-report-mousquetaires.html`. |
| Lune, complete 5-agent audit (04:54) | **34/F**: baseline 92 and tremor 80 complete; low vision, colour blind and keyboard-only blocked. One session never started on H (tremor) and was retried automatically on a fresh browser, then completed. axe: 4 rules. 4 fixes with before/after. Baseline needed 28 steps on the 18 px date strip, so I replaced it with a month calendar (26 px days) for the next runs. |
| Lune (earlier runs) | Runs that H started work as designed: baseline books Thu 10 Dec for €98.00 in 7 steps; low vision blocked (33 steps); keyboard-only blocked (25 steps, cannot select a date); colour blind picks a sold-out date, only discovers it at checkout ("waitlist"), then books a later date (now counted as a failure: the expected answer checks the total *and* the first open date). No complete 5/5 Lune audit yet because of the H stalls below. axe on Lune: 4 rules (48 contrast nodes), none for the blockers the agents hit. |

## Not done / next

- Devin: no Devin API key in `~/Documents/h_company`, so `DevinFixer` is still untested (the UI and pipeline
  accept it as soon as `DEVIN_API_KEY` and `DEVIN_ORG_ID` are set).
- Gladia: not used (Linkup covers the partner requirement).

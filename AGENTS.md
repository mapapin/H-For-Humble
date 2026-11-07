# AGENTS.md — H For Humble

Context file for coding agents (Claude Code, Devin, Cursor, Codex…) working in this repo.
Read it fully before you write code. It holds the hackathon rules, how we are judged, and the
H Company Agents API cheat sheet.

> Source: event page, participant dashboard and sponsor PDFs, collected 2026-11-07.
> If something here conflicts with the live dashboard, the dashboard wins.

---

## 0. TL;DR for agents

- **Event**: H Company × 42AI — *Computer Use Agents Hackathon*, Paris, **Sat Nov 7 – Sun Nov 8, 2026**.
- **Goal**: build an **agent that operates real software** (websites, desktop apps, UIs) to complete a
  **meaningful real-world task**. Show it working end to end.
- **Hard deadline**: **Sunday Nov 8, 14:00 CET** (event page). The dashboard says 14:30 GMT+1. **Plan for 14:00.**
- **Mandatory tech (hackathon rule)**: the project **must use the H Company computer-use agent** (Agents API:
  `hai-agents` SDK, `holo` CLI, Holo models) **plus at least one partner tool: Stripe, Gladia, Linkup or Devin**.
  Using **Devin to write part of the code counts**, so in practice only the H agent is a hard product requirement.
  Computer use must be *central* to the product. That is 25% of the score.
- **Optimize for**: a reliable, demoable end-to-end flow + user supervision (watch/stop the agent) + a clear video.
- **Never commit secrets** (API keys, promo codes). Use `.env` (git-ignored) and `HAI_API_KEY`.

---

## 1. Team & repo

- Team **H For Humble**: Théophile + Mattéo (allowed team size: 1–4).
- Repo: `git@github.com:mapapin/H-For-Humble.git` (branch `main`). The repo URL is a required submission field.
- Project idea / architecture: **TBD**. Fill in section 9 once decided.

---

## 2. The challenge (official brief)

> Build an agent that can navigate websites, desktop apps, and software interfaces to complete a
> meaningful real-world task. Pick a use case, build it, and show it working.
>
> H Company brings its Agents API and specialized models. Aim for a working demo that clearly shows
> **the problem, the actions your agent takes, and the result**.

Partners: Gladia, Linkup, Cognition (Devin), Stripe, Morning. Provided: API credits, 1:1 mentoring
by H Company researchers/engineers, on-site technical support.

---

## 3. Judging criteria (how we are scored)

| Weight | Criterion | Judges ask… | What it means for the build |
|---|---|---|---|
| **25%** | Use of Computer Use | How central and well-used is the agent's ability to operate a computer? | The agent must *drive real UIs* (click, type, navigate). Don't build something a plain API call could do. |
| **25%** | Technical execution & reliability | Does it work end to end, consistently, without hand-holding? | One flow that works every time beats five flaky ones. Add retries, timeouts and structured output. Test the demo path many times. |
| **20%** | Usefulness & impact | Does it solve a real problem for real users? | Pick a painful, concrete, recurring task with a clear user. |
| **15%** | Originality & innovation | Is the idea or approach new? | Avoid "book a flight" / "shop on Amazon" clichés, or give them a twist. |
| **10%** | UX & user control | Easy to use? Can the user supervise or stop the agent? | Show live progress (Agent View link / event stream), plus **Stop / Pause / Steer** controls and confirmation before risky actions. |
| **5%** | Demo clarity & Q&A | Was the video clear, did the team answer well? | Short video: problem → agent acting → result. |

Judges (from the event page): Mathieu Astruc, Axel Darmouni, Thibaut Le Boulaire, Maxime Langevin, Alexis Deprez,
zak a, Nolwenn Guiban, Denis Charrier, Amel Negra, Mathieu Diaz.

---

## 4. Submission (BuilderBase dashboard → Overview)

All **six** fields are required before "Submit" unlocks:

1. **Project Name**
2. **GitHub Repo**: this repo, so keep `README.md` clean, with setup + run instructions.
3. **Submission on H Showcase Platform**: a link to our entry on H's showcase.
4. **Demo Video URL**: record early. Don't leave it for the last hour.
5. **Project Description**
6. **How do I test your product live**: judges must be able to try it. Write exact steps in the README.

Deadline: **Sun Nov 8, 14:00 CET** (dashboard countdown points to 14:30. Don't count on it).
Then: 14:30 first round of judging → 15:45 final pitches → 17:15 deliberation → 17:45 winners.

---

## 5. Schedule (Paris time, CET / UTC+1)

**Sat Nov 7**: 09:00 check-in · 09:45 kickoff · **10:30 hacking begins** · 12:30 lunch · 16:00 snacks ·
19:00 dinner · **21:00 no further entry to the venue**.

**Sun Nov 8**: 08:00 venue reopens · 09:00 breakfast · 12:30 lunch · **14:00 SUBMISSION DEADLINE** ·
14:30 judging round 1 · 15:45 final pitches · 17:15 jury deliberation · 17:45 winners · 18:00 drinks · 20:30 close.

Venue: La Maison by Motier Ventures, Paris.

---

## 6. Prizes (€13,000 cash)

- 1st **€5,000** · 2nd **€3,000** · 3rd **€2,000** (per team)
- Sponsor tracks: **Best use of Gladia €1,000** · **Best use of Devin €1,000**
- Social: Best LinkedIn post €500 · Best X post €500
- Morning Nomade: 1 free month of coworking for top-3 team members. 50% off for all participants.

Side-track opportunity: a voice interface via **Gladia** (speech-to-text → agent task) can make us eligible
for the Gladia prize without changing the core product. Building with **Devin** qualifies for the Devin prize.

---

## 7. H Company Agents API — cheat sheet

Docs index (LLM-friendly): **https://hub.hcompany.ai/llms.txt**. Every page also has a `.md` version
(e.g. `https://hub.hcompany.ai/agents-api/overview.md`).
Read-only **docs MCP server** (no key needed): `https://hub.hcompany.ai/mcp`. Add it to your coding agent.

### Products
- **Agents API**: managed computer-use agents. Base URL **`https://agp.eu.hcompany.ai/api/v2`** (EU) or `https://agp.hcompany.ai/api/v2` (US).
- **Models API**: OpenAI-compatible Holo VLMs at `https://api.hcompany.ai/v1` (for building our own agent loop / GUI grounding).
- **HoloTab**: free no-code Chrome extension running Holo in the user's browser.
- **`holo` CLI**: runs agents from the terminal, drives the local desktop, exposes `holo mcp` for coding agents.

### Setup
```bash
# API key: https://platform.hcompany.ai/settings/api-keys  (shown once; keep server-side)
# Hackathon €100 credit code: on the BuilderBase dashboard → Resources (never commit it)
export HAI_API_KEY="hk-..."           # put it in .env, never in code
pip install hai-agents                 # Python SDK  (from hai_agents import Client)
pip install "hai-agents[cli]"          # + holo CLI  (holo login)
pip install "hai-agents[cli,desktop]"  # + local desktop control
npm install hai-agents                 # TypeScript  (import { HaiAgentsClient } from "hai-agents")
```

### Minimal run (Python)
```python
from hai_agents import Client
client = Client()  # reads HAI_API_KEY
result = client.run_session(
    agent="h/web-surfer-flash",
    messages="Open Hacker News and list the top 3 stories with their URLs.",
)
print(result.status, result.answer)
```
Raw HTTP: `POST /sessions` with `{"agent": "...", "messages": "..."}` and `Authorization: Bearer $HAI_API_KEY`.
Then poll `GET /sessions/{id}/status` until terminal, and read `latest_answer` from `GET /sessions/{id}`.

### Pre-built agents & models
- `h/web-surfer-flash`: fast cloud-browser agent. **Default choice.**
- `h/web-surfer-pro`: stronger, for harder multi-step web tasks (logins, checkouts).
- `h/deep-search-pro`: manager that delegates to subagents.
- `h/holo-desktop`: drives **your own desktop** (mouse/keyboard/screen; optional shell).
- List all: `GET /api/v2/agents`. Environments: `GET /api/v2/environments`.
- Models: `holo4-27b` (default, most capable) · `holo4-35b-a3b` (fastest/cheapest, good for interactive loops) ·
  `holo3-1-35b-a3b` (free tier) · `holo3-122b-a10b`.

### Key concepts
- **Agent** = saved config: `name`, `description`, `environments`, `model`, `instructions`, `skills`, `tools`,
  `subagents`, `answer_format`. Define it inline in `run_session(agent={...})` or create it in the catalog.
- **Environments**: cloud browser (default, `kind: "web"`, host `cloud`) · **your Chrome** (`host: "user_device"`,
  attaches to Chrome on port 9222, keeps your logins) · **your desktop** (`kind: "desktop"`).
- **Session** = one agent working on one task. States: `queued`, `pending`, `running`, `paused`, `idle`,
  `awaiting_tool_results` → terminal: `completed` | `failed` | `timed_out` | `interrupted`.
- **Structured output**: pass `answer_schema=<Pydantic model>` (Python) / `answerSchema` (zod) → typed JSON answer.
- **Custom tools**: let the agent call functions in *our* code (e.g. save to DB, ask the user for confirmation).
  They run only while our program waits on or streams the session.
- **Subagents**: a manager agent fans out work to parallel subagents (`subagents=["h/web-surfer-flash"]`).
- **Skills**: markdown instructions an agent loads when a task matches.
- **Vaults / browser profiles**: sign in without secrets entering the agent's context. Profiles start a session signed in.
- **Overrides**: tweak a catalog agent per session, e.g. `agent.environments[kind=web].start_url=...`.

### Supervision & control (counts for the UX score)
- Every session has an **`agent_view_url`** (Agent View on H Platform): live view + replay. Embed or link it in our UI.
- `session = client.start_session(...)` then `for event in session.stream(): ...` gives live events for our own UI.
- Steer mid-run: `session.send_message("...")`. Also **pause / resume / cancel / force-answer** endpoints.
- Desktop kill switch: **Esc twice** or `holo local stop`.
- Sessions can be shared via a public Agent View link (`/sessions/{id}/share`). Useful for the demo / judges.

### Limits (free tier)
- 60M tokens/month, **3 concurrent sessions** (extra ones queue, they don't fail). Builder plan: 10 concurrent.
- Cloud environment starts: 10/min per user, 20/min per org.
- Codex-like hosts time out MCP calls at 60 s. The `holo mcp` server runs one task at a time.

### Coding-agent integrations
- `holo login && holo mcp install claude-code` (or `cursor`, `codex`, `vscode`…) adds a `run_agent` / `list_agents` MCP tool.
- Claude Code skill that writes correct `hai-agents` code: `/plugin marketplace add hcompai/computer-use-agents-demos`
  then `/plugin install hai-agents@hai-skills`.
- **Demo/example repo**: https://github.com/hcompai/computer-use-agents-demos (the sponsor PDF lists the older name
  `hcompai/agents-api-demos`). "Don't start from scratch: fork a demo and build on top of it."

### Writing good agent tasks
The H agent does **not** see our conversation. Each task must name the app or site, the account, the target, and
**when it is done / what to return**. Write it as an imperative instruction.
Good: *"Open Slack in the Acme workspace and DM Sarah Chen 'on my way'. Return 'sent' once the message appears."*
Bad: *"Tell Sarah."*

### Useful doc pages
Quickstart `agents-api/overview` · Sessions `agents-api/sessions/overview` · Watch & steer `agents-api/observe-and-steer` ·
Custom tools `agents-api/custom-tools` · Structured output `agents-api/structured-output` · Subagents `agents-api/multi-agent` ·
Your browser `agents-api/your-browser` · Your desktop `agents-api/your-desktop` · Speed `agents-api/optimize-for-speed` ·
Errors `agents-api/errors` · Models `models-api/overview` · CLI `holo-cli/introduction` (all under `https://hub.hcompany.ai/`).

---

## 8. Partner tools (at least one is required, and they unlock sponsor prizes)

**Rule:** besides the H computer-use agent (mandatory), the project must use **at least one** of Stripe, Gladia,
Linkup or Devin. Devin counts even if it's only used to write part of the code, so keep a trace of that
(e.g. Devin PRs or sessions, and a mention in the README and video). Integrating Gladia or Linkup into the
product itself is optional but makes us eligible for more and adds to originality and usefulness.

- **Gladia** (speech-to-text, voice agents). €1K prize for best use. Docs https://docs.gladia.io, LLM docs
  https://docs.gladia.io/llms.txt. SDK: `pip install gladiaio-sdk` / `npm i @gladiaio/sdk`. Live STT quickstart:
  `docs.gladia.io/chapters/live-stt/quickstart`. MCP: `claude mcp add gladia --env GLADIA_API_KEY=... -- npx -y @gladiaio/mcp`.
  Skills: `npx skills add gladiaio/skills`. Integrations: Pipecat, LiveKit, Vapi, Twilio, n8n. GladiaFlow = open-source voice dictation app.
  Credits: 1K via coupon in the dashboard PDF.
- **Linkup** (real-time web search API for agents): https://docs.linkup.so/pages/documentation/get-started/introduction.
  $20 free + $25 promo (code in dashboard PDF).
- **Devin / Cognition** (AI software engineer). €1K prize for best use. $200 promo via
  https://www.trydevin.ai/computer-use-agents-hackathon (log in with the hackathon registration email), then app.devin.ai.
- **Stripe**: $250 credits as a participation gift, added after the event.
- **Morning**: coworking perks (see prizes).

---

## 9. Our project (fill in as we go)

- **Use case / problem**: TBD
- **Target user**: TBD
- **Agent flow (problem → actions → result)**: TBD
- **Stack**: TBD (must include the H Agents API, plus at least one of Stripe / Gladia / Linkup / Devin)
- **Demo script**: TBD
- **How judges test it live**: TBD

---

## 10. Rules for agents working in this repo

1. **Secrets**: never hard-code or commit API keys, promo codes or credentials. Read them from env vars
   (`HAI_API_KEY`, `GLADIA_API_KEY`, `LINKUP_API_KEY`…) loaded from a git-ignored `.env`. Commit a `.env.example` with names only.
2. **Demo path first**: get one end-to-end flow working before adding features. Reliability is worth 25%.
3. **Keep the human in control**: show live progress (`agent_view_url` and/or streamed events), expose stop/pause,
   and ask for confirmation before irreversible actions (sending, paying, deleting).
4. **Prefer the SDK** (`hai-agents`) over raw HTTP. Use structured output (`answer_schema`) when our code consumes the result.
5. **Watch the 3-concurrent-session limit** on the free tier. Cancel sessions you no longer need.
6. **Keep `README.md` judge-ready**: what it does, setup, run, and "how to test live".
7. Time is short. Prefer simple, working code over abstractions.

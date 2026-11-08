# Humble

Test a website through five perspectives: baseline, low vision, colour blindness, hand tremor and keyboard only.
H Company agents navigate real Chrome browsers, then Humble shows results, replays, barriers and suggested fixes
with before/after previews. Built by **H For Humble — Théophile + Mattéo**.

## 1. Install

You need **Python 3.10+**, **Node.js 20.19+ (or 22.12+)**, and **Google Chrome** installed locally.
The commands below are for macOS/Linux.

```bash
git clone https://github.com/mapapin/H-For-Humble.git
cd H-For-Humble
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm install
cp .env.example .env
```

If you already have a `.env`, keep it rather than copying over it.

## 2. Add your API keys

Edit `.env` in the project root:

| Variable | Required? | Used for |
| --- | --- | --- |
| `HAI_API_KEY` | **Yes** | H computer-use agents and Holo evaluation. Get it from [H Platform](https://platform.hcompany.ai/settings/api-keys). |
| `DEVIN_API_KEY` | Optional | Devin-generated accessibility fixes. Use a service user API key from Devin Settings → Devin API. |
| `DEVIN_ORG_ID` | Required with Devin | Organization ID shown at the top of Devin Settings → Devin API. |
| `LINKUP_API_KEY` | Optional | Journey suggestions and accessibility research for public websites. |

Keep `DEVIN_MODE=fast` from the example configuration. Without Devin, Humble uses demo fixes or its local rule
engine. Real audits and Devin sessions consume your providers’ credits. Keep `.env` private; never commit it.

Chrome is detected automatically. If necessary, set `AUDIT_CHROME` in `.env` to the absolute path of its executable.

## 3. Start Humble and Lune

**Terminal 1 — Humble**, from the project root:

```bash
npm run build
npm start
```

Open **http://127.0.0.1:8000** and create a Humble account with your own email and password.

**Terminal 2 — Lune**, also from the project root:

```bash
.venv/bin/python poc/demo_startup/server.py --port 8770
```

The deliberately inaccessible pottery-booking demo is at **http://127.0.0.1:8770**.
Leave both terminals running. Use `Ctrl+C` to stop each server.

### Lune test credentials

These credentials belong to the local **Lune demo**, not to your Humble account:

- **Email:** `camille@lune-atelier.test`
- **Password:** `terre-cuite-2026`
- **Login page:** http://127.0.0.1:8770/login

Login is optional by default. To test a journey that requires authentication, stop the Lune server and restart it
with this command, then enable website login in Humble and enter the credentials above:

```bash
LUNE_REQUIRE_LOGIN=1 .venv/bin/python poc/demo_startup/server.py --port 8770
```

## 4. Run your first test

1. In Humble, start a new audit and enter `http://127.0.0.1:8770/` as the website URL.
2. Use this journey: **Book 2 seats for “Wheel throwing for beginners” on the first available date in December.
   Reach checkout and report the date and total. Stop before confirming or paying.**
3. Expected answer: `98.00; 8 Dec|8 December|December 8|8 décembre|08/12` — **8 December 2026, €98 for 2 seats**.
4. For a smaller first test, select **Baseline** and **Keyboard only**, with **one repetition**. Add the other
   perspectives afterwards. Leave website login off unless you started Lune with `LUNE_REQUIRE_LOGIN=1`.
5. Launch the audit. Watch progress or open **H Agent View**; pause or stop from Humble if needed.
6. Open a perspective’s replay to read the agent’s answer and barriers. In **Fixes**, inspect the before/after
   previews, then use **Evaluate again with fixes** to test the proposed changes.

The keyboard-only perspective should expose the inaccessible calendar. Runs vary: these are simulated user
journeys, not an accessibility certification.

Each active perspective normally needs **2 H session slots** (browser channel + agent). With a 3-slot quota,
runs wait their turn; five perspectives need 10 free slots to run together. Waiting for H slots and generating
Devin fixes can take several minutes. Restart Humble after changing `.env`.

## Useful commands

```bash
npm test                                      # automated tests
.venv/bin/python scripts/preflight.py          # local setup and H quota checks
.venv/bin/python poc/demo_site/server.py --port 8765  # alternative Mugs & Co demo
```

Sample reports and demo videos are in [docs/](docs/). Technical details are in [poc/README.md](poc/README.md).
Local accounts, audit data and encrypted credentials are stored in ignored `data/`; preserve `data/vault.key`
if backing up the credential vault. Run this hackathon app on localhost and use test accounts on audited sites.

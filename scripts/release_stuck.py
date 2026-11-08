"""Try to end H sessions stuck in "running" at step 0 (they hold slots; cancel/pause have no effect on them).

H's docs say a new session on a self-hosted browser channel takes it over and cancels the earlier session. So for
each stuck session of ours: bind a fresh bridge to its browser channel, start a one-step session on it, check that
the stuck session is no longer running, then end the helper session. Needs 1 free H slot.

Usage: .venv/bin/python scripts/release_stuck.py [session_id ...]   (default: every stuck session found in data/)
"""
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "poc"))
from audit import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ["HAI_AUTO_BRIDGE"] = "0"

from hai_agents import Client  # noqa: E402
from chrome import ChromeProcess  # noqa: E402
from personas import PERSONAS  # noqa: E402
from runner import BridgeThread, PersonaBridge, close_h_session  # noqa: E402


def channels_of_our_sessions() -> dict[str, str]:
    """session id -> browser channel id, from saved run records."""
    out = {}
    for f in glob.glob(str(ROOT / "data" / "audits" / "*" / "*.json")):
        try:
            r = json.loads(Path(f).read_text())
        except Exception:
            continue
        if isinstance(r, dict) and r.get("session_id") and r.get("channel_id"):
            out[r["session_id"]] = r["channel_id"]
    return out


def release(client: Client, session_id: str, channel_id: str, port: int = 9498) -> bool:
    chrome = ChromeProcess(port, PERSONAS["baseline"], headless=True)
    bridge = PersonaBridge(PERSONAS["baseline"], port, os.environ["HAI_API_KEY"], rng_seed=1, channel_id=channel_id)
    serving = BridgeThread(bridge)
    helper = None
    try:
        if not bridge.ready.wait(60):
            print(f"{session_id}: bridge not ready")
            return False
        helper = client.start_session(
            agent={"name": "humble-channel-release", "description": "Ends a stuck session on this browser",
                   "instructions": "Answer immediately with the word done. Do not use the browser.",
                   "environments": [{"id": "browser", "kind": "web", "host": "user_device", "session_id": channel_id}]},
            messages="Answer with the word done.", max_steps=1, max_time_s=60)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = str(client.sessions.get_session_status(session_id).status)
            if status != "running":
                print(f"{session_id}: now {status}")
                return True
            time.sleep(3)
        print(f"{session_id}: still running")
        return False
    finally:
        if helper is not None:
            close_h_session(client, helper.id)
        serving.stop()
        chrome.close()


def main(ids: list[str]) -> None:
    channels = channels_of_our_sessions()
    with Client() as client:
        if not ids:
            ids = [s.id for s in client.sessions.list_sessions(size=50).items
                   if str(s.status) == "running" and s.id in channels]
        print("quota before:", client.sessions.get_session_quota())
        for sid in ids:
            if sid not in channels:
                print(f"{sid}: no channel recorded, skipped")
                continue
            release(client, sid, channels[sid])
        print("quota after:", client.sessions.get_session_quota())


if __name__ == "__main__":
    main(sys.argv[1:])

"""One audit run = one persona trying one task once, in its own Chrome, driven by an H agent.

The H agent runs in H's cloud; its browser actions come back to this process through the H "your browser"
bridge (host: user_device), which we subclass to filter inputs (tremor, keyboard only) and log every action.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import tempfile
import os
import random
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from hai_agents import Client
from hai_agents_local import SeleniumBrowserBridge

from chrome import ChromeProcess, DevToolsController
from personas import PERSONAS, Persona, build_instructions
from replay import build_replay, fetch_events, latest_thought
from scoring import run_metrics

CHANNEL_LOCK = Path(tempfile.gettempdir()) / "h-audit-channel.lock"
CHANNEL_SPACING_S = 3
CHANNEL_REUSE_GRACE_S = 20  # pause between one session ending and the next binding the same channel
STALL_S = 180  # no new agent step for this long = the session is stuck
TERMINAL_STATUSES = {"completed", "failed", "timed_out", "interrupted"}
log = logging.getLogger("audit")
TERMINAL_SESSION_STATES = {"completed", "failed", "timed_out", "interrupted"}


def close_h_session(client, session_id: str) -> bool:
    """Cancellation is asynchronous: keep serving the browser while checking it landed."""
    options = {"timeout_in_seconds": 5, "max_retries": 0}
    for attempt in range(3):
        try:
            state = client.sessions.get_session_status(session_id, request_options=options)
            if str(state.status) in TERMINAL_SESSION_STATES:
                return True
        except Exception:
            pass
        try:
            client.sessions.cancel_session(session_id, request_options=options)
        except Exception:
            pass
        time.sleep(1)
    try:
        return str(client.sessions.get_session_status(session_id, request_options=options).status) in TERMINAL_SESSION_STATES
    except Exception:
        return False

POINTER_COMMANDS = {"click_at", "mouse_move_to", "write_at", "get_select_at", "scroll_at", "fill_secret_at"}
MOUSE_ONLY_COMMANDS = {"click_at", "click", "double_click", "mouse_press", "mouse_release", "mouse_move_to",
                       "mouse_move_by", "drag_and_drop", "get_select_at"}
KEYBOARD_ONLY_ERROR = (
    "Mouse input is unavailable: this user cannot use a mouse. Use the keyboard instead "
    "(Tab / Shift+Tab to move focus, Enter or Space to activate, arrow keys, then type into the focused field)."
)
QUIET_COMMANDS = {"observation_bundle", "screenshot_and_metadata", "screenshot_b64", "screenshot_png_bytes",
                  "get_screen_size", "current_url", "get_tabs", "get_active_tab", "get_tab_title",
                  "webpage_metadata", "viewport_markdown", "supports_files", "runner_session_id"}


class Obstacle(BaseModel):
    where: str = Field(description="Page or screen where it happened")
    what: str = Field(description="What went wrong or was hard, in the user's words")
    severity: Literal["minor", "major", "blocking"]


class RunReport(BaseModel):
    completed: bool = Field(description="True only if you fully achieved the goal")
    answer: str = Field(description="The information the goal asked you to report, or an empty string")
    obstacles: list[Obstacle]
    summary: str = Field(description="One or two sentences on how it went")


class LocalVault:
    """Vault served by our own process: H's driver hook for `fill_secret_at` on a self-hosted browser.
    The agent only says which field to fill and where; the value is typed here and never leaves this machine."""

    def __init__(self, login: dict[str, str], allowed_host: str) -> None:
        self.login = login
        self.allowed_host = allowed_host
        self.driver = None  # set once the driver exists

    def list_secrets(self, url: str | None = None):
        from hai_drivers.web.interface import AvailableSecrets

        fields = [f for f in ("username", "password") if f in self.login]
        return AvailableSecrets(fields=fields)

    def fill_secret_at(self, field: str, x: int, y: int, submit: bool = True) -> None:
        from hai_drivers.web.interface import assert_secret_origin

        key = "password" if field == "password" else "username"
        assert_secret_origin(self.driver.current_url(), self.allowed_host)
        self.driver.click_at(x, y)
        self.driver.input_text(str(self.login[key]))
        if submit:
            self.driver.input_text("\n")


class PersonaBridge(SeleniumBrowserBridge):
    """H's local browser bridge, plus persona input filters and an action log."""

    def __init__(self, persona: Persona, debugging_port: int, api_key: str, rng_seed: int,
                 vault: LocalVault | None = None, channel_id: str | None = None) -> None:
        # channel_id: reuse an existing browser channel (it keeps its slot) instead of opening a new one
        super().__init__("browser", api_key=api_key, debugging_port=debugging_port, session_id=channel_id)
        self.persona = persona
        self.vault = vault
        self.rng = random.Random(rng_seed)
        self.t0 = time.monotonic()
        self.actions: list[dict[str, Any]] = []
        self.dispatched = 0  # every command served, screenshots included

    def create_driver(self):
        driver = super().create_driver()
        if self.vault is not None:
            self.vault.driver = driver
            driver.attach_vault(self.vault)
        return driver

    def start_run(self, persona: Persona, vault: LocalVault | None, rng_seed: int) -> None:
        """Reuse this bridge, and so its H channel, for another run: new persona, fresh Chrome on the same port.
        The bridge keeps polling the channel throughout, so no command is ever picked up by a dying bridge."""
        self.persona, self.vault = persona, vault
        self.rng = random.Random(rng_seed)
        self.t0 = time.monotonic()
        self.actions = []
        self.dispatched = 0
        # The bridge dedups redelivered commands by command_uid; clear it so a new session can never be answered
        # with a previous session's cached result (precaution: we have not seen uids repeat across sessions).
        self._results.clear()
        self.end_run()
        self._driver = self.create_driver()

    def end_run(self) -> None:
        old, self._driver = self._driver, None
        if old is not None:
            try:
                old.destroy()  # stops this run's chromedriver; the browser itself is closed by ChromeProcess
            except Exception:
                pass


    def _dispatch(self, name: str, args: dict[str, Any]):  # runs on a worker thread per command
        p = self.persona
        rec: dict[str, Any] = {"t": round(time.monotonic() - self.t0, 2), "ts": round(time.time(), 2), "cmd": name}
        log.info("command %s received (persona %s)", name, p.id)  # names only, never args (may hold secrets)
        if p.keyboard_only and name in MOUSE_ONLY_COMMANDS:
            rec["blocked"] = True
            self.actions.append(rec)
            return None, KEYBOARD_ONLY_ERROR
        if p.keyboard_only and name == "write_at":
            # No clicking into fields: type into whatever has keyboard focus, like a real keyboard user.
            rec["rewritten"] = "typed into focused element"
            self.actions.append(rec)
            try:
                if args.get("overwrite", True):
                    self._driver.press_and_release_keys(["ctrl", "a"])
                self._driver.input_text(args["text"])
                if args.get("enter"):
                    self._driver.input_text("\n")
                return "not_checked", None
            except Exception as exc:
                return None, str(exc)
        if name in POINTER_COMMANDS and "x" in args and "y" in args:
            args = dict(args)
            rec["xy"] = [args["x"], args["y"]]  # where the agent aimed, in screenshot px
            if p.click_jitter_px:
                dx = self.rng.randint(-p.click_jitter_px, p.click_jitter_px)
                dy = self.rng.randint(-p.click_jitter_px, p.click_jitter_px)
                args["x"] += dx
                args["y"] += dy
                rec["jitter"] = [dx, dy]
            if p.zoom != 1:
                # The agent points in screenshot pixels; Chrome input events take CSS pixels.
                args["x"] = round(args["x"] / p.zoom)
                args["y"] = round(args["y"] / p.zoom)
        result, error = super()._dispatch(name, args)
        self.dispatched += 1
        if error:
            rec["error"] = error[:300]
        if name not in QUIET_COMMANDS or error:
            self.actions.append(rec)
        return result, error


class Lane:
    """One parallel lane: a fixed port and one long-lived bridge on one H channel, shared by all its runs.

    Each channel is a trajectory holding a session slot until H's periodic cleanup marks it failed; a failed
    channel no longer delivers commands to new sessions. So a lane opens a fresh channel once per audit and
    reuses it for every run (no slot leaked per run), instead of reusing ids across audits."""

    def __init__(self, index: int, port: int, reuse_channel: bool = False) -> None:
        import uuid

        self.index, self.port = index, port
        # Default: a fresh channel per run. Reusing one channel across sessions saves slots but H sometimes stops
        # sending commands to the next session after its first screenshot (seen in ~1 of 9 reuses, never on a
        # fresh channel), so reuse is opt-in for slot-starved plans.
        self.reuse_channel = reuse_channel
        self.channel_id = str(uuid.uuid4())
        self.bridge: PersonaBridge | None = None
        self.serving: BridgeThread | None = None
        self.prev_session_id: str | None = None

    def wait_previous_released(self, client: Client, tag: str) -> None:
        """Before the next session binds this channel, let H fully finish the previous one. Starting a session
        2 s after the previous one completed got it cut off after its first screenshot (H stopped sending
        commands); waiting for a terminal status plus a grace period avoids racing H's teardown."""
        if not self.prev_session_id:
            return
        deadline = time.monotonic() + 90
        status = None
        while time.monotonic() < deadline:
            status = str(client.sessions.get_session(self.prev_session_id).status.status)
            if status in TERMINAL_STATUSES:
                break
            time.sleep(3)
        log.info("previous session %s is %s; grace %ss before reusing channel", self.prev_session_id, status,
                 CHANNEL_REUSE_GRACE_S)
        if status not in TERMINAL_STATUSES:
            print(f"[{tag}] previous session {self.prev_session_id} still {status}; switching to a fresh channel",
                  flush=True)
            self.close(rotate_channel=True)
            return
        time.sleep(CHANNEL_REUSE_GRACE_S)

    def alive(self) -> bool:
        return (self.serving is not None and self.serving.thread.is_alive() and self.serving.error is None
                and self.bridge is not None)

    def close(self, rotate_channel: bool = False) -> bool:
        closed = True
        if self.bridge is not None:
            with Client() as client:
                closed = close_h_session(client, self.bridge.session_id)
        if self.serving is not None:
            self.serving.stop()
        self.bridge = self.serving = None
        if rotate_channel:
            # After a failed run its session may still be attached to the channel; two sessions on one channel
            # break both, so continue on a fresh channel (costs one more slot until H's cleanup).
            import uuid

            self.channel_id = str(uuid.uuid4())
        return closed


def wait_terminal(handle, timeout_s: float) -> str | None:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        try:
            status = str(handle.status().status)
        except Exception:
            return None
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(2)
    return status


FIRST_STEP_S = 90  # a session that has not made its first step by then never received the browser


def wait_with_watchdog(handle, total_s: float, tag: str, stall_s: float = STALL_S, poll_s: float = 60,
                       first_step_s: float = FIRST_STEP_S):
    """wait_for_completion (which also runs our custom tools), but give up early when the agent stops making
    progress: a session whose browser commands stopped flowing otherwise hangs until total_s.

    poll_s must stay well above the SDK's 20 s server long-poll; a long-poll that overruns raises httpx's
    ReadTimeout, which only means "no news yet", never a failed run."""
    import httpx

    deadline = time.monotonic() + total_s
    started = time.monotonic()
    last_steps, last_progress = None, time.monotonic()
    while True:
        try:
            return handle.wait_for_completion(timeout_seconds=poll_s)
        except (TimeoutError, httpx.TimeoutException):
            pass
        except httpx.TransportError:  # network blip (DNS, reset...): not a failed run; the stall check still applies
            time.sleep(5)
        try:
            steps = handle.status().steps or 0
        except httpx.TransportError:
            continue  # transient network hiccup: check again next round
        now = time.monotonic()
        if steps != last_steps:
            last_steps, last_progress = steps, now
        if not steps and now - started > first_step_s:
            print(f"[{tag}] STALLED: no first step after {first_step_s:.0f}s.", flush=True)
            raise TimeoutError(f"session stalled at step 0: no first step after {first_step_s:.0f}s")
        if steps == last_steps and now - last_progress > stall_s:
            print(f"[{tag}] STALLED: no new step for {stall_s:.0f}s at step {steps}. Stop session {handle.id} "
                  "from the H web UI (cancel via API does not work while it waits on the browser).", flush=True)
            raise TimeoutError(f"session stalled at step {steps}: no progress for {stall_s:.0f}s")
        if now > deadline:
            raise TimeoutError(f"session did not finish within {total_s:.0f}s")


# ------------------------------------------------------------------------------------------ channel recycling
# H keeps a finished browser channel "pending" (holding a session slot) until its periodic cleanup, ~25-30 min.
# A pending channel can be bound again by a new bridge and agent session, which then needs 1 new slot instead of 2.
# So we keep our own channels in a small cross-process pool (guarded by CHANNEL_LOCK) and reuse an idle pending one
# before opening a new one. Once H releases channels at session end, the pool simply stays empty.
POOL_FILE = Path(tempfile.gettempdir()) / "h-audit-channels.json"
RECYCLE_CHANNELS = os.environ.get("HUMBLE_RECYCLE_CHANNELS", "1") != "0"


def _pool_load() -> dict[str, dict]:
    try:
        return json.loads(POOL_FILE.read_text())
    except Exception:
        return {}


def _pool_save(pool: dict[str, dict]) -> None:
    tmp = POOL_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(pool))
    tmp.replace(POOL_FILE)


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def take_recycled_channel() -> str | None:
    """An idle channel of ours that H still holds as pending, reserved for this process. Call under CHANNEL_LOCK."""
    pool, now = _pool_load(), time.time()
    found = None
    for cid, entry in sorted(pool.items(), key=lambda kv: kv[1].get("released_at", 0)):
        if entry.get("in_use") and _pid_alive(entry["in_use"]):
            continue
        if now - entry.get("released_at", 0) < CHANNEL_REUSE_GRACE_S:
            continue
        if now - entry.get("created_at", now) > 40 * 60:  # H will clean it up any minute: do not start on it
            pool.pop(cid, None)
            continue
        try:
            status = channel_status(cid)
        except Exception:
            continue
        if status != "pending":
            pool.pop(cid, None)
            continue
        entry.update(in_use=os.getpid(), taken_at=now)
        found = cid
        break
    _pool_save(pool)
    return found


def pool_register(channel_id: str) -> None:
    """Mark a channel as ours and in use (under CHANNEL_LOCK)."""
    pool = _pool_load()
    entry = pool.setdefault(channel_id, {"created_at": time.time()})
    entry["in_use"] = os.getpid()
    _pool_save(pool)


def pool_release(channel_id: str, reusable: bool = True) -> None:
    lock = open(CHANNEL_LOCK, "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        pool = _pool_load()
        if reusable and channel_id in pool:
            pool[channel_id].update(in_use=None, released_at=time.time())
        else:
            pool.pop(channel_id, None)
        _pool_save(pool)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def acquire_channel(client: Client, tag: str, stop_event=None, on_wait=None, timeout_s: float = 2400) -> str | None:
    """Slots for one run (under CHANNEL_LOCK): returns an idle recycled channel (1 more slot needed), or None for
    a fresh channel (2 slots). Waits while neither is possible."""
    deadline = time.monotonic() + timeout_s
    while True:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Audit stopped before starting an H session")
        quota = client.sessions.get_session_quota()
        if quota.available is None or quota.available >= 1:
            cid = take_recycled_channel() if RECYCLE_CHANNELS else None
            if cid:
                print(f"[{tag}] recycling pending browser channel {cid}", flush=True)
                return cid
            if quota.available is None or quota.available >= 2:
                return None
        if time.monotonic() > deadline:
            raise RuntimeError(f"no free H session slots after {timeout_s:.0f}s ({quota.active}/{quota.limit} busy)")
        print(f"[{tag}] waiting for H slots: {quota.active}/{quota.limit} busy", flush=True)
        if on_wait is not None:
            try:
                on_wait(quota)
            except Exception:
                pass
        if stop_event is not None:
            stop_event.wait(10)
        else:
            time.sleep(10)


def channel_status(channel_id: str | None) -> str | None:
    """Status of an existing channel trajectory, or None if it does not exist yet."""
    if not channel_id:
        return None
    import httpx
    from hai_agents_local.config import default_base_url

    r = httpx.get(f"{default_base_url().rstrip('/')}/api/v1/trajectories/{channel_id}",
                  headers={"Authorization": f"Bearer {os.environ['HAI_API_KEY']}"}, timeout=20)
    return r.json().get("status") if r.status_code == 200 else None


def wait_for_slots(client: Client, needed: int, tag: str, timeout_s: float = 2400, stop_event=None,
                   on_wait=None) -> None:
    """A self-hosted browser channel is itself a trajectory on H and holds a concurrency slot until its session
    ends (or ~30 min idle timeout), so a run needs 2 free slots. Wait for them instead of hammering the API."""
    deadline = time.monotonic() + timeout_s
    while True:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Audit stopped before starting an H session")
        quota = client.sessions.get_session_quota()
        if quota.available is None or quota.available >= needed:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"no free H session slots after {timeout_s:.0f}s ({quota.active}/{quota.limit} busy)")
        print(f"[{tag}] waiting for H slots: {quota.active}/{quota.limit} busy", flush=True)
        if on_wait is not None:
            try:
                on_wait(quota)
            except Exception:
                pass
        if stop_event is not None:
            stop_event.wait(20)
        else:
            time.sleep(20)


class BridgeThread:
    """Serves the bridge on its own event loop, like the SDK's runner, and stops it cleanly
    (so its chromedriver is shut down too)."""

    def __init__(self, bridge: PersonaBridge) -> None:
        self.bridge = bridge
        self.loop = asyncio.new_event_loop()
        self.error: str | None = None
        self.thread = threading.Thread(target=self._serve, daemon=True, name="bridge")
        self.thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.bridge.run())
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.bridge.ready.set()
            self.loop.close()

    def stop(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.bridge.request_stop)
        except RuntimeError:
            pass  # loop already closed
        self.thread.join(timeout=30)


def run_one(spec: dict[str, Any], lane: "Lane | None" = None, on_event=None, stop_event=None) -> dict[str, Any]:
    """spec: persona, task, site, run_index, port, out_dir, headless, model (optional).
    With a lane, the run uses the lane's port; with lane.reuse_channel it also reuses its bridge and H channel."""
    persona = PERSONAS[spec["persona"]]
    task, site = spec["task"], spec["site"]
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = (f"{task['id']}__{persona.id}__{spec['run_index']}" + (f"__r{spec['round']}" if spec.get("round") else "")
           + (f"__retry{spec['retry']}" if spec.get("retry") else ""))
    api_key = os.environ["HAI_API_KEY"]
    os.environ["HAI_AUTO_BRIDGE"] = "0"  # we serve the browser ourselves (PersonaBridge), not the SDK
    auth = site.get("auth") or {}
    login = auth.get("login")
    record: dict[str, Any] = {"persona": persona.id, "task": task["id"], "run": spec["run_index"], "tag": tag}

    port = lane.port if lane is not None else spec["port"]
    chrome = devtools = bridge = serving = handle = None
    monitor_done = threading.Event()
    monitor = None
    started = time.monotonic()
    try:
        chrome = ChromeProcess(port, persona, headless=spec.get("headless", True))
        devtools = DevToolsController(port, persona, headers=auth.get("headers"),
                                      patch_script=spec.get("patch_script"), capture_pages=True)
        record["patched"] = bool(spec.get("patch_script"))
        devtools.navigate(site["start_url"])
        login_mode = spec.get("login_mode", "tool")  # "tool" (fill_credential) or "vault" (driver vault hook)
        record["login_mode"] = login_mode
        vault = None
        if login and login_mode == "vault":
            from urllib.parse import urlparse

            vault = LocalVault(login, urlparse(site["start_url"]).hostname)
        def fill_credential(field: str) -> str:
            """Type the stored test-account credential into the input field that currently has keyboard focus.
            field: "username" (or email) or "password". Focus the field first. You never see the value."""
            key = "password" if field.lower().startswith("pass") else "username"
            bridge.actions.append({"t": round(time.monotonic() - bridge.t0, 2), "cmd": "tool:fill_credential", "field": key})
            if not login or key not in login:
                return f"No stored {key} for this site."
            from hai_drivers.web.interface import assert_secret_origin
            from urllib.parse import urlparse

            assert_secret_origin(bridge._driver.current_url(), urlparse(site["start_url"]).hostname)
            bridge._driver.input_text(str(login[key]))
            return f"Typed the stored {key} into the focused field."

        client = Client()
        on_wait = (lambda q: on_event({"waiting": True, "message": f"Waiting for a free H slot ({q.active}/{q.limit} "
                                       "busy; finished browser channels are released by H after a while)."})) \
            if on_event is not None else None
        reuse = lane is not None and lane.reuse_channel
        if reuse and lane.alive():
            lane.wait_previous_released(client, tag)
        # A self-hosted browser channel is itself a trajectory holding a session slot, and opening one at capacity
        # is refused (429). Runs therefore go one at a time through this cross-process lock, from the slot check
        # until their agent session exists, so two runs never count the same free slots.
        lock = open(CHANNEL_LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            seed = hash(tag) & 0xFFFF
            if reuse and lane.alive():
                bridge, serving = lane.bridge, lane.serving
                bridge.start_run(persona, vault, seed)
                record["channel_id"] = bridge.session_id
                wait_for_slots(client, needed=1, tag=tag, stop_event=stop_event, on_wait=on_wait)  # our channel holds its slot
            else:
                channel_id = lane.channel_id if reuse else spec.get("channel_id")  # None = new channel
                if channel_id is None and not reuse:
                    channel_id = acquire_channel(client, tag, stop_event=stop_event, on_wait=on_wait)
                    record["recycled_channel"] = channel_id is not None
                else:
                    # a new channel and the agent session each take a slot; a pending channel already holds its own
                    wait_for_slots(client, needed=1 if channel_status(channel_id) == "pending" else 2, tag=tag,
                                   stop_event=stop_event, on_wait=on_wait)
                record["channel_id"] = channel_id
                # The SDK already retries channel setup using the same UUID. Creating new
                # bridges here can leave extra channel trajectories holding quota slots.
                bridge = PersonaBridge(persona, port, api_key, rng_seed=seed, vault=vault, channel_id=channel_id)
                record["channel_id"] = bridge.session_id
                if not reuse:
                    pool_register(bridge.session_id)
                serving = BridgeThread(bridge)
                if not (bridge.ready.wait(90) and bridge._driver is not None and serving.error is None):
                    raise RuntimeError(f"local browser bridge did not become ready: {serving.error}")
                if reuse:
                    lane.bridge, lane.serving = bridge, serving
            agent = {
                "name": f"usability-tester-{persona.id.replace('_', '-')}",
                "description": f"Simulated website user: {persona.label}",
                "instructions": build_instructions(persona, login_mode if login else None),
                "environments": [{"id": "browser", "kind": "web", "host": "user_device", "session_id": bridge.session_id}],
            }
            if spec.get("model"):
                agent["model"] = spec["model"]
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("Audit stopped before starting an H session")
            message = (
                f"The website is already open in the browser at {site['start_url']}.\n"
                f"Your goal as this user: {task['goal']}"
            )
            handle = client.start_session(
                agent=agent,
                messages=message,
                answer_schema=RunReport,
                tools=[fill_credential] if login and login_mode == "tool" else None,
                max_steps=int(task.get("max_steps", 30)),
                max_time_s=float(task.get("max_time_s", 600)),
                group_id=spec.get("group_id"),
            )
            time.sleep(CHANNEL_SPACING_S)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        record["session_id"] = handle.id
        record["agent_view_url"] = f"https://platform.hcompany.ai/agents/sessions/{handle.id}"
        print(f"[{tag}] started {record['agent_view_url']}", flush=True)
        if reuse:
            lane.prev_session_id = handle.id
        if on_event is not None:
            on_event({"session_id": handle.id, "agent_view_url": record["agent_view_url"],
                      "message": "H session connected. The agent is navigating the website."})

        live_name = spec.get("live_name", "live.png")

        def observe():
            last_cancel = 0.0
            feed_index, feed_events, feed_client, feed_steps = 0, [], None, 0
            while not monitor_done.wait(3):
                if stop_event is not None and stop_event.is_set() and time.monotonic() - last_cancel > 15:
                    try:
                        handle.cancel()
                        last_cancel = time.monotonic()
                    except Exception:
                        pass  # keep the bridge alive while H finishes or retries cancellation
                if on_event is not None:
                    data = {"actions": len(bridge.actions),
                            "last_action": bridge.actions[-1]["cmd"] if bridge.actions else "Observing the page"}
                    try:
                        temp_image = out_dir / f"{live_name}.tmp"
                        temp_image.write_bytes(devtools.screenshot())
                        temp_image.replace(out_dir / live_name)
                        data["preview_at"] = time.time()
                    except Exception:
                        pass
                    try:  # the agent's latest thought and action, for live captions
                        feed_client = feed_client or Client()
                        new, feed_index = fetch_events(feed_client, handle.id, from_index=feed_index, max_pages=2)
                        feed_events = (feed_events + new)[-60:]
                        caption = latest_thought(feed_events)
                        if caption:
                            data.update(caption)
                        feed_steps += sum(1 for e in new if (e.get("data") or {}).get("kind") == "policy_event")
                        data["step"] = feed_steps
                    except Exception:
                        pass
                    on_event(data)
            if feed_client is not None:
                try:
                    feed_client.close()
                except Exception:
                    pass

        if on_event is not None or stop_event is not None:
            monitor = threading.Thread(target=observe, daemon=True)
            monitor.start()
        agent_started = time.monotonic()
        result = wait_with_watchdog(handle, total_s=float(task.get("max_time_s", 600)) + 120, tag=tag)
        record["agent_duration_s"] = round(time.monotonic() - agent_started, 1)  # excludes waiting for H slots

        record["status"] = str(result.status)
        record["outcome"] = result.outcome
        record["error"] = result.error
        try:
            final = handle.status()
            record["steps"] = final.steps
            changes = handle.changes(from_index=0)
            metrics = (changes.model_dump(mode="json").get("metrics") if changes else None) or {}
            record["cost_usd"] = metrics.get("total_cost")
        except Exception as exc:
            record["status_error"] = str(exc)
        report = result.answer
        record["report"] = report.model_dump(mode="json") if isinstance(report, BaseModel) else report
        try:
            (out_dir / f"{tag}.png").write_bytes(devtools.screenshot())
            record["final_screenshot"] = f"{tag}.png"
        except Exception as exc:
            record["screenshot_error"] = str(exc)
    except Exception as exc:
        record["status"] = "harness_error"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["stalled"] = str(exc).startswith("session stalled")
        traceback.print_exc()
    finally:
        monitor_done.set()
        if monitor is not None:
            monitor.join(timeout=12)
        if handle is not None:
            record["session_closed"] = close_h_session(client, handle.id)
            if not record["session_closed"]:
                record["cleanup_warning"] = "H has not confirmed session closure. Check Agent View before retrying."
                # Do not start another run on a channel still owned by the preceding agent.
                record["status"] = "harness_error"
                if on_event is not None:
                    on_event({"message": record["cleanup_warning"]})
        record["duration_s"] = round(time.monotonic() - started, 1)
        if bridge is not None:
            record["actions"] = [a for a in bridge.actions if a["cmd"] != "destroy"]
            record["commands_served"] = bridge.dispatched
        if handle is not None and spec.get("replay", True):
            try:  # step-by-step replay from H's history: screenshots, thoughts, actions, persona effects
                with Client() as replay_client:
                    events, _ = fetch_events(replay_client, handle.id)
                record["replay"] = build_replay(events, record.get("actions", []), out_dir, tag, api_key)
            except Exception as exc:
                record["replay_error"] = f"{type(exc).__name__}"
        # Each cleanup step on its own, so one failure never leaves a Chrome (and its port) behind.
        try:
            if lane is not None and lane.bridge is bridge and bridge is not None:
                bridge.end_run()  # keep the lane's bridge and channel for the next run
                if record.get("status") == "harness_error":
                    lane.close(rotate_channel=True)  # next run: fresh bridge on a fresh channel
            elif serving is not None:
                if handle is not None and not record.get("stalled"):
                    wait_terminal(handle, timeout_s=45)  # H sends a final `destroy` after the answer: serve it
                with Client() as cleanup_client:
                    record["channel_closed"] = close_h_session(cleanup_client, bridge.session_id)
                serving.stop()
                # A channel H still holds as pending is recycled by the next run (not after a technical error).
                pool_release(bridge.session_id, reusable=record.get("status") != "harness_error"
                             and record.get("session_closed") is not False)
        except Exception as exc:
            record["cleanup_error"] = f"bridge: {exc}"
        if devtools is not None:
            try:  # every page this user saw, as rendered: the input for fix suggestions and before/after images
                devtools.snapshot_page()
                snap_dir = out_dir / "snapshots"
                snap_dir.mkdir(exist_ok=True)
                record["snapshots"] = []
                for i, (url, html) in enumerate(devtools.snapshots.items()):
                    name = f"{tag}__{i}.html"
                    (snap_dir / name).write_text(html)
                    record["snapshots"].append({"url": url, "file": f"snapshots/{name}"})
            except Exception as exc:
                record["snapshot_error"] = str(exc)
            record["devtools_errors"] = devtools.errors
            try:
                devtools.close()
            except Exception:
                pass
        if chrome is not None:
            try:
                chrome.close()
            except Exception as exc:
                record["cleanup_error"] = f"chrome: {exc}"

    browser_actions = [a for a in record.get("actions", []) if not a["cmd"].startswith("tool:")]
    if record.get("steps") and not browser_actions and not record.get("commands_served"):
        # The agent acted but none of its browser commands went through PersonaBridge._dispatch, so persona
        # filters were not applied: the run must not be trusted.
        record["harness_warning"] = "no browser action went through the persona bridge; result invalid"
        record["status"] = "harness_error"
    expected = task.get("expect_answer_contains")
    rep = record.get("report") or {}
    claimed = bool(rep.get("completed"))
    if expected is not None:
        record["success"] = claimed and expected.lower() in str(rep.get("answer", "")).lower()
    else:
        record["success"] = claimed
    record["metrics"] = run_metrics(record)
    (out_dir / f"{tag}.json").write_text(json.dumps(record, indent=2, default=str))
    print(f"[{tag}] done success={record['success']} steps={record.get('steps')} {record['duration_s']}s", flush=True)
    return record

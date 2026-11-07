"""One audit run = one persona trying one task once, in its own Chrome, driven by an H agent.

The H agent runs in H's cloud; its browser actions come back to this process through the H "your browser"
bridge (host: user_device), which we subclass to filter inputs (tremor, keyboard only) and log every action.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
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

CHANNEL_LOCK = Path(tempfile.gettempdir()) / "h-audit-channel.lock"
CHANNEL_SPACING_S = 3

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
        self.end_run()
        self._driver = self.create_driver()

    def end_run(self) -> None:
        old, self._driver = self._driver, None
        if old is not None:
            try:
                old.destroy()  # stops this run's chromedriver; the browser itself is closed by ChromeProcess
            except Exception:
                pass


class Lane:
    """One parallel lane: a fixed port and one long-lived bridge on one H channel, shared by all its runs.

    Each channel is a trajectory holding a session slot until H's periodic cleanup marks it failed; a failed
    channel no longer delivers commands to new sessions. So a lane opens a fresh channel once per audit and
    reuses it for every run (no slot leaked per run), instead of reusing ids across audits."""

    def __init__(self, index: int, port: int) -> None:
        import uuid

        self.index, self.port = index, port
        self.channel_id = str(uuid.uuid4())
        self.bridge: PersonaBridge | None = None
        self.serving: BridgeThread | None = None

    def alive(self) -> bool:
        return (self.serving is not None and self.serving.thread.is_alive() and self.serving.error is None
                and self.bridge is not None)

    def close(self) -> None:
        if self.serving is not None:
            self.serving.stop()
        self.bridge = self.serving = None

    def _dispatch(self, name: str, args: dict[str, Any]):  # runs on a worker thread per command
        p = self.persona
        rec: dict[str, Any] = {"t": round(time.monotonic() - self.t0, 2), "cmd": name}
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
        if error:
            rec["error"] = error[:300]
        if name not in QUIET_COMMANDS or error:
            self.actions.append(rec)
        return result, error


def channel_status(channel_id: str | None) -> str | None:
    """Status of an existing channel trajectory, or None if it does not exist yet."""
    if not channel_id:
        return None
    import httpx
    from hai_agents_local.config import default_base_url

    r = httpx.get(f"{default_base_url().rstrip('/')}/api/v1/trajectories/{channel_id}",
                  headers={"Authorization": f"Bearer {os.environ['HAI_API_KEY']}"}, timeout=20)
    return r.json().get("status") if r.status_code == 200 else None


def wait_for_slots(client: Client, needed: int, tag: str, timeout_s: float = 2400) -> None:
    """A self-hosted browser channel is itself a trajectory on H and holds a concurrency slot until its session
    ends (or ~30 min idle timeout), so a run needs 2 free slots. Wait for them instead of hammering the API."""
    deadline = time.monotonic() + timeout_s
    while True:
        quota = client.sessions.get_session_quota()
        if quota.available is None or quota.available >= needed:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"no free H session slots after {timeout_s:.0f}s ({quota.active}/{quota.limit} busy)")
        print(f"[{tag}] waiting for H slots: {quota.active}/{quota.limit} busy", flush=True)
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


def run_one(spec: dict[str, Any], lane: "Lane | None" = None) -> dict[str, Any]:
    """spec: persona, task, site, run_index, port, out_dir, headless, model (optional).
    With a lane, the run reuses the lane's bridge and H channel (and its port) instead of opening its own."""
    persona = PERSONAS[spec["persona"]]
    task, site = spec["task"], spec["site"]
    out_dir = Path(spec["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{task['id']}__{persona.id}__{spec['run_index']}"
    api_key = os.environ["HAI_API_KEY"]
    os.environ["HAI_AUTO_BRIDGE"] = "0"  # we serve the browser ourselves (PersonaBridge), not the SDK
    auth = site.get("auth") or {}
    login = auth.get("login")
    record: dict[str, Any] = {"persona": persona.id, "task": task["id"], "run": spec["run_index"], "tag": tag}

    port = lane.port if lane is not None else spec["port"]
    chrome = ChromeProcess(port, persona, headless=spec.get("headless", True))
    devtools = bridge = serving = handle = None
    started = time.monotonic()
    try:
        devtools = DevToolsController(port, persona, headers=auth.get("headers"))
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
            bridge._driver.input_text(str(login[key]))
            return f"Typed the stored {key} into the focused field."

        client = Client()
        # A self-hosted browser channel is itself a trajectory holding a session slot, and opening one at capacity
        # is refused (429). Runs therefore go one at a time through this cross-process lock, from the slot check
        # until their agent session exists, so two runs never count the same free slots.
        lock = open(CHANNEL_LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            seed = hash(tag) & 0xFFFF
            if lane is not None and lane.alive():
                bridge, serving = lane.bridge, lane.serving
                bridge.start_run(persona, vault, seed)
                record["channel_id"] = bridge.session_id
                wait_for_slots(client, needed=1, tag=tag)  # our channel already holds its slot
            else:
                channel_id = lane.channel_id if lane is not None else spec.get("channel_id")
                record["channel_id"] = channel_id
                # a new channel and the agent session each take a slot; an open (pending) channel already holds its own
                wait_for_slots(client, needed=1 if channel_status(channel_id) == "pending" else 2, tag=tag)
                for attempt in range(4):
                    bridge = PersonaBridge(persona, port, api_key, rng_seed=seed, vault=vault, channel_id=channel_id)
                    serving = BridgeThread(bridge)
                    if bridge.ready.wait(90) and bridge._driver is not None and serving.error is None:
                        break
                    print(f"[{tag}] bridge not ready ({serving.error}); retry {attempt + 1}", flush=True)
                    serving.stop()
                    time.sleep(30 + 15 * attempt)
                else:
                    raise RuntimeError(f"local browser bridge did not become ready: {serving.error}")
                if lane is not None:
                    lane.bridge, lane.serving = bridge, serving
            agent = {
                "name": f"usability-tester-{persona.id.replace('_', '-')}",
                "description": f"Simulated website user: {persona.label}",
                "instructions": build_instructions(persona, login_mode if login else None),
                "environments": [{"id": "browser", "kind": "web", "host": "user_device", "session_id": bridge.session_id}],
            }
            if spec.get("model"):
                agent["model"] = spec["model"]
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
        agent_started = time.monotonic()
        result = handle.wait_for_completion(timeout_seconds=float(task.get("max_time_s", 600)) + 120)
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
        if record["status"] == "idle":
            try:  # answered: end it now so it frees its concurrency slot instead of idling
                handle.cancel()
            except Exception:
                pass
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
        traceback.print_exc()
        if handle is not None:  # never leave a session running on H once we stop serving its browser
            try:
                handle.cancel()
            except Exception:
                pass
    finally:
        record["duration_s"] = round(time.monotonic() - started, 1)
        if bridge is not None:
            record["actions"] = [a for a in bridge.actions if a["cmd"] != "destroy"]
        if lane is not None and lane.bridge is bridge and bridge is not None:
            bridge.end_run()  # keep the lane's bridge and channel for the next run
            if record.get("status") == "harness_error":
                lane.close()  # start the next run from a fresh bridge (same channel)
        elif serving is not None:
            serving.stop()
        if devtools is not None:
            record["devtools_errors"] = devtools.errors
            devtools.close()
        chrome.close()

    expected = task.get("expect_answer_contains")
    rep = record.get("report") or {}
    claimed = bool(rep.get("completed"))
    if expected is not None:
        record["success"] = claimed and expected.lower() in str(rep.get("answer", "")).lower()
    else:
        record["success"] = claimed
    (out_dir / f"{tag}.json").write_text(json.dumps(record, indent=2, default=str))
    print(f"[{tag}] done success={record['success']} steps={record.get('steps')} {record['duration_s']}s", flush=True)
    return record

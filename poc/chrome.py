"""Launch a dedicated Chrome per run and keep a DevTools connection that applies the persona's rendering
(vision deficiency, media features) and the site's auth headers to every tab, including tabs opened later."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import websocket

from personas import Persona

DEFAULT_CHROME = Path.home() / ".cache/selenium/chrome/linux64/157.0.8103.38/chrome"
WINDOW = (1280, 900)


def chrome_binary() -> str:
    path = os.environ.get("AUDIT_CHROME") or str(DEFAULT_CHROME)
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Chrome not found at {path}. Set AUDIT_CHROME, or download Chrome for Testing with Selenium Manager."
        )
    return path


def ensure_chromedriver_on_path() -> None:
    """Parallel runs must not all ask Selenium Manager for a driver at once (they collide on its cache).
    hai_drivers uses the chromedriver on PATH when its version matches the browser, so put ours there."""
    driver_dir = os.environ.get("AUDIT_CHROMEDRIVER_DIR") or str(
        Path.home() / ".cache/selenium/chromedriver/linux64" / Path(chrome_binary()).parent.name
    )
    if Path(driver_dir, "chromedriver").exists() and driver_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = driver_dir + os.pathsep + os.environ.get("PATH", "")


def _listening(port: int) -> bool:
    try:
        return httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.0, trust_env=False).status_code == 200
    except httpx.HTTPError:
        return False


class ChromeProcess:
    def __init__(self, port: int, persona: Persona, headless: bool = True) -> None:
        self.port = port
        ensure_chromedriver_on_path()
        self.user_data_dir = tempfile.mkdtemp(prefix=f"audit-chrome-{persona.id}-")
        args = [
            chrome_binary(),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={self.user_data_dir}",
            # Same physical screen for everyone; zoom shrinks the CSS viewport like Ctrl+ would.
            f"--window-size={int(WINDOW[0] / persona.zoom)},{int(WINDOW[1] / persona.zoom)}",
            f"--force-device-scale-factor={persona.zoom:g}",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--disable-search-engine-choice-screen",
            "--disable-features=Translate",
            *persona.chrome_flags,
        ]
        if headless:
            args.append("--headless=new")
        args.append("about:blank")
        deadline = time.monotonic() + 10  # a lane reuses its port: give the previous run's Chrome time to exit
        while _listening(port):
            if time.monotonic() > deadline:
                raise RuntimeError(f"port {port} is already used by another Chrome")
            time.sleep(0.3)
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while not _listening(port):
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"Chrome failed to open debugging port {port}")
            time.sleep(0.2)

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.user_data_dir, ignore_errors=True)


class DevToolsController:
    """Browser-level DevTools session. Auto-attaches to every tab and applies the persona before it renders.

    Emulation only lasts while this connection stays open, so it lives for the whole run."""

    def __init__(self, port: int, persona: Persona, headers: dict[str, str] | None = None) -> None:
        self.persona = persona
        self.headers = headers or {}
        info = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=5, trust_env=False).json()
        self.ws = websocket.create_connection(info["webSocketDebuggerUrl"], suppress_origin=True, timeout=None)
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._events: dict[int, threading.Event] = {}
        self.page_sessions: list[str] = []
        self.errors: list[str] = []
        self._closed = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name="devtools")
        self._thread.start()
        self.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        deadline = time.monotonic() + 10
        while not self.page_sessions and time.monotonic() < deadline:
            time.sleep(0.05)

    # -- protocol plumbing ---------------------------------------------------------------
    def _send(self, method: str, params: dict | None = None, session_id: str | None = None) -> int:
        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            self._events[msg_id] = threading.Event()
            msg: dict = {"id": msg_id, "method": method, "params": params or {}}
            if session_id:
                msg["sessionId"] = session_id
            self.ws.send(json.dumps(msg))
        return msg_id

    def call(self, method: str, params: dict | None = None, session_id: str | None = None, timeout: float = 10) -> dict:
        msg_id = self._send(method, params, session_id)
        if not self._events[msg_id].wait(timeout):
            raise TimeoutError(f"DevTools {method} timed out")
        reply = self._pending.pop(msg_id)
        if "error" in reply:
            raise RuntimeError(f"DevTools {method} failed: {reply['error']}")
        return reply.get("result", {})

    def _loop(self) -> None:
        while not self._closed:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                return
            if "id" in msg:
                self._pending[msg["id"]] = msg
                ev = self._events.pop(msg["id"], None)
                if ev:
                    ev.set()
            elif msg.get("method") == "Target.attachedToTarget":
                threading.Thread(target=self._on_attach, args=(msg["params"],), daemon=True).start()

    def _on_attach(self, params: dict) -> None:
        sid = params["sessionId"]
        ttype = params["targetInfo"]["type"]
        try:
            if ttype in ("page", "iframe"):
                self._apply(sid, is_page=ttype == "page")
        except Exception as exc:  # keep going: a failed emulation must show up in the report, not hang the tab
            self.errors.append(f"{ttype}: {exc}")
        finally:
            try:
                self._send("Runtime.runIfWaitingForDebugger", session_id=sid)
            except Exception:
                pass
        if ttype == "page":
            self.page_sessions.append(sid)

    def _apply(self, sid: str, is_page: bool) -> None:
        p = self.persona
        if is_page and p.vision_deficiency:
            self.call("Emulation.setEmulatedVisionDeficiency", {"type": p.vision_deficiency}, sid)
        if is_page and p.media_features:
            features = [{"name": n, "value": v} for n, v in p.media_features]
            self.call("Emulation.setEmulatedMedia", {"features": features}, sid)
        if self.headers:
            self.call("Network.enable", {}, sid)
            self.call("Network.setExtraHTTPHeaders", {"headers": self.headers}, sid)

    # -- helpers -------------------------------------------------------------------------
    def navigate(self, url: str) -> None:
        if not self.page_sessions:
            raise RuntimeError("no page attached")
        self.call("Page.navigate", {"url": url}, self.page_sessions[0])

    def screenshot(self) -> bytes:
        import base64

        sid = self.page_sessions[-1]
        return base64.b64decode(self.call("Page.captureScreenshot", {"format": "png"}, sid)["data"])

    def close(self) -> None:
        self._closed = True
        try:
            self.ws.close()
        except Exception:
            pass

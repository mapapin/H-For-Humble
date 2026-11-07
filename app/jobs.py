"""One server-wide audit at a time; one persistent H browser channel per audit."""
from __future__ import annotations

import multiprocessing as mp
import queue
import sys
import threading
import time
from pathlib import Path

from . import store

POC = store.ROOT / "poc"
sys.path.insert(0, str(POC))
from audit import summarize


def audit_worker(audit_id, config, credentials, out_dir, messages, stopped, paused):
    """Spawned process: credentials only arrive through process memory, never CLI or SQLite."""
    sys.path.insert(0, str(POC))
    from hai_agents import Client
    from runner import Lane, run_one

    terminal_message = None

    def emit(kind, **data):
        nonlocal terminal_message
        if kind in {"completed", "stopped", "failed"}:
            terminal_message = {"kind": kind, **data}
        else:
            messages.put({"kind": kind, **data})

    lane = None
    try:
        with Client() as client:
            quota = client.sessions.get_session_quota()
        if quota.available is not None and quota.available < 2:
            emit("failed", message=f"H has {quota.active}/{quota.limit} slots in use. This audit needs 2 free slots. "
                 "Wait for previous browser channels to be released, then try again.")
            return
        lane = Lane(0, port=9400)
        site = {"start_url": config["url"], "auth": credentials}
        completed = 0
        for task in config["tasks"]:
            for persona in config["personas"]:
                for run in range(config["repetitions"]):
                    while paused.is_set() and not stopped.wait(0.25):
                        pass
                    if stopped.is_set():
                        emit("stopped")
                        return
                    emit("run_started", persona=persona, task=task["id"], run=run,
                         completed=completed, message=f"Starting {persona.replace('_', ' ')} · {task['title']}")
                    spec = {
                        "persona": persona, "task": task, "site": site, "run_index": run,
                        "port": lane.port, "out_dir": out_dir, "headless": True,
                        "group_id": f"humble-{audit_id}", "login_mode": "tool",
                    }
                    record = run_one(spec, lane=lane, on_event=lambda data: emit("progress", **data),
                                     stop_event=stopped)
                    if record.get("stalled") and record.get("session_closed") and not stopped.is_set():
                        emit("progress", message="H session stalled. Retrying once with a fresh browser channel.")
                        previous = record.get("session_id")
                        record = run_one({**spec, "retry": 1}, lane=lane,
                                         on_event=lambda data: emit("progress", **data), stop_event=stopped)
                        record["retry_of"] = previous
                    # Never persist third-party exception text: it can contain request headers or credentials.
                    for key in ("error", "status_error", "screenshot_error"):
                        if record.get(key):
                            record[key] = "The browser or H session encountered a technical error. Check the local server terminal."
                    completed += 1
                    emit("run_finished", record=record, completed=completed)
                    if stopped.is_set():
                        emit("stopped")
                        return
                    if record.get("status") == "harness_error":
                        emit("failed", message="The browser connection failed. Remaining runs were not started to avoid occupying more H slots.")
                        return
        emit("completed")
    except Exception as exc:
        emit("failed", message=f"Unable to run the audit ({type(exc).__name__}). Check Chrome, network access and your H API key.")
    finally:
        credentials.clear()
        if lane is not None:
            emit("progress", message="Closing H sessions and browser channel.")
            try:
                if lane.close() is False:
                    messages.put({"kind": "cleanup_warning", "message":
                        "H has not confirmed browser channel closure. A slot may remain occupied until H cleanup."})
            except Exception:
                messages.put({"kind": "cleanup_warning", "message":
                    "Unable to verify H browser channel closure. Check H before starting another audit."})
        if terminal_message is not None:
            messages.put(terminal_message)


class JobManager:
    def __init__(self):
        self.ctx = mp.get_context("spawn")
        self.lock = threading.Lock()
        self.current = None

    def start(self, audit_id, config, credentials):
        with self.lock:
            if self.current is not None:
                return False
            messages = self.ctx.Queue()
            stopped, paused = self.ctx.Event(), self.ctx.Event()
            out_dir = store.DATA / "audits" / audit_id
            out_dir.mkdir(parents=True, exist_ok=True)
            process = self.ctx.Process(target=audit_worker,
                args=(audit_id, config, credentials, str(out_dir), messages, stopped, paused))
            self.current = {"id": audit_id, "process": process, "stop": stopped, "pause": paused}
            try:
                process.start()
            except Exception:
                self.current = None
                raise
            threading.Thread(target=self._watch, args=(audit_id, process, messages), daemon=True).start()
            return True

    def _watch(self, audit_id, process, messages):
        records, progress = [], {}
        terminal = False
        try:
            store.update(audit_id, status="running")
            store.event(audit_id, "started", "Audit started. Preparing a dedicated browser.")
            while True:
                try:
                    item = messages.get(timeout=0.5)
                except queue.Empty:
                    if process.is_alive():
                        continue
                    break
                kind = item.pop("kind")
                if kind in {"run_started", "progress"}:
                    if kind == "run_started":
                        progress = item
                    else:
                        progress.update(item)
                    store.update(audit_id, progress=progress)
                    if item.get("message"):
                        store.event(audit_id, kind, item["message"])
                elif kind == "run_finished":
                    records.append(item["record"])
                    progress["completed"] = item["completed"]
                    store.update(audit_id, results=records, summary=summarize(records), progress=progress)
                    r = item["record"]
                    store.event(audit_id, kind, f"{r['persona'].replace('_', ' ').capitalize()}: "
                                + ("journey completed" if r.get("success") else "journey not completed"))
                elif kind in {"completed", "stopped", "failed"}:
                    terminal = True
                    store.update(audit_id, status=kind, finished=time.time(), error=item.get("message"))
                    store.event(audit_id, kind, item.get("message") or
                                ("Audit completed. Your report is ready." if kind == "completed" else "Audit stopped. Partial results have been saved."))
                elif kind == "cleanup_warning":
                    store.event(audit_id, kind, item["message"])
            if not terminal:
                store.update(audit_id, status="failed", finished=time.time(),
                             error="The audit process exited unexpectedly. Check the local server terminal.")
        finally:
            process.join(timeout=2)
            messages.close()
            with self.lock:
                if self.current and self.current["id"] == audit_id:
                    self.current = None

    def control(self, audit_id, action):
        with self.lock:
            job = self.current
            if not job or job["id"] != audit_id or not job["process"].is_alive():
                return False
            if action == "stop":
                job["stop"].set()
                job["pause"].clear()
                store.update(audit_id, status="stopping")
            elif action == "pause":
                if job["stop"].is_set():
                    return False
                job["pause"].set()
                store.update(audit_id, status="paused")
            elif action == "resume":
                if job["stop"].is_set():
                    return False
                job["pause"].clear()
                store.update(audit_id, status="running")
            store.event(audit_id, action, {"stop": "Stop requested. Waiting for H to end the active session.",
                                         "pause": "Queue paused. The current run will finish; no new runs will start.",
                                         "resume": "Queue resumed."}[action])
            return True

    def shutdown(self):
        with self.lock:
            job = self.current
            if job:
                job["stop"].set()
                job["pause"].clear()
        if job:
            job["process"].join(timeout=30)
            # Do not kill a bridge while H is still sending it commands.


manager = JobManager()

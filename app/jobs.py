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
    from fixes import make_fixer, merge_patches, patch_script
    from pipeline import passes, persona_rates, score, suggest_fixes
    from render import build_comparisons

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
        # standard: one round, then suggested fixes. auto: audit -> fix -> re-audit until every perspective passes.
        # A re-evaluation is a standard audit whose config carries the patch to inject in every page.
        auto = config.get("mode") == "auto"
        max_rounds = int(config.get("max_rounds", 2)) if auto else 0
        patch = config.get("patch")
        fixer = make_fixer("auto")  # Devin when DEVIN_API_KEY/DEVIN_ORG_ID are set, else the demo stand-in
        completed, first_round, round_records, rows = 0, None, [], []
        for rnd in range(max_rounds + 1):
            script = patch_script(patch["fixes"]) if patch and patch.get("fixes") else None
            if rnd:
                emit("round_started", round=rnd, message=f"Round {rnd + 1}: re-evaluating every perspective with "
                     f"{len(patch['fixes'])} suggested fixes applied.")
            round_records = []
            for task in config["tasks"]:
                for persona in config["personas"]:
                    for run in range(config["repetitions"]):
                        while paused.is_set() and not stopped.wait(0.25):
                            pass
                        if stopped.is_set():
                            emit("stopped")
                            return
                        emit("run_started", persona=persona, task=task["id"], run=run, round=rnd,
                             completed=completed, message=f"Starting {persona.replace('_', ' ')} · {task['title']}")
                        spec = {
                            "persona": persona, "task": task, "site": site, "run_index": run,
                            "port": lane.port, "out_dir": out_dir, "headless": True,
                            "group_id": f"humble-{audit_id}", "login_mode": "tool",
                            "patch_script": script, "round": rnd,
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
                        for key in ("error", "status_error", "screenshot_error", "snapshot_error"):
                            if record.get(key):
                                record[key] = "The browser or H session encountered a technical error. Check the local server terminal."
                        record["round"] = rnd
                        round_records.append(record)
                        completed += 1
                        emit("run_finished", record=record, completed=completed)
                        if stopped.is_set():
                            emit("stopped")
                            return
                        if record.get("status") == "harness_error":
                            emit("failed", message="The browser connection failed. Remaining runs were not started to avoid occupying more H slots.")
                            return
            rows = summarize([r for r in round_records if "persona" in r and "task" in r])
            first_round = first_round or round_records
            entry = {"round": rnd, "summary": rows, "score": score(rows), "rates": persona_rates(rows),
                     "fixes_applied": [f["id"] for f in (patch or {}).get("fixes", [])], "passed": passes(rows)}
            if not auto or entry["passed"] or rnd == max_rounds:
                if auto:
                    emit("round_finished", entry=entry)
                break
            emit("progress", message="Some perspectives still fail. Asking for fixes.")
            new = fixer.generate(round_records, rows, out_dir, site, patch)
            if not new["fixes"]:
                entry["stopped"] = "No new fix to suggest."
                emit("round_finished", entry=entry)
                break
            patch = merge_patches(patch, new)
            entry["new_fixes"] = [f["id"] for f in new["fixes"]]
            emit("round_finished", entry=entry)
        # Suggested fixes with before/after images. Auto: the final patch on the pages users first saw.
        # Standard: ask the fixer about what failed (a re-evaluation only gets fixes it did not have yet).
        try:
            fix_dir = Path(out_dir) / "fixes"
            if auto and patch and patch.get("fixes"):
                emit("progress", message="Rendering before/after images of the applied fixes.")
                comparisons = build_comparisons(first_round, out_dir, patch, fix_dir, headers=credentials.get("headers"))
                emit("fixes", fixes={"source": fixer.source, "patch": patch, "comparisons": comparisons,
                                     "new_fix_ids": [f["id"] for f in patch["fixes"]], "created": time.time()})
            elif not auto and not config.get("patch") and any((r.get("success_rate") or 0) < 1 for r in rows):
                # (a re-evaluation compares with its parent audit instead: its snapshots already include the fixes)
                emit("progress", message="Asking for fix suggestions and rendering before/after images.")
                sug = suggest_fixes(round_records, rows, out_dir, site, fixer, previous=config.get("patch"),
                                    out_dir=fix_dir)
                emit("fixes", fixes=sug)
        except Exception as exc:
            emit("progress", message=f"Fix suggestions are unavailable ({type(exc).__name__}). The audit results are complete.")
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
        records, progress, rounds = [], {}, []
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
                if kind == "round_started":
                    records = []  # results/summary show the current round; earlier rounds live in `rounds`
                    store.update(audit_id, results=records, summary=[])
                    store.event(audit_id, "progress", item["message"])
                elif kind == "round_finished":
                    rounds.append(item["entry"])
                    store.update(audit_id, rounds=rounds)
                    e = item["entry"]
                    store.event(audit_id, "progress", f"Round {e['round'] + 1}: score {e['score']}/100"
                                + (" · every perspective passed." if e["passed"] else
                                   f" · {len(e.get('new_fixes', []))} new fixes suggested." if e.get("new_fixes") else "."))
                elif kind == "fixes":
                    store.update(audit_id, fixes=item["fixes"])
                    store.event(audit_id, "progress", f"{len(item['fixes']['patch']['fixes'])} suggested fixes ready, "
                                "with before/after images.")
                elif kind in {"run_started", "progress"}:
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

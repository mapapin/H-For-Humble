"""One server-wide audit at a time; its cases run in parallel lanes (one H browser channel each)."""
from __future__ import annotations

import multiprocessing as mp
import json
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

from . import store

POC = store.ROOT / "poc"
sys.path.insert(0, str(POC))
from audit import summarize
from scoring import scorecard


MAX_LANES = int(os.environ.get("HUMBLE_MAX_LANES", "5"))
BASE_PORT = 9400
LANE_STAGGER_S = 1.5  # between browser startups
SANITIZED_KEYS = ("error", "status_error", "screenshot_error", "snapshot_error", "cleanup_error")


def checkpoint_run(audit_id, record):
    """Publish a finished run independently of the process event queue."""
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT results,progress,config FROM audits WHERE id=?", (audit_id,)).fetchone()
        if row is None:
            return
        records, progress, config = (json.loads(row[k]) for k in ("results", "progress", "config"))
        rnd = record.get("round", 0)
        if records and max(r.get("round", 0) for r in records) > rnd:
            return
        records = [r for r in records if r.get("round", 0) == rnd and
                   (r.get("task"), r.get("persona"), r.get("run")) !=
                   (record.get("task"), record.get("persona"), record.get("run"))]
        records.append(record)
        progress["completed"] = len(records)
        if record.get("lane") is not None:
            lane = progress.setdefault("lanes", {}).setdefault(str(record["lane"]), {})
            lane.update(state="finished", tag=record.get("tag"), success=bool(record.get("success")),
                        error=record.get("status") == "harness_error", steps=record.get("steps"),
                        finished_at=time.time())
        db.execute("UPDATE audits SET results=?,summary=?,scores=?,progress=? WHERE id=?",
                   (json.dumps(records), json.dumps(summarize(records)),
                    json.dumps(scorecard(records, config.get("personas"))), json.dumps(progress), audit_id))


def recover_saved_runs():
    """Restore final reports left on disk if event delivery or the server stopped."""
    with store.connect() as db:
        audits = db.execute("SELECT id,results FROM audits").fetchall()
    for audit in audits:
        existing = {r.get("tag") for r in json.loads(audit["results"])}
        directory = store.DATA / "audits" / audit["id"]
        for path in sorted(directory.glob("*__*.json"), key=lambda p: p.stat().st_mtime):
            try:
                record = json.loads(path.read_text())
                if not all(k in record for k in ("task", "persona", "run", "tag", "success")):
                    continue
                if record["tag"] in existing:
                    continue
                # Older runner reports encode the round in their filename.
                record.setdefault("round", next((int(part[1:]) for part in record["tag"].split("__")
                                                   if part.startswith("r") and part[1:].isdigit()), 0))
                checkpoint_run(audit["id"], record)
            except (ValueError, OSError):
                continue


class AnyEvent:
    """Set when any of its events is set (user stop, or the worker's own circuit breaker)."""

    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(e.is_set() for e in self.events)

    def wait(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while not self.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(0.25, left))
        return True


def audit_worker(audit_id, config, credentials, out_dir, messages, stopped, paused):
    """Spawned process: credentials only arrive through process memory, never CLI or SQLite.

    Runs go in parallel lanes, one H browser channel each (2 H slots per lane: the channel + the agent session),
    as many lanes as free slots allow, up to one per case and MAX_LANES. Cases are queued perspective-first, so the
    first wave runs every perspective side by side."""
    sys.path.insert(0, str(POC))
    from hai_agents import Client
    from runner import Lane, run_one
    from fixes import make_fixer, merge_patches, patch_script
    from pipeline import passes, persona_rates, score, suggest_fixes
    from render import build_comparisons
    from scoring import scorecard
    from autofix import needs_fix

    terminal_message = None
    lock = threading.Lock()

    def emit(kind, **data):
        nonlocal terminal_message
        if kind in {"completed", "stopped", "failed"}:
            with lock:
                terminal_message = terminal_message or {"kind": kind, **data}
        else:
            messages.put({"kind": kind, **data})

    abort = threading.Event()
    halt = AnyEvent(stopped, abort)
    start_gate = threading.Semaphore(1)  # agents start one after another (see runner.run_one)
    opened_lanes = []

    def research():  # Linkup: declared accessibility compliance + legal context, shown next to the measured scores
        try:
            from research import available, is_public, site_context
            if not (available() and is_public(config["url"])):
                return
            context = site_context(config["url"])
            print(f"[research] Linkup context: {bool(context)}", flush=True)
            if context:
                emit("context", context=context)
        except Exception as exc:
            traceback.print_exc()
            emit("progress", message=f"Linkup research unavailable ({type(exc).__name__}).")

    if not config.get("patch"):
        threading.Thread(target=research, daemon=True, name="research").start()
    try:
        quota = None
        for attempt in range(4):  # a network blip at the start must not fail the whole audit
            try:
                with Client() as client:
                    quota = client.sessions.get_session_quota()
                break
            except Exception:
                if attempt == 3 or stopped.wait(3 * (attempt + 1)):
                    raise
        if quota.available is not None and quota.available < 1:
            emit("failed", message=f"H has {quota.active}/{quota.limit} slots in use. An agent needs at least 1 free "
                 "slot (2 without a browser channel to recycle). Wait for H to release slots, then try again.")
            return
        site = {"start_url": config["url"], "auth": credentials}
        # standard: one round, then suggested fixes. auto: audit -> fix -> re-audit until every perspective passes.
        # A re-evaluation is a standard audit whose config carries the patch to inject in every page.
        auto = config.get("mode") == "auto"
        max_rounds = int(config.get("max_rounds", 2)) if auto else 0
        patch = config.get("patch")
        fixer = make_fixer("auto")  # Devin when DEVIN_API_KEY/DEVIN_ORG_ID are set, else rules + demo stand-in
        def fix_progress(**details):
            emit("progress", phase="fixes", **details)
        fixer.on_progress = fix_progress
        if hasattr(fixer, "primary"):
            fixer.primary.on_progress = fix_progress

        def start_fixes():
            fix_progress(fix_status="starting", fix_provider=fixer.source,
                         fix_started_at=time.time(), devin_session_url=None,
                         message="Starting Devin…" if fixer.source == "devin" else "Preparing local fix suggestions…")
        state = {"completed": 0, "failed_cases": 0}
        first_round, round_records, rows = None, [], []
        personas, tasks, reps = config["personas"], config["tasks"], config["repetitions"]
        order = {(t["id"], p, r): i for i, (r, t, p) in enumerate((r, t, p) for r in range(reps) for t in tasks for p in personas)}
        wanted_lanes = int(config.get("parallel") or MAX_LANES)
        for rnd in range(max_rounds + 1):
            emit("progress", phase="testing", fix_status=None)
            script = patch_script(patch["fixes"]) if patch and patch.get("fixes") else None
            if rnd:
                emit("round_started", round=rnd, message=f"Round {rnd + 1}: re-evaluating every perspective with "
                     f"{len(patch['fixes'])} suggested fixes applied.")
            jobs = queue.Queue()
            for run in range(reps):
                for task in tasks:
                    for persona in personas:
                        jobs.put((task, persona, run))
            available = quota.available if quota.available is not None else 2 * MAX_LANES
            if rnd:
                try:
                    with Client() as client:
                        available = client.sessions.get_session_quota().available or 2
                except Exception:
                    available = 2
            # One lane per case up to MAX_LANES, whatever the free slots right now: a lane without a free slot waits
            # for one (shown as "waiting" in the UI) and starts as soon as H releases a slot.
            n_lanes = max(1, min(jobs.qsize(), wanted_lanes, MAX_LANES))
            emit("progress", message=f"Running {jobs.qsize()} cases on {n_lanes} parallel browser"
                 f"{'s' if n_lanes > 1 else ''} ({available} H slots free, 2 per agent).", lanes_total=n_lanes)
            round_records = []

            def lane_loop(idx, rnd=rnd, script=script, jobs=jobs, round_records=round_records):
                lane = Lane(idx, port=BASE_PORT + idx)
                with lock:
                    opened_lanes.append(lane)
                while not halt.is_set():
                    while paused.is_set() and not halt.wait(0.25):
                        pass
                    if halt.is_set():
                        return
                    try:
                        task, persona, run = jobs.get_nowait()
                    except queue.Empty:
                        return
                    emit("run_started", lane=idx, persona=persona, task=task["id"], run=run, round=rnd,
                         completed=state["completed"], message=f"Starting {persona.replace('_', ' ')} · {task['title']}")
                    spec = {
                        "persona": persona, "task": task, "site": site, "run_index": run,
                        "port": lane.port, "out_dir": out_dir, "headless": True,
                        "group_id": f"humble-{audit_id}", "login_mode": "tool",
                        "patch_script": script, "round": rnd, "live_name": f"live-{idx}.png",
                        "start_gate": start_gate,
                    }
                    on_event = lambda data, idx=idx: emit("progress", lane=idx, **data)
                    record = run_one(spec, lane=lane, on_event=on_event, stop_event=halt)
                    dead_recycled = record.get("recycled_channel") and record.get("stalled")
                    # A stalled session (no step, H sometimes never starts it) is retried even if H cannot confirm it
                    # closed: our browser is gone, so it can no longer act; it only holds a slot until H ends it.
                    if record.get("status") == "harness_error" and not halt.is_set() and (
                            record.get("session_closed") is not False or record.get("stalled")):
                        # One retry on a fresh browser channel: stalls, setup errors and a recycled channel that stopped
                        # delivering commands are transient. (A session stuck on a dead channel ends at its time limit.)
                        emit("progress", lane=idx, message=("The recycled browser channel did not respond"
                             if dead_recycled else "H did not start this session (no first step)" if record.get("stalled")
                             else "Technical error") + ", retrying once with a fresh browser channel.")
                        previous = record.get("session_id")
                        stuck_before = [previous] if previous and record.get("session_closed") is False else []
                        # (a retry does not wait long for a slot: stuck sessions on H may never free theirs)
                        record = run_one({**spec, "retry": 1, "no_recycle": True, "slot_timeout_s": 240}, lane=lane,
                                         on_event=on_event, stop_event=halt)
                        record["retry_of"] = previous
                        if stuck_before:
                            record["stuck_session_ids"] = stuck_before
                    # Never persist third-party exception text: it can contain request headers or credentials.
                    for key in SANITIZED_KEYS:
                        if record.get(key):
                            record[key] = "The browser or H session encountered a technical error. Check the local server terminal."
                    record.update(round=rnd, lane=idx)
                    with lock:
                        checkpoint_run(audit_id, record)
                        round_records.append(record)
                        state["completed"] += 1
                        if record.get("status") == "harness_error" and not halt.is_set():
                            state["failed_cases"] += 1
                            if state["failed_cases"] >= 2:
                                abort.set()  # something is wrong with Chrome, the network or H: stop using slots
                        completed = state["completed"]
                    emit("run_finished", lane=idx, record=record, completed=completed)

            threads = [threading.Thread(target=lane_loop, args=(i,), daemon=True, name=f"lane-{i}") for i in range(n_lanes)]
            for i, t in enumerate(threads):
                t.start()
                if i + 1 < len(threads):
                    time.sleep(LANE_STAGGER_S)
            for t in threads:
                t.join()
            if stopped.is_set():
                emit("stopped")
                return
            if abort.is_set():
                emit("failed", message="Two cases failed for technical reasons (browser or H connection). The audit "
                     "stopped to avoid occupying more H slots. Results collected so far are saved.")
                return
            round_records.sort(key=lambda r: order.get((r.get("task"), r.get("persona"), r.get("run")), 0))
            rows = summarize([r for r in round_records if "persona" in r and "task" in r])
            if rnd == 0 and not config.get("patch"):
                try:  # what a static checker (axe-core) sees on the same pages, to compare with what agents lived
                    from static_scan import scan_snapshots
                    emit("progress", message="Static scan (axe-core) of the pages the agents visited.")
                    emit("static", static=scan_snapshots(round_records, out_dir, headers=credentials.get("headers")))
                except Exception:
                    pass
            first_round = first_round or round_records
            card = scorecard(round_records, personas)
            entry = {"round": rnd, "summary": rows, "score": card["score"], "completion_score": score(rows),
                     "rates": persona_rates(rows), "scores": {k: v["score"] for k, v in card["perspectives"].items()},
                     "fixes_applied": [f["id"] for f in (patch or {}).get("fixes", [])], "passed": passes(rows)}
            if not auto or entry["passed"] or rnd == max_rounds:
                if auto:
                    emit("round_finished", entry=entry)
                break
            emit("progress", phase="fixes", message="Some perspectives still fail. Asking "
                 + ("Devin" if getattr(fixer, "source", "") == "devin" else "the fixer") + " for fixes.")
            start_fixes()
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
                fix_progress(fix_status="rendering", message="Preparing before/after previews of the applied fixes.")
                comparisons = build_comparisons(first_round, out_dir, patch, fix_dir, headers=credentials.get("headers"))
                emit("fixes", fixes={"source": fixer.source, "patch": patch, "comparisons": comparisons,
                                     "new_fix_ids": [f["id"] for f in patch["fixes"]], "created": time.time()})
            elif not auto and not config.get("patch") and needs_fix(round_records, rows):
                # (a re-evaluation compares with its parent audit instead: its snapshots already include the fixes)
                emit("progress", phase="fixes", message=("Devin is writing fixes for the perspectives that struggled "
                     "(a few minutes); the results above are final." if getattr(fixer, "source", "") == "devin" else
                     "Writing fix suggestions and rendering before/after images."))
                start_fixes()
                sug = suggest_fixes(round_records, rows, out_dir, site, fixer, previous=config.get("patch"),
                                    out_dir=fix_dir)
                emit("fixes", fixes=sug)
            fix_progress(fix_status="done", message="Fix preparation finished.")
        except Exception as exc:
            fix_progress(fix_status="error", message=f"Fix suggestions are unavailable ({type(exc).__name__}). The audit results are complete.")
        emit("completed")
    except Exception as exc:
        traceback.print_exc()
        emit("failed", message=f"Unable to run the audit ({type(exc).__name__}). Check Chrome, network access and your H API key.")
    finally:
        credentials.clear()
        if opened_lanes:
            emit("progress", message="Closing H sessions and browser channels.")
            unconfirmed = 0
            for lane in opened_lanes:
                try:
                    if lane.close() is False:
                        unconfirmed += 1
                except Exception:
                    unconfirmed += 1
            if unconfirmed:
                messages.put({"kind": "cleanup_warning", "message":
                    "H has not confirmed every browser channel closure. A slot may remain occupied until H cleanup."})
        if terminal_message is not None:
            messages.put(terminal_message)


class JobManager:
    def __init__(self):
        self.ctx = mp.get_context("spawn")
        self.lock = threading.Lock()
        self.current = None
        self.assisted: set[str] = set()  # H session ids a user steered (their runs are marked as assisted)

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
            threading.Thread(target=self._watch, args=(audit_id, process, messages, config["personas"]), daemon=True).start()
            return True

    def _watch(self, audit_id, process, messages, personas=None):
        records, progress, rounds = [], {"completed": 0, "lanes": {}}, []
        terminal = False

        def label(persona):
            return (persona or "").replace("_", " ").capitalize()

        try:
            store.update(audit_id, status="running")
            store.event(audit_id, "started", "Audit started. Preparing dedicated browsers.")
            while True:
                try:
                    item = messages.get(timeout=0.5)
                except queue.Empty:
                    if process.is_alive():
                        continue
                    break
                kind = item.pop("kind")
                lane = item.pop("lane", None)
                lanes = progress["lanes"]
                if kind == "round_started":
                    records = []  # results/summary show the current round; earlier rounds live in `rounds`
                    progress["lanes"] = {}
                    store.update(audit_id, results=records, summary=[], scores={}, progress=progress)
                    store.event(audit_id, "progress", item["message"])
                elif kind == "round_finished":
                    rounds.append(item["entry"])
                    store.update(audit_id, rounds=rounds)
                    e = item["entry"]
                    store.event(audit_id, "progress", f"Round {e['round'] + 1}: score {e['score']}/100"
                                + (" · every perspective passed." if e["passed"] else
                                   f" · {len(e.get('new_fixes', []))} new fixes suggested." if e.get("new_fixes") else "."))
                elif kind == "static":
                    store.update(audit_id, static_scan=item["static"])
                    t = item["static"].get("totals") or {}
                    if t:
                        store.event(audit_id, "progress", f"axe-core static scan: {t.get('rules', 0)} rule violations "
                                    f"on {len(item['static'].get('pages', []))} pages.")
                elif kind == "context":
                    store.update(audit_id, context=item["context"])
                    store.event(audit_id, "progress", "Linkup found the site's declared accessibility context.")
                elif kind == "fixes":
                    store.update(audit_id, fixes=item["fixes"])
                    store.event(audit_id, "progress", f"{len(item['fixes']['patch']['fixes'])} suggested fixes ready, "
                                "with before/after images.")
                elif kind == "run_started":
                    lanes[str(lane)] = {k: item[k] for k in ("persona", "task", "run", "round") if k in item}
                    lanes[str(lane)].update(state="starting", started_at=time.time())
                    progress.update({k: item[k] for k in ("persona", "task", "run") if k in item})  # latest case
                    store.update(audit_id, progress=progress)
                    store.event(audit_id, kind, item["message"])
                elif kind == "progress":
                    message = item.pop("message", None)
                    if lane is not None and str(lane) in lanes:
                        current = lanes[str(lane)]
                        if item.get("waiting") and current.get("state") == "waiting":
                            message = None  # already logged once for this lane
                        current.update(item)
                        if item.get("waiting"):
                            current["state"] = "waiting"
                        if item.get("session_id"):
                            current["state"] = "running"
                            current.pop("waiting", None)
                        if message:
                            current["message"] = message
                        if message:
                            store.event(audit_id, kind, f"{label(current.get('persona'))}: {message}")
                    else:
                        progress.update(item)
                        if message:
                            store.event(audit_id, kind, message)
                    store.update(audit_id, progress=progress)
                elif kind == "run_finished":
                    r = item["record"]
                    if r.get("session_id") in self.assisted:
                        r["assisted"] = True
                    if lane is not None:
                        r["lane"] = lane
                    checkpoint_run(audit_id, r)
                    with store.connect() as db:
                        saved = db.execute("SELECT results,progress FROM audits WHERE id=?", (audit_id,)).fetchone()
                    records, progress = json.loads(saved["results"]), json.loads(saved["progress"])
                    store.event(audit_id, kind, f"{label(r.get('persona'))}: "
                                + ("technical error" if r.get("status") == "harness_error" else
                                   "journey completed" if r.get("success") else "journey not completed"))
                elif kind in {"completed", "stopped", "failed"}:
                    terminal = True
                    for current in lanes.values():
                        if current.get("state") not in {"finished"}:
                            current["state"] = "cancelled"
                    store.update(audit_id, status=kind, finished=time.time(), error=item.get("message"), progress=progress)
                    store.event(audit_id, kind, item.get("message") or
                                ("Audit completed. Your report is ready." if kind == "completed" else "Audit stopped. Partial results have been saved."))
                elif kind == "cleanup_warning":
                    store.event(audit_id, kind, item["message"])
            if not terminal:
                store.update(audit_id, status="failed", finished=time.time(),
                             error="The audit process exited unexpectedly. Check the local server terminal.")
        except Exception:
            traceback.print_exc()
            store.update(audit_id, status="failed", finished=time.time(),
                         error="The live result feed stopped unexpectedly. Saved run reports remain available.")
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

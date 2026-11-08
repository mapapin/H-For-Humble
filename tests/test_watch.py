"""The job watcher turns worker messages into the audit row the UI polls: lanes, results, scores, events."""
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import store
from app.jobs import JobManager


class Messages:
    def __init__(self, items):
        self.q = queue.Queue()
        for i in items:
            self.q.put(i)

    def get(self, timeout=None):
        return self.q.get(timeout=timeout)

    def close(self):
        pass


def record(persona, success, steps=8):
    return {"persona": persona, "task": "journey-1", "run": 0, "tag": f"journey-1__{persona}__0", "status": "completed",
            "success": success, "steps": steps, "agent_duration_s": 30, "duration_s": 40, "actions": [],
            "report": {"completed": success, "answer": "", "summary": "", "obstacles": []}}


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = patch.object(store, "DATA", Path(self.tmp.name))
        self.storage.start()
        store.init()
        with store.connect() as db:
            db.execute("INSERT INTO users VALUES('u','U','u@x.test','x',0)")
            db.execute("INSERT INTO projects VALUES('p','u','site','http://x.test/',0)")
            db.execute("INSERT INTO audits(id,user_id,project_id,name,url,status,created,config) VALUES('a','u','p','A','http://x.test/','queued',0,'{}')")

    def tearDown(self):
        self.storage.stop()
        self.tmp.cleanup()

    def test_lanes_results_scores_and_terminal_state(self):
        msgs = Messages([
            {"kind": "run_started", "lane": 0, "persona": "baseline", "task": "journey-1", "run": 0, "round": 0, "message": "Starting baseline"},
            {"kind": "run_started", "lane": 1, "persona": "keyboard_only", "task": "journey-1", "run": 0, "round": 0, "message": "Starting keyboard"},
            {"kind": "progress", "lane": 1, "waiting": True, "message": "Waiting for a free H slot"},
            {"kind": "progress", "lane": 1, "waiting": True, "message": "Waiting for a free H slot"},
            {"kind": "progress", "lane": 0, "session_id": "s0", "message": "H session connected."},
            {"kind": "progress", "lane": 0, "step": 3, "thought": "Clicking Reserve"},
            {"kind": "run_finished", "lane": 0, "record": record("baseline", True), "completed": 1},
            {"kind": "run_finished", "lane": 1, "record": record("keyboard_only", False, 20), "completed": 2},
            {"kind": "completed"},
        ])
        JobManager()._watch("a", SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None), msgs,
                            ["baseline", "keyboard_only"])
        with store.connect() as db:
            a = store.serialize(db.execute("SELECT * FROM audits WHERE id='a'").fetchone())
            events = [r["message"] for r in db.execute("SELECT message FROM events WHERE audit_id='a'")]
        self.assertEqual(a["status"], "completed")
        self.assertEqual(a["progress"]["lanes"]["0"]["state"], "finished")
        self.assertEqual(a["progress"]["lanes"]["0"]["thought"], "Clicking Reserve")
        self.assertEqual(len(a["results"]), 2)
        self.assertEqual(a["scores"]["perspectives"]["baseline"]["score"], 100)
        self.assertEqual(a["scores"]["perspectives"]["keyboard_only"]["score"], 0)
        self.assertEqual(sum("Waiting for a free H slot" in e for e in events), 1)  # logged once per lane


if __name__ == "__main__":
    unittest.main()

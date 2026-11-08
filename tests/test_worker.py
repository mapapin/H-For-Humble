import queue
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.jobs import audit_worker
from runner import PersonaBridge, SeleniumBrowserBridge, close_h_session, wait_with_watchdog, Lane
from personas import PERSONAS


class WorkerTests(unittest.TestCase):
    def test_fresh_channels_are_the_default(self):
        self.assertFalse(Lane(0, 9400).reuse_channel)

    @staticmethod
    def clock(step=5.0):
        t = {"now": 0.0}

        def tick():
            t["now"] += step
            return t["now"]
        return tick

    @patch("runner.time.sleep")
    def test_watchdog_detects_no_progress(self, sleep):
        handle = MagicMock()
        handle.wait_for_completion.side_effect = TimeoutError()
        handle.status.return_value = SimpleNamespace(status="running", steps=3)
        with patch("runner.time.monotonic", side_effect=self.clock()), \
                self.assertRaisesRegex(TimeoutError, "no progress for 180s"):
            wait_with_watchdog(handle, total_s=600, tag="test", stall_s=180)

    @patch("runner.time.sleep")
    def test_watchdog_retries_a_session_that_never_makes_its_first_step_within_30s(self, sleep):
        handle = MagicMock()
        states = [SimpleNamespace(status="queued", steps=0)] * 10 + [SimpleNamespace(status="running", steps=0)] * 20
        handle.status.side_effect = states
        with patch("runner.time.monotonic", side_effect=self.clock()), \
                self.assertRaisesRegex(TimeoutError, "stalled at step 0"):
            wait_with_watchdog(handle, total_s=600, tag="test")
        # 10 checks while queued do not count; then about 30 s of running (every check is 5 s on this clock)
        self.assertLess(handle.status.call_count, 20)
        handle.wait_for_completion.assert_not_called()

    @patch("runner.time.sleep")
    def test_watchdog_hands_over_after_the_first_step(self, sleep):
        handle = MagicMock()
        handle.status.side_effect = [SimpleNamespace(status="running", steps=0), SimpleNamespace(status="running", steps=1)]
        handle.wait_for_completion.return_value = "done"
        with patch("runner.time.monotonic", side_effect=self.clock()):
            self.assertEqual(wait_with_watchdog(handle, total_s=600, tag="test"), "done")

    def test_worker_retries_stall_only_once_after_session_closed(self):
        messages = queue.Queue()
        config = self.config()
        config["personas"] = ["baseline"]
        with patch("hai_agents.Client") as client, patch("runner.Lane"), patch("runner.run_one") as run:
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3)
            run.side_effect = [{"status": "harness_error", "stalled": True, "session_closed": True, "session_id": "old"},
                               {"status": "completed", "success": True}]
            audit_worker("test-audit", config, {}, "/unused", messages, threading.Event(), threading.Event())
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[1].args[0]["retry"], 1)

    @patch("runner.time.sleep")
    def test_close_retries_until_cancellation_is_confirmed(self, sleep):
        client = MagicMock()
        client.sessions.get_session_status.side_effect = [
            SimpleNamespace(status="idle"), SimpleNamespace(status="running"),
            SimpleNamespace(status="interrupted")]
        self.assertTrue(close_h_session(client, "test-session"))
        self.assertEqual(client.sessions.cancel_session.call_count, 2)

    @patch("runner.time.sleep")
    def test_accepted_cancel_is_not_treated_as_closed(self, sleep):
        client = MagicMock()
        client.sessions.get_session_status.return_value = SimpleNamespace(status="pending")
        self.assertFalse(close_h_session(client, "test-channel"))
        self.assertEqual(client.sessions.cancel_session.call_count, 3)

    def test_completed_is_emitted_after_channel_cleanup(self):
        messages = queue.Queue()
        with patch("hai_agents.Client") as client, patch("runner.Lane") as lane, patch("runner.run_one", return_value={"status": "completed"}):
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3)
            def close():
                self.assertNotIn("completed", [m["kind"] for m in list(messages.queue)])
                return False
            lane.return_value.close.side_effect = close
            audit_worker("test-audit", self.config(), {}, "/unused", messages, threading.Event(), threading.Event())
            kinds = [messages.get()["kind"] for _ in range(messages.qsize())]
            self.assertEqual(kinds[-2:], ["cleanup_warning", "completed"])

    def config(self):
        return {"url": "https://example.test", "tasks": [{"id": "journey-1", "title": "Read", "goal": "Read the main heading."}],
                "personas": ["baseline", "keyboard_only"], "repetitions": 1}

    def test_quota_prevents_opening_a_channel(self):
        messages = queue.Queue()
        with patch("hai_agents.Client") as client, patch("runner.Lane") as lane:
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=0, active=3, limit=3)
            audit_worker("test-audit", self.config(), {}, "/unused", messages, threading.Event(), threading.Event())
            lane.assert_not_called()
            self.assertEqual(messages.get()["kind"], "failed")

    def test_one_lane_per_perspective(self):
        messages = queue.Queue()
        with tempfile.TemporaryDirectory() as folder, patch("hai_agents.Client") as client, patch("runner.Lane") as lane, patch("runner.run_one", return_value={"status": "completed", "success": True}) as run, patch("app.jobs.LANE_STAGGER_S", 0):
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3, active=0, limit=3)
            audit_worker("test-audit", self.config(), {}, folder, messages, threading.Event(), threading.Event())
            self.assertEqual(lane.call_count, 2)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(lane.return_value.close.call_count, 2)  # every lane closes its channel

    def test_technical_failures_are_retried_once_then_stop_the_audit(self):
        messages = queue.Queue()
        config = self.config()
        config["personas"] = ["baseline", "keyboard_only", "tremor"]
        with patch("hai_agents.Client") as client, patch("runner.Lane"), patch("runner.run_one", return_value={"status": "harness_error", "success": False}) as run:
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3, active=0, limit=3)
            audit_worker("test-audit", config, {}, "/unused", messages, threading.Event(), threading.Event())
            self.assertEqual(run.call_count, 4)  # 2 cases x (run + one retry); the third case never starts
            self.assertEqual(run.call_args_list[1].args[0]["retry"], 1)
            kinds = [messages.get()["kind"] for _ in range(messages.qsize())]
            self.assertEqual(kinds[-1], "failed")

    def test_perspectives_run_in_parallel_lanes(self):
        messages = queue.Queue()
        config = self.config()
        config["personas"] = ["baseline", "keyboard_only", "tremor", "low_vision", "color_blind"]
        started, peak, lock = [], [0], threading.Lock()
        active = [0]

        def fake_run(spec, lane=None, on_event=None, stop_event=None):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                started.append((spec["persona"], spec["port"], spec["live_name"]))
            import time as _t
            _t.sleep(0.2)
            with lock:
                active[0] -= 1
            return {"status": "completed", "success": True, "persona": spec["persona"], "task": spec["task"]["id"],
                    "run": spec["run_index"], "tag": spec["persona"]}

        with tempfile.TemporaryDirectory() as folder, patch("hai_agents.Client") as client, \
                patch("runner.Lane", side_effect=lambda i, port: SimpleNamespace(port=port, close=lambda: True)), \
                patch("runner.run_one", side_effect=fake_run), patch("app.jobs.LANE_STAGGER_S", 0):
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=10, active=0, limit=10)
            audit_worker("test-audit", config, {}, folder, messages, threading.Event(), threading.Event())
        self.assertEqual(len(started), 5)
        self.assertEqual(len({port for _, port, _ in started}), 5)  # one lane (port) per perspective
        self.assertEqual(len({name for _, _, name in started}), 5)  # one live preview per lane
        self.assertGreater(peak[0], 1)
        kinds = [messages.get()["kind"] for _ in range(messages.qsize())]
        self.assertEqual(kinds[-1], "completed")

    def test_every_case_gets_a_lane_even_when_slots_are_short(self):
        messages = queue.Queue()
        config = self.config()
        config["personas"] = ["baseline", "keyboard_only", "tremor", "low_vision", "color_blind"]
        with tempfile.TemporaryDirectory() as folder, patch("hai_agents.Client") as client, patch("runner.Lane") as lane, \
                patch("runner.run_one", return_value={"status": "completed", "success": True}), patch("app.jobs.LANE_STAGGER_S", 0):
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=5, active=5, limit=10)
            audit_worker("test-audit", config, {}, folder, messages, threading.Event(), threading.Event())
            self.assertEqual(lane.call_count, 5)  # every case gets a lane; lanes without a free slot wait in run_one

    def test_stopped_queue_does_not_launch_an_agent(self):
        stopped = threading.Event()
        stopped.set()
        messages = queue.Queue()
        with patch("hai_agents.Client") as client, patch("runner.Lane"), patch("runner.run_one") as run:
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3, active=0, limit=3)
            audit_worker("test-audit", self.config(), {}, "/unused", messages, stopped, threading.Event())
            run.assert_not_called()
            kinds = [messages.get()["kind"] for _ in range(messages.qsize())]
            self.assertEqual(kinds[-1], "stopped")

    def test_keyboard_filter_blocks_mouse_and_zoom_scales_coordinates(self):
        bridge = PersonaBridge(PERSONAS["keyboard_only"], 9400, "test-only-key", 1)
        self.assertIsNotNone(bridge._dispatch("click_at", {"x": 10, "y": 10})[1])
        self.assertTrue(bridge.actions[-1]["blocked"])
        bridge = PersonaBridge(PERSONAS["low_vision"], 9400, "test-only-key", 1)
        with patch.object(SeleniumBrowserBridge, "_dispatch", return_value=("ok", None)) as dispatch:
            bridge._dispatch("click_at", {"x": 100, "y": 60})
            dispatch.assert_called_once_with("click_at", {"x": 50, "y": 30})


class ChannelPoolTests(unittest.TestCase):
    def setUp(self):
        import runner
        self.runner = runner
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(runner, "POOL_FILE", __import__("pathlib").Path(self.tmp.name) / "pool.json"),
                        patch.object(runner, "CHANNEL_LOCK", __import__("pathlib").Path(self.tmp.name) / "lock")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_released_pending_channel_is_recycled_once(self):
        r = self.runner
        r.pool_register("chan-1")
        self.assertIsNone(r.take_recycled_channel())  # still in use by this process
        r.pool_release("chan-1")
        with patch.object(r, "channel_status", return_value="pending"), patch.object(r, "CHANNEL_REUSE_GRACE_S", 0):
            self.assertEqual(r.take_recycled_channel(), "chan-1")
            self.assertIsNone(r.take_recycled_channel())  # reserved now

    def test_released_channel_h_closed_is_dropped(self):
        r = self.runner
        r.pool_register("chan-2")
        r.pool_release("chan-2")
        with patch.object(r, "channel_status", return_value="failed"), patch.object(r, "CHANNEL_REUSE_GRACE_S", 0):
            self.assertIsNone(r.take_recycled_channel())
        self.assertEqual(r._pool_load(), {})

    def test_channel_after_technical_error_is_not_recycled(self):
        r = self.runner
        r.pool_register("chan-3")
        r.pool_release("chan-3", reusable=False)
        self.assertEqual(r._pool_load(), {})

    def test_acquire_prefers_recycled_then_fresh_then_waits(self):
        r = self.runner
        client = MagicMock()
        client.sessions.get_session_quota.return_value = SimpleNamespace(available=1, active=9, limit=10)
        with patch.object(r, "take_recycled_channel", return_value="chan-4"):
            self.assertEqual(r.acquire_channel(client, "t"), "chan-4")
        client.sessions.get_session_quota.return_value = SimpleNamespace(available=4, active=6, limit=10)
        with patch.object(r, "take_recycled_channel", return_value=None):
            self.assertIsNone(r.acquire_channel(client, "t"))
        stop = threading.Event()
        stop.set()
        client.sessions.get_session_quota.return_value = SimpleNamespace(available=1, active=9, limit=10)
        with patch.object(r, "take_recycled_channel", return_value=None), self.assertRaises(RuntimeError):
            r.acquire_channel(client, "t", stop_event=stop)


class StartGateTests(unittest.TestCase):
    def test_gate_is_released_when_the_run_fails_before_its_session(self):
        import runner
        gate = threading.Semaphore(1)
        spec = {"persona": "baseline", "task": {"id": "t", "goal": "Read the heading."}, "site": {"start_url": "http://x.test/"},
                "run_index": 0, "port": 9499, "out_dir": tempfile.mkdtemp(), "start_gate": gate}
        with patch.object(runner, "ChromeProcess"), patch.object(runner, "DevToolsController"), \
                patch.object(runner, "Client") as client, patch.object(runner, "CHANNEL_LOCK", Path(tempfile.mkdtemp()) / "lock"), \
                patch.dict("os.environ", {"HAI_API_KEY": "fixture-key"}):
            client.return_value.sessions.get_session_quota.side_effect = RuntimeError("H unreachable")
            record = runner.run_one(spec)  # fails after taking the gate, before any session
        self.assertEqual(record["status"], "harness_error")
        self.assertTrue(gate.acquire(blocking=False))  # not leaked


class StartupTests(unittest.TestCase):
    def test_network_blip_at_start_is_retried(self):
        messages = queue.Queue()
        calls = {"n": 0}

        def quota(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("dns")
            return SimpleNamespace(available=10, active=0, limit=10)

        with tempfile.TemporaryDirectory() as folder, patch("hai_agents.Client") as client, patch("runner.Lane"), \
                patch("runner.run_one", return_value={"status": "completed", "success": True}), \
                patch("app.jobs.LANE_STAGGER_S", 0), patch("app.jobs.time.sleep"):
            client.return_value.__enter__.return_value.sessions.get_session_quota.side_effect = quota
            config = {"url": "https://example.test", "tasks": [{"id": "journey-1", "title": "Read", "goal": "Read the heading."}],
                      "personas": ["baseline"], "repetitions": 1}
            stopped = threading.Event()
            with patch.object(stopped, "wait", return_value=False):
                audit_worker("test-audit", config, {}, folder, messages, stopped, threading.Event())
        kinds = [messages.get()["kind"] for _ in range(messages.qsize())]
        self.assertEqual(kinds[-1], "completed")
        self.assertGreaterEqual(calls["n"], 2)

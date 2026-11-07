import queue
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

    @patch("runner.time.monotonic", side_effect=[0, 0, 1, 2, 200])
    def test_watchdog_detects_no_progress(self, clock):
        handle = MagicMock()
        handle.wait_for_completion.side_effect = TimeoutError()
        handle.status.return_value = SimpleNamespace(steps=0)
        with self.assertRaisesRegex(TimeoutError, "session stalled"):
            wait_with_watchdog(handle, total_s=600, tag="test", stall_s=180)

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
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=1, active=2, limit=3)
            audit_worker("test-audit", self.config(), {}, "/unused", messages, threading.Event(), threading.Event())
            lane.assert_not_called()
            self.assertEqual(messages.get()["kind"], "failed")

    def test_one_lane_reused_for_all_personas(self):
        messages = queue.Queue()
        with tempfile.TemporaryDirectory() as folder, patch("hai_agents.Client") as client, patch("runner.Lane") as lane, patch("runner.run_one", return_value={"status": "completed", "success": True}) as run:
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3, active=0, limit=3)
            audit_worker("test-audit", self.config(), {}, folder, messages, threading.Event(), threading.Event())
            lane.assert_called_once_with(0, port=9400)
            self.assertEqual(run.call_count, 2)
            self.assertIs(run.call_args_list[0].kwargs["lane"], run.call_args_list[1].kwargs["lane"])
            lane.return_value.close.assert_called_once()

    def test_technical_failure_does_not_start_more_runs(self):
        messages = queue.Queue()
        with patch("hai_agents.Client") as client, patch("runner.Lane"), patch("runner.run_one", return_value={"status": "harness_error", "success": False}) as run:
            client.return_value.__enter__.return_value.sessions.get_session_quota.return_value = SimpleNamespace(available=3, active=0, limit=3)
            audit_worker("test-audit", self.config(), {}, "/unused", messages, threading.Event(), threading.Event())
            self.assertEqual(run.call_count, 1)
            kinds = [messages.get()["kind"] for _ in range(messages.qsize())]
            self.assertEqual(kinds[-1], "failed")

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

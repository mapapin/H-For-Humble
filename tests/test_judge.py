"""Holo judge: verdicts replace the exact-match rule when available, fall back to it otherwise, and the headline
never carries a number that is not in the results."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "poc"))
import judge  # noqa: E402

TASK = {"title": "Book", "goal": "Book 2 seats on the first December date and report the total.",
        "expect_answer_contains": "98.00; 8 Dec"}


def record(answer, completed=True, status="idle"):
    return {"persona": "tremor", "status": status, "steps": 9,
            "report": {"completed": completed, "answer": answer, "summary": "", "obstacles": []}}


class Reply:
    def __init__(self, content, status=200):
        self.status_code, self.text = status, content
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"HAI_API_KEY": "fixture-key", "HUMBLE_JUDGE": "on"})
        self.env.start()
        self.sleep = patch("judge.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.env.stop()

    def test_judge_accepts_another_format_and_keeps_the_rule_verdict(self):
        r = record("Total 98 € on Tuesday 8th of December")
        with patch("judge.httpx.post", return_value=Reply('```json\n{"success": true, "reason": "Same facts."}\n```')) as post:
            judge.decide(r, TASK, rule_success=False)
        self.assertTrue(r["success"])
        self.assertFalse(r["rule_success"])
        self.assertEqual(r["judge"]["reason"], "Same facts.")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn("98.00; 8 Dec", body["messages"][1]["content"])

    def test_judge_can_reject_a_claimed_success(self):
        r = record("Booked 18 Dec, 98.00")
        with patch("judge.httpx.post", return_value=Reply('{"success": false, "reason": "Wrong date."}')):
            judge.decide(r, TASK, rule_success=True)
        self.assertFalse(r["success"])

    def test_not_completed_and_technical_errors_are_never_sent_to_the_judge(self):
        with patch("judge.httpx.post") as post:
            r = record("I gave up", completed=False)
            judge.decide(r, TASK, rule_success=False)
            e = record("98.00 8 Dec", status="harness_error")
            judge.decide(e, TASK, rule_success=True)
        post.assert_not_called()
        self.assertFalse(r["success"])
        self.assertTrue(e["success"])  # the run is excluded from scores anyway

    def test_falls_back_to_the_rule_when_unavailable(self):
        r = record("98.00 on 8 Dec")
        with patch("judge.httpx.post", side_effect=OSError("down")):
            judge.decide(r, TASK, rule_success=True)
        self.assertTrue(r["success"])
        self.assertNotIn("judge", r)
        with patch.dict("os.environ", {"HUMBLE_JUDGE": "off"}), patch("judge.httpx.post") as post:
            judge.decide(record("x"), TASK, rule_success=False)
        post.assert_not_called()

    def test_rate_limit_switches_model(self):
        replies = [Reply('{"error": "Too Many Requests"}', 429), Reply('{"success": true, "reason": "ok"}')]
        with patch("judge.httpx.post", side_effect=replies) as post:
            verdict = judge.judge_run(record("98.00 8 Dec"), TASK)
        models = [c.kwargs["json"]["model"] for c in post.call_args_list]
        self.assertEqual(models, [judge.MODEL, judge.FALLBACK_MODELS[0]])
        self.assertEqual(verdict["model"], judge.FALLBACK_MODELS[0])

    def test_headline_is_grounded(self):
        card = {"score": 49, "can_complete": 3, "tested_perspectives": 5,
                "perspectives": {"baseline": {"score": 100, "completed": 1, "tested": 1},
                                 "keyboard_only": {"score": 0, "completed": 0, "tested": 1}}}
        good = '{"headline": "Keyboard users cannot pick a date; 3 of 5 users finished.", "next_step": "Make dates focusable."}'
        invented = '{"headline": "73% of users fail at checkout.", "next_step": ""}'
        with patch("judge.httpx.post", return_value=Reply(good)):
            out = judge.headline(card, [], [TASK])
        self.assertEqual(out["next_step"], "Make dates focusable.")
        with patch("judge.httpx.post", return_value=Reply(invented)) as post:
            self.assertIsNone(judge.headline(card, [], [TASK]))
        self.assertEqual(post.call_count, 2)  # one retry, then the UI writes it from rules
        data = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
        self.assertEqual(data["users_who_finished"], 3)


if __name__ == "__main__":
    unittest.main()

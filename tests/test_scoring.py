"""Scores: completion first, then effort and barriers compared with the baseline user."""
import unittest

from scoring import grade, scorecard


def rec(persona, success, steps=10, time_s=60, obstacles=(), actions=(), status="completed"):
    return {"persona": persona, "task": "t", "run": 0, "success": success, "steps": steps, "agent_duration_s": time_s,
            "status": status, "actions": list(actions),
            "report": {"obstacles": [{"severity": s, "what": "x", "where": "y"} for s in obstacles]}}


class ScoringTests(unittest.TestCase):
    def test_failed_journey_scores_zero_and_baseline_scores_full(self):
        card = scorecard([rec("baseline", True), rec("keyboard_only", False)], ["baseline", "keyboard_only"])
        self.assertEqual(card["perspectives"]["baseline"]["score"], 100)
        self.assertEqual(card["perspectives"]["keyboard_only"]["score"], 0)
        self.assertEqual(card["score"], 50)
        self.assertEqual(card["impaired_blocked"], 1)

    def test_extra_effort_and_barriers_cost_points_but_stay_above_failure(self):
        card = scorecard([rec("baseline", True, steps=10), rec("low_vision", True, steps=30, time_s=200,
                                                                 obstacles=["major", "minor"])])
        lv = card["perspectives"]["low_vision"]
        self.assertLess(lv["score"], 60)
        self.assertGreater(lv["score"], 0)
        self.assertEqual(lv["steps_vs_baseline"], 3.0)
        self.assertEqual(lv["barriers"], 2)

    def test_technical_errors_are_excluded(self):
        card = scorecard([rec("baseline", True), rec("tremor", False, status="harness_error")])
        self.assertIsNone(card["perspectives"]["tremor"]["score"])
        self.assertEqual(card["perspectives"]["tremor"]["errors"], 1)
        self.assertEqual(card["score"], 100)

    def test_blocked_mouse_actions_count_as_friction(self):
        blocked = [{"cmd": "click_at", "blocked": True}] * 3
        card = scorecard([rec("baseline", True), rec("keyboard_only", True, actions=blocked)])
        self.assertEqual(card["perspectives"]["keyboard_only"]["blocked"], 3)
        self.assertLess(card["perspectives"]["keyboard_only"]["score"], 100)

    def test_grades(self):
        self.assertEqual([grade(s) for s in (95, 80, 65, 45, 10)], ["A", "B", "C", "D", "F"])


if __name__ == "__main__":
    unittest.main()


class AnswerTests(unittest.TestCase):
    def test_all_facts_with_alternatives(self):
        from runner import answer_matches
        spec = "98.00; 10 Dec|10 December|December 10"
        self.assertTrue(answer_matches("Total €98.00 for Thursday 10 December 2026", spec))
        self.assertTrue(answer_matches("Booked December 10: total 98.00", spec))
        self.assertFalse(answer_matches("Total €98.00 on Tuesday 15 December", spec))
        self.assertFalse(answer_matches("Thu 10 Dec, total €49.00", spec))
        self.assertTrue(answer_matches("Order total €19.90", "19.90"))
        spec8 = "98.00; 8 Dec|8 December|December 8"
        self.assertTrue(answer_matches("Tuesday 8 December 2026, total €98.00", spec8))
        self.assertTrue(answer_matches("Tue 8 Dec · 18:30 — €98.00", spec8))
        self.assertFalse(answer_matches("Friday 18 December, total €98.00", spec8))
        self.assertFalse(answer_matches("Total €198.00 on 8 Dec", spec8))

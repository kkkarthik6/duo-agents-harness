"""The text every agent turn carries stays small, unique and cache-friendly."""
import unittest

from engine import broker, check_policy, jobs, orchestration, relay, roleflow, task_output
from engine.session import Session


class SharedTextTests(unittest.TestCase):
    def test_the_fixed_text_is_short(self):
        self.assertLess(len(check_policy.RULES) + len(task_output.GUIDANCE), 1400)

    def test_the_fixed_text_does_not_repeat_itself(self):
        text = check_policy.RULES + task_output.GUIDANCE
        sentences = [s.strip() for s in text.replace("\n", " ").split(".") if len(s.strip()) > 25]
        self.assertEqual(len(sentences), len(set(sentences)))

    def test_no_text_talks_about_old_systems_the_agent_never_saw(self):
        text = (check_policy.RULES + task_output.GUIDANCE + relay.REVIEW + orchestration.contract("T", "a", build=True)).lower()
        for stale in ("replace older", "older reply", "old duo", "command-policy", "end marker", "json block"):
            self.assertNotIn(stale, text)

    def test_job_registration_is_one_short_paragraph_with_a_relative_registry(self):
        text = jobs.instructions({"job_registry": "/w/.duo/r/jobs/t.json", "task_id": "T1"}, "/w")
        self.assertLess(len(text), 800)
        self.assertIn(".duo/r/jobs/t.json", text)
        self.assertNotIn("/w/.duo", text)

    def test_the_outcome_folder_note_is_short(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        self.assertLess(len(task_output.instructions(root, root / "output/task-0001/step-0001")), 400)


class PlanByPathTests(unittest.TestCase):
    def test_the_review_prompt_points_to_the_plan_instead_of_pasting_it(self):
        text = roleflow.review_prompt("TASK", ".duo/r/plan.md")
        self.assertIn(".duo/r/plan.md", text)
        self.assertLess(len(text), 1800)


class ReviewerTests(unittest.TestCase):
    def test_reviewers_must_list_every_defect_in_one_pass(self):
        self.assertIn("EVERY required fix", relay.REVIEW)
        self.assertIn("EVERY required correction", roleflow.review_prompt("x", ".duo/r/plan.md"))
        self.assertIn("one pass", roleflow.rubric("critic")["instructions"])
        self.assertIn("one pass", roleflow.rubric("challenger")["instructions"])


class FastCloseTests(unittest.TestCase):
    def state(self, **kw):
        s = {"goal_loop_version": 2, "goal": {"status": "active"}, "last_verdict": "APPROVED", "verify_passed": True,
             "task_delivery": {"task_id": "T"}, "task_type": "programming"}
        s.update(kw)
        return s

    def closes(self, **kw):
        run = Session.__new__(Session)
        run.st = self.state(**kw)
        return run._closes_without_goal_check()

    def test_approved_verified_delivered_work_closes_directly(self):
        self.assertTrue(self.closes())

    def test_anything_short_of_that_still_gets_the_goal_check(self):
        for change in ({"last_verdict": "REVISE"}, {"verify_passed": False}, {"task_delivery": None},
                       {"ready_for_review": False}, {"task_type": "research"}, {"stop": {"rule": "x"}},
                       {"goal": {"status": "assigning"}}):
            self.assertFalse(self.closes(**change), change)

    def test_the_switch_turns_it_off(self):
        from unittest import mock
        from engine import config
        with mock.patch.dict(config.CFG, {"fast_close": False}):
            self.assertFalse(self.closes())


if __name__ == "__main__":
    unittest.main()

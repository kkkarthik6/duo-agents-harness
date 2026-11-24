"""Small tasks are done by one agent, like a CLI alone; anything bigger keeps the whole pipeline."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import agent_failures, config, handoff, triage
from engine.phases import next_turns

ROLES = {"lead": "claude", "challenger": "codex", "builder": "claude", "critic": "codex", "verifier": "codex"}


class TriageTests(unittest.TestCase):
    def test_a_short_single_part_task_is_trivial(self):
        for task in ("Write a script that renames photos by EXIF date", "Fix the typo in README",
                     "Build a small CLI called wordstat that prints lines, words and the 5 most common words, with unit tests."):
            self.assertEqual(triage.classify(task)[0], "trivial", task)

    def test_risky_or_wide_work_keeps_the_full_pipeline(self):
        for task in ("Refactor the auth module to use JWT", "Add an endpoint to the API server", "Migrate the database schema",
                     "Deploy the app with docker", "Research methods for few-shot learning and write a paper"):
            self.assertEqual(triage.classify(task)[0], "standard", task)

    def test_a_long_or_many_part_task_is_standard(self):
        self.assertEqual(triage.classify("word " * 120)[0], "standard")
        self.assertEqual(triage.classify("\n".join("requirement %d" % i for i in range(9)))[0], "standard")

    def test_an_empty_task_is_not_trivial(self):
        self.assertEqual(triage.classify("  ")[0], "standard")


def quick_state(**kw):
    s = {"phase": None, "size": "trivial", "task_type": "general", "roles": dict(ROLES), "agents": ["claude", "codex"],
         "options": [{"id": "O1"}], "chosen": "O1", "round": 0, "max_rounds": 100, "plan_verdict": "APPROVED",
         "stage_requests": {}, "quick_review": False}
    s.update(kw)
    return s


class QuickPhaseTests(unittest.TestCase):
    def test_a_quick_task_goes_straight_to_one_build_turn(self):
        s = quick_state()
        self.assertEqual(next_turns(s), [])
        self.assertEqual(s["phase"], "decide")
        turns = next_turns(s)
        self.assertEqual([(t.kind, t.agent) for t in turns], [("build", "claude")])

    def test_after_the_build_it_is_done_with_no_review(self):
        s = quick_state(phase="deep", deep_step="build", round=1)
        self.assertEqual(next_turns(s), "done")
        self.assertTrue(s["quick"] and s["verify_passed"])

    def test_an_unfinished_build_still_gets_another_round(self):
        s = quick_state(phase="deep", deep_step="build", round=1, ready_for_review=False)
        self.assertEqual(next_turns(s)[0].kind, "revise")

    def test_quick_review_adds_one_independent_review(self):
        s = quick_state(phase="deep", deep_step="build", round=1, quick_review=True)
        self.assertEqual(next_turns(s)[0].kind, "critique")

    def test_a_standard_task_is_still_planned_and_plan_reviewed(self):
        s = quick_state(size="standard", plan_verdict=None, chosen=None)
        self.assertEqual(next_turns(s)[0].kind, "exchange")


class EscalationPhaseTests(unittest.TestCase):
    def test_a_quick_task_still_unfinished_after_two_rounds_becomes_a_full_run(self):
        s = quick_state(phase="deep", deep_step="build", round=2, ready_for_review=False)
        self.assertEqual(next_turns(s), [])
        self.assertEqual((s["size"], s["phase"], s["escalated"], s["plan_verdict"]), ("standard", None, True, None))
        self.assertIsNone(s["accepted_plan_signature"])
        self.assertEqual(next_turns(s)[0].kind, "exchange")            # then it is planned like any standard task
        self.assertEqual(next_turns(s)[0].kind, "plan_review")

    def test_the_first_unfinished_round_is_just_revised(self):
        s = quick_state(phase="deep", deep_step="build", round=1, ready_for_review=False)
        self.assertEqual(next_turns(s)[0].kind, "revise")
        self.assertEqual(s["size"], "trivial")

    def test_a_quick_task_that_stops_changing_files_escalates_instead_of_stalling(self):
        s = quick_state(phase="deep", deep_step="build", round=1, last_changed=0, stall=1, ready_for_review=False)
        self.assertEqual(next_turns(s), [])
        self.assertTrue(s["escalated"])

    def test_a_standard_task_that_stalls_still_stops(self):
        s = quick_state(size="standard", phase="deep", deep_step="build", round=1, last_changed=0, stall=1, ready_for_review=False)
        self.assertEqual(next_turns(s), "stalled")

    def test_an_escalated_task_does_not_escalate_again(self):
        s = quick_state(phase="deep", deep_step="build", round=2, ready_for_review=False)
        next_turns(s)
        s.update(phase="deep", deep_step="build", round=5, ready_for_review=False)
        self.assertEqual(next_turns(s)[0].kind, "revise")

    def test_the_planner_is_told_what_happened_and_not_to_redo_good_work(self):
        s = {"title": "t", "status": "running", "phase": None, "round": 0, "max_rounds": 100, "cycle": 1, "size": "standard",
             "escalated": True, "plan_verdict": None, "stage_requests": {},
             "_role_models": {"lead": {"size": "large", "model": "m"}},
             "entries": [{"n": 1, "author": "human", "kind": "task", "prose": "Do the thing", "cycle": 1},
                         {"n": 3, "author": "codex", "kind": "build", "prose": "Built part of it.", "changed": ["a.py"], "file": "r.md"}]}
        text = handoff.brief(s, "exchange")
        self.assertIn("quick one-agent attempt did not finish", text)
        self.assertIn("Do not redo what already works", text)
        self.assertIn("a.py", text)
        self.assertIn("Built part of it.", text)


class QuickBriefTests(unittest.TestCase):
    def state(self, **kw):
        s = {"title": "t", "status": "running", "phase": "deep", "round": 0, "max_rounds": 100, "cycle": 1,
             "size": "trivial", "quick_review": False, "plan_verdict": "APPROVED", "stage_requests": {},
             "_role_models": {"builder": {"size": "small", "model": "m"}, "critic": {"size": "medium", "model": "n"}},
             "entries": [{"n": 1, "author": "human", "kind": "task", "prose": "Fix the typo", "cycle": 1}]}
        s.update(kw)
        return s

    def test_the_brief_says_there_is_no_plan_and_no_reviewer(self):
        text = handoff.brief(self.state(), "build")
        self.assertIn("Do it in one pass", text)
        self.assertIn("Fix the typo", text)
        self.assertNotIn("Plan:", text)
        self.assertNotIn("read next by", text)
        self.assertNotIn("small model", text)

    def test_the_quick_brief_is_short_and_keeps_every_constraint(self):
        s = self.state(constraints=["keep it under 200 lines"])
        s["entries"].append({"n": 2, "author": "human", "kind": "message", "prose": "also support --shout", "cycle": 1})
        text = handoff.brief(s, "build")
        self.assertLess(len(text), 900)
        self.assertIn("keep it under 200 lines", text)
        self.assertIn("also support --shout", text)
        self.assertIn("do not repeat checks", text.lower())

    def test_with_quick_review_the_reader_is_named_again(self):
        self.assertIn("read next by", handoff.brief(self.state(quick_review=True), "build"))


class RememberedLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patch = mock.patch.object(config, "DUO_HOME", Path(self.tmp))
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_expired_login_is_remembered_for_later_runs(self):
        agent_failures.record({"agents": ["claude", "codex"]}, "claude", "m", {"kind": "auth_expired"}, now=1000)
        state = {"agents": ["claude", "codex"]}
        self.assertEqual(agent_failures.seed(state, now=1100), ["anthropic"])
        self.assertFalse(agent_failures.available(state, "claude", "m", now=1100))
        self.assertTrue(agent_failures.available(state, "codex", "m", now=1100))

    def test_it_is_forgotten_after_fifteen_minutes(self):
        agent_failures.record({}, "claude", "m", {"kind": "auth_expired"}, now=1000)
        self.assertEqual(agent_failures.seed({"agents": ["claude", "codex"]}, now=1000 + agent_failures.AUTH_MEMORY + 1), [])

    def test_a_run_with_only_that_cli_still_tries_it(self):
        agent_failures.record({}, "claude", "m", {"kind": "auth_expired"}, now=1000)
        state = {"agents": ["claude"]}
        self.assertEqual(agent_failures.seed(state, now=1100), [])
        self.assertTrue(agent_failures.available(state, "claude", "m", now=1100))

    def test_other_failures_are_not_remembered(self):
        agent_failures.record({}, "claude", "m", {"kind": "rate_limit", "retry_after": 30}, now=1000)
        self.assertEqual(agent_failures.seed({"agents": ["claude", "codex"]}, now=1010), [])


if __name__ == "__main__":
    unittest.main()

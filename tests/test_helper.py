import unittest
from unittest import mock

from engine import helper


class ContextTests(unittest.TestCase):
    def st(self):
        return {"task": "Build a notes app", "phase": "deep", "status": "running",
                "entries": [{"n": 1, "author": "human", "kind": "task", "phase": "task", "summary": "Build a notes app"},
                            {"n": 2, "author": "claude", "kind": "build", "phase": "deep", "summary": "Claude built it."}]}

    def test_context_has_task_phase_and_recent_events(self):
        text = helper.context(self.st(), None)
        for want in ("Build a notes app", "Phase: deep", "Claude built it."):
            self.assertIn(want, text)

    def test_ask_stores_answer_from_cli(self):
        st = self.st()
        with mock.patch.object(helper, "run_cli", return_value=(True, "It is building.")):
            item = helper.ask(st, ["claude"], "/tmp", "What now?", None)
        self.assertEqual(item["a"], "It is building.")
        self.assertEqual(st["helper"][-1]["q"], "What now?")

    def test_ask_falls_back_to_digest_when_cli_fails(self):
        st = self.st()
        with mock.patch.object(helper, "run_cli", return_value=(False, "no cli")):
            item = helper.ask(st, ["claude"], "/tmp", "What now?", None)
        self.assertIn("Phase deep, status running", item["a"])
        self.assertTrue(item["fallback"])

    def test_prompt_forbids_changes(self):
        with mock.patch.object(helper, "run_cli", return_value=(True, "ok")) as r:
            helper.ask(self.st(), ["claude"], "/tmp", "Why?", None)
        self.assertIn("Do not change any file", r.call_args[0][2])


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace

from engine import session


def reason(st):
    return session.Session.stop_reason(SimpleNamespace(st=st))


class StopReasonTests(unittest.TestCase):
    def test_no_progress_guard_names_the_count(self):
        r = reason({"status": "waiting", "paused": True, "stall": 2, "phase": "deep", "round": 3, "max_rounds": 40, "detail": ""})
        self.assertEqual(r["rule"], "No-progress guard")
        self.assertIn("count: 2 of 2", r["why"])

    def test_round_limit_names_the_round_and_limit(self):
        r = reason({"status": "waiting", "paused": True, "stall": 0, "phase": "deep", "round": 40, "max_rounds": 40, "detail": ""})
        self.assertEqual(r["rule"], "Round limit")
        self.assertIn("Round 40 of 40", r["why"])

    def test_verify_failures(self):
        r = reason({"status": "waiting", "paused": True, "stall": 0, "phase": "verify", "verify_failures": 2, "round": 1, "max_rounds": 40, "detail": ""})
        self.assertEqual(r["rule"], "Verify failed twice")

    def test_running_run_has_no_reason(self):
        self.assertIsNone(reason({"status": "running", "paused": False, "stall": 1, "phase": "deep", "round": 2, "max_rounds": 40, "detail": ""}))


if __name__ == "__main__":
    unittest.main()

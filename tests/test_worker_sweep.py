import unittest
from unittest import mock

from engine import workers

NOW = 10_000


def listing(*rows):
    return "\n".join("\t".join(map(str, r)) for r in rows) + "\n"


class SweepTests(unittest.TestCase):
    def sweep(self, rows, keep=()):
        with mock.patch.object(workers, "tmux") as t:
            t.side_effect = lambda *a, check=True: listing(*rows) if a[0] == "list-sessions" else ""
            closed = workers.sweep(set(keep), now=NOW)
            killed = [c.args[2] for c in t.call_args_list if c.args[0] == "kill-session"]
        self.assertEqual(closed, killed)
        return closed

    def test_unowned_old_sessions_are_closed(self):
        self.assertEqual(self.sweep([("duo-run-a", "old-run", 0, 100)]), ["duo-run-a"])

    def test_a_session_of_an_active_run_stays(self):
        self.assertEqual(self.sweep([("duo-run-a", "live", 0, 100)], keep={"live"}), [])

    def test_an_open_terminal_stays(self):
        self.assertEqual(self.sweep([("duo-run-a", "old-run", 1, 100)]), [])

    def test_a_new_session_stays(self):
        self.assertEqual(self.sweep([("duo-run-a", "", 0, NOW - 60)]), [])

    def test_other_tmux_sessions_are_never_touched(self):
        self.assertEqual(self.sweep([("work", "", 0, 100), ("duo-run-a", "x", 0, 100)]), ["duo-run-a"])


if __name__ == "__main__":
    unittest.main()

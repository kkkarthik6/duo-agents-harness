import unittest
from engine import session, harness, protocol


class Fake:
    """Just enough of Session for the harness methods: state, entries, and no disk."""
    def __init__(self, **st):
        self.st = {"status": "running", "paused": False, "phase": "deep", "entries": [], "stall": 0, "checklist": [],
                   "pending": [], "created": None, **st}
        self.saved = 0
    def add_entry(self, author, phase, kind, prose, summary="", **extra):
        e = {"n": len(self.st["entries"]) + 1, "author": author, "phase": phase, "kind": kind, "prose": prose, "summary": summary, **extra}
        self.st["entries"].append(e)
        return e
    def save(self):
        self.saved += 1
    def label(self, agent):
        return agent.title()
    def _diagnostic(self, message, p=None, operation=None):
        # Diagnostics need a real run folder; these tests only check which rule pauses the run.
        return "error-test"
    def __getattr__(self, name):
        if name in ("_pause", "_pause_for_blocker", "_loop_checks", "_guard_value", "_ledger", "stop_reason",
                    "checklist_edit", "_preflight_code"):
            return getattr(session.Session, name).__get__(self)
        raise AttributeError(name)


def reply(prose):
    r = protocol.Reply(prose=prose)
    return r


class GuardValueTests(unittest.TestCase):
    def test_progress_resets_and_no_outcome_counts(self):
        f = Fake()
        self.assertEqual(f._guard_value("progress", None, 3, False), 3)
        self.assertEqual(f._guard_value("progress", None, 0, True), 0)
        self.assertEqual(f._guard_value("blocked", "no_outcome", 0, False), 0)

    def test_answered_and_other_blocks_neither_count_nor_reset(self):
        f = Fake()
        self.assertIsNone(f._guard_value("answered", None, 0, False))
        self.assertIsNone(f._guard_value("blocked", "docker_unreachable", 0, False))


class PauseTests(unittest.TestCase):
    def test_docker_blocker_pauses_with_named_rule(self):
        f = Fake()
        f._pause_for_blocker("docker_unreachable")
        self.assertTrue(f.st["paused"])
        self.assertEqual(f.st["status"], "waiting")
        self.assertEqual(f.st["blocker"], "docker_unreachable")
        self.assertEqual(f.st["stop"]["rule"], "Docker unreachable")
        self.assertIn("Start Docker", f.st["stop"]["next"])
        self.assertEqual(f.stop_reason()["rule"], "Docker unreachable")

    def test_stop_reason_is_none_while_running(self):
        f = Fake()
        self.assertIsNone(f.stop_reason())


class LoopTests(unittest.TestCase):
    def test_two_identical_replies_do_not_pause(self):
        f = Fake()
        f._loop_checks("claude", reply("Nothing run."), "progress")
        f._loop_checks("claude", reply("nothing  run."), "progress")
        self.assertFalse(f.st["paused"])

    def test_three_identical_replies_in_a_row_pause(self):
        f = Fake()
        for _ in range(harness.LOOP_REPEAT_LIMIT):
            f._loop_checks("claude", reply("Nothing run."), "progress")
        self.assertEqual(f.st["stop"]["rule"], "Repeated reply")

    def test_answer_streak_pauses(self):
        f = Fake()
        for i in range(harness.LOOP_ANSWER_STREAK):
            f._loop_checks("codex", reply("answer %d" % i), "answered")
        self.assertEqual(f.st["stop"]["rule"], "Questions instead of work")

    def test_an_answer_then_work_resets_the_streak(self):
        f = Fake()
        f._loop_checks("codex", reply("a"), "answered")
        f._loop_checks("codex", reply("b"), "progress")
        self.assertEqual(f.st["answer_streak"], 0)


class LedgerTests(unittest.TestCase):
    def test_ledger_row_is_kept_and_capped(self):
        f = Fake()
        f._ledger({"n": 1, "author": "claude", "phase": "deep", "kind": "build", "duration": 4}, "progress", None, 2, False, {"checkpoint": 1})
        row = f.st["ledger"][0]
        self.assertEqual((row["outcome"], row["changed"], row["checkpoint"]), ("progress", 2, 1))
        for i in range(305):
            f._ledger({"n": i, "author": "a", "phase": "deep", "kind": "build"}, "progress", None, 1, False, {})
        self.assertEqual(len(f.st["ledger"]), 300)


class ChecklistTests(unittest.TestCase):
    def test_add_toggle_remove(self):
        f = Fake()
        f.checklist_edit("add", "Run experiments")
        f.checklist_edit("add", "Write paper")
        self.assertEqual([i["id"] for i in f.st["checklist"]], [1, 2])
        f.checklist_edit("toggle", item_id=1)
        self.assertTrue(f.st["checklist"][0]["done"])
        f.checklist_edit("remove", item_id=2)
        self.assertEqual(len(f.st["checklist"]), 1)

    def test_empty_text_refused(self):
        with self.assertRaises(ValueError):
            Fake().checklist_edit("add", "   ")


if __name__ == "__main__":
    unittest.main()

import unittest

from engine.bounds import check, is_high_impact
from engine.protocol import Reply

TASK = "Build a CLI that renames photos by EXIF date and keeps a log"


def ctx(**kw):
    base = {"task": TASK, "expected_phase": "frame", "changed_files": [], "handoff_log": [], "author": "local-agent"}
    base.update(kw)
    return base


class BoundsTests(unittest.TestCase):
    def test_clean_reply_passes(self):
        r = Reply(phase="frame", handoffs=[{"to": "codex", "what": "probe O2", "ask_ref": "renames photos by EXIF date"}])
        self.assertEqual(check(r, ctx()), [])

    def test_wrong_phase(self):
        self.assertEqual(check(Reply(phase="deep"), ctx()), ["wrong phase: expected frame"])

    def test_files_changed_before_decide(self):
        r = Reply(phase="frame")
        out = check(r, ctx(expected_phase="frame", changed_files=["main.py", ".duo/1113/replies/0002-claude.md"]))
        self.assertEqual(out, ["files changed before decide: main.py"])

    def test_scout_may_write_scout_dir_only(self):
        r = Reply(phase="scout")
        self.assertEqual(check(r, ctx(expected_phase="scout", changed_files=[".duo/1113/scout/spike.py"])), [])
        self.assertEqual(check(r, ctx(expected_phase="scout", changed_files=["spike.py"])),
                         ["files changed before decide: spike.py"])

    def test_deep_may_change_files(self):
        self.assertEqual(check(Reply(phase="deep"), ctx(expected_phase="deep", changed_files=["main.py"])), [])

    def test_handoff_outside_ask(self):
        r = Reply(phase="frame", handoffs=[{"to": "codex", "what": "add a GUI", "ask_ref": "add a GUI too"}])
        self.assertEqual(check(r, ctx()), ["handoff outside the ask"])

    def test_handoff_ref_too_short(self):
        r = Reply(phase="frame", handoffs=[{"to": "codex", "what": "x", "ask_ref": "photos"}])
        self.assertEqual(check(r, ctx()), ["handoff outside the ask"])

    def test_handoff_ref_normalized(self):
        r = Reply(phase="frame", handoffs=[{"to": "codex", "what": "x", "ask_ref": "RENAMES  photos\nby exif"}])
        self.assertEqual(check(r, ctx()), [])

    def test_handoff_to_human(self):
        r = Reply(phase="frame", handoffs=[{"to": "human", "what": "x", "ask_ref": "renames photos by EXIF date"}])
        self.assertEqual(check(r, ctx()), ["handoff to human not allowed"])

    def test_ping_pong(self):
        h = {"to": "codex", "what": "Fix the parser", "ask_ref": "renames photos by EXIF date"}
        log = [("local-agent", "codex", "fix the parser"), ("codex", "local-agent", "Fix the  parser")]
        self.assertEqual(check(Reply(phase="frame", handoffs=[h]), ctx(handoff_log=log)), ["ping-pong limit reached"])
        self.assertEqual(check(Reply(phase="frame", handoffs=[h]), ctx(handoff_log=log[:1])), [])

    def test_manifest_is_high_impact(self):
        self.assertTrue(is_high_impact(["src/package.json"], []))
        self.assertTrue(is_high_impact(["requirements-dev.txt"], []))
        self.assertTrue(is_high_impact([], ["old.py"]))
        self.assertFalse(is_high_impact(["main.py"], []))


if __name__ == "__main__":
    unittest.main()

    def test_native_workers_are_exempt_from_scope_and_handoff_rules(self):
        """Claude and Codex enforce their own permissions; only phase order is checked for them."""
        r = Reply(phase="score", handoffs=[{"to": "human", "what": "x", "ask_ref": "unrelated"}])
        out = check(r, ctx(expected_phase="score", author="claude", changed_files=["main.py"]))
        self.assertEqual(out, [])
        self.assertEqual(check(Reply(phase="deep"), ctx(author="codex")), ["wrong phase: expected frame"])

import tempfile
import unittest
from pathlib import Path

from engine import feedback, userspace


class FeedbackKindTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(self.base)

    def test_feedback_records_its_update_type_and_number(self):
        rec = feedback.submit("make it faster", self.base, spawn=False, kind="major")
        self.assertEqual((rec["kind"], rec["number"]), ("major", "1.1.0"))
        self.assertEqual(feedback.read(self.base)["number"], "1.1.0")
        self.assertTrue(rec["path"].startswith(str(self.base / "versions")))

    def test_default_update_type_is_minor(self):
        self.assertEqual(feedback.submit("tidy the header", self.base, spawn=False)["number"], "1.0.1")

    def test_unknown_update_type_is_refused_before_a_copy_is_made(self):
        with self.assertRaises(ValueError):
            feedback.submit("tidy", self.base, spawn=False, kind="patch")
        self.assertEqual(userspace.load(self.base)["versions"], [])

    def test_notice_uses_the_version_number_and_asks_for_roll_up(self):
        rec = feedback.submit("tidy", self.base, spawn=False)
        userspace.update(self.base, rec["version"], status="ready")
        text = feedback.notice(self.base)["text"]
        self.assertIn("1.0.1", text)
        self.assertIn("Roll up", text)

    def test_notice_for_a_core_wait(self):
        rec = feedback.submit("tidy", self.base, spawn=False)
        userspace.update(self.base, rec["version"], status="waiting_for_core")
        self.assertEqual(feedback.notice(self.base)["kind"], "waiting_for_core")

    def test_an_auto_fix_does_not_replace_the_users_own_reference(self):
        mine = feedback.submit("my request", self.base, spawn=False)
        feedback.submit("fix export for core 1.1.0", self.base, spawn=False, auto_fix=True, feature="export")
        self.assertEqual(feedback.read(self.base)["version"], mine["version"])

    def test_a_legacy_install_starts_feedback_at_its_own_number(self):
        base = Path(tempfile.mkdtemp()) / "Duo"
        base.mkdir()
        (base / "versions.json").write_text('{"current": "source", "versions": [{"id": "source", "number": "3.0.0", "path": "x"}]}')
        self.assertEqual(feedback.submit("x", base, spawn=False, kind="major")["number"], "3.1.0")


if __name__ == "__main__":
    unittest.main()

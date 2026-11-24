import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, personal, selfdev


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True).stdout.strip()


class SelfDevTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.app = self.tmp / "_duo"
        (self.app / "engine").mkdir(parents=True)
        (self.app / "tests").mkdir()
        (self.app / "rubrics").mkdir()
        (self.app / "engine" / "a.py").write_text("X = 1\n")
        (self.app / "engine" / "session.py").write_text("security.check_command(x)\nY = 2\n")
        (self.app / "tests" / "test_a.py").write_text("pass\n")
        (self.app / "rubrics" / "security.json").write_text("{}\n")
        self.cfg = mock.patch.dict(config.CFG, {"root": str(self.tmp / "DW")})
        self.cfg.start()
        self.patch = mock.patch.object(selfdev, "APP", self.app)
        self.patch.start()
        personal._write(personal._proposals_path(), json.dumps([{"id": "p1", "title": "Tweak X", "request": "Set X to 2",
                                                                  "feedback_ids": [], "status": "queued"}]))
        self.p = personal.proposals()[0]

    def tearDown(self):
        self.patch.stop()
        self.cfg.stop()

    def prepared(self, edit):
        selfdev.prepare(self.p)
        p = personal.proposals()[0]
        wt = Path(p["worktree"])
        edit(wt)
        return p, wt

    def finish(self, p, ok=True):
        selfdev.finalize(p, verify_passed=True, runner=lambda cwd: (ok, "5 tests OK" if ok else "1 failed"))
        return personal.proposals()[0]

    def test_ensure_repo_makes_baseline_and_tag(self):
        selfdev.ensure_repo()
        self.assertEqual(git(self.app, "tag"), "v3.0")
        self.assertIn(".duo/", (self.app / ".gitignore").read_text())

    def good_edit(self, wt):
        (wt / "engine" / "a.py").write_text("X = 2\n")
        (wt / "tests" / "test_b.py").write_text("pass\n")

    def test_ready_then_approve_tags_and_applies(self):
        p, wt = self.prepared(self.good_edit)
        self.assertEqual(p["status"], "prepared")
        p = self.finish(p)
        self.assertEqual(p["status"], "ready")
        self.assertEqual(sorted(p["files"]), ["engine/a.py", "tests/test_b.py"])
        selfdev.approve(p["id"])
        p = personal.proposals()[0]
        self.assertEqual((p["status"], p["tag"]), ("approved", "v3.1"))
        self.assertEqual((self.app / "engine" / "a.py").read_text(), "X = 2\n")
        self.assertFalse(wt.exists())

    def test_rollback_reverts_and_tags(self):
        p, wt = self.prepared(self.good_edit)
        selfdev.approve(self.finish(p)["id"])
        selfdev.rollback("p1")
        self.assertEqual((self.app / "engine" / "a.py").read_text(), "X = 1\n")
        self.assertFalse((self.app / "tests" / "test_b.py").exists())
        self.assertEqual((personal.proposals()[0]["status"], git(self.app, "tag").split()[-1]), ("rolled_back", "v3.2"))

    def test_failed_tests_cannot_be_approved(self):
        p, wt = self.prepared(self.good_edit)
        p = self.finish(p, ok=False)
        self.assertEqual(p["status"], "failed")
        with self.assertRaises(ValueError):
            selfdev.approve("p1")

    def blocked(self, edit, rule):
        p, wt = self.prepared(edit)
        p = self.finish(p)
        self.assertEqual(p["status"], "blocked")
        self.assertIn(rule, [f["rule"] for f in p["findings"]])
        with self.assertRaises(ValueError):
            selfdev.approve("p1")

    def test_security_files_are_protected(self):
        def edit(wt):
            (wt / "rubrics" / "security.json").write_text('{"commands": []}\n')
            (wt / "tests" / "test_b.py").write_text("pass\n")
        self.blocked(edit, "protected_file")

    def test_outside_allowed_folders_blocked(self):
        def edit(wt):
            (wt / "bin").mkdir()
            (wt / "bin" / "duo").write_text("x\n")
            (wt / "tests" / "test_b.py").write_text("pass\n")
        self.blocked(edit, "outside_scope")

    def test_removing_a_security_check_is_blocked(self):
        def edit(wt):
            (wt / "engine" / "session.py").write_text("Y = 2\n")
            (wt / "tests" / "test_b.py").write_text("pass\n")
        self.blocked(edit, "removes_security_call")

    def test_a_change_without_a_test_is_blocked(self):
        self.blocked(lambda wt: (wt / "engine" / "a.py").write_text("X = 3\n"), "no_test")

    def test_leaked_secret_in_proposal_is_blocked(self):
        def edit(wt):
            (wt / "engine" / "a.py").write_text("password = 'hunter2hunter2'\n")
            (wt / "tests" / "test_b.py").write_text("pass\n")
        self.blocked(edit, "hardcoded_secret")

    def test_verify_not_passed_fails_without_running_tests(self):
        p, wt = self.prepared(self.good_edit)
        selfdev.finalize(p, verify_passed=False, runner=lambda cwd: self.fail("tests must not run"))
        self.assertEqual(personal.proposals()[0]["status"], "failed")

    def test_decline_removes_worktree_and_branch(self):
        p, wt = self.prepared(self.good_edit)
        selfdev.decline("p1")
        self.assertEqual(personal.proposals()[0]["status"], "declined")
        self.assertFalse(wt.exists())
        self.assertNotIn("self/", git(self.app, "branch"))

    def test_approve_refuses_dirty_app_folder(self):
        p, wt = self.prepared(self.good_edit)
        self.finish(p)
        (self.app / "engine" / "a.py").write_text("X = 99\n")
        with self.assertRaises(ValueError):
            selfdev.approve("p1")

    def test_task_text_names_the_limits(self):
        t = selfdev.task_text(self.p)
        for needle in ("engine/", "tests/", "security.py", "Tweak X", "Set X to 2"):
            self.assertIn(needle, t)


if __name__ == "__main__":
    unittest.main()

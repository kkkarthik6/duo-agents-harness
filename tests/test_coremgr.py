import json
import tempfile
import unittest
from pathlib import Path

from engine import coremgr, userspace


def core_src(root, version="1.0.0", marker="a", contract="1.0.0"):
    src = Path(root) / ("src-" + version + marker)
    (src / "engine" / "migrations").mkdir(parents=True, exist_ok=True)
    (src / "bin").mkdir(exist_ok=True)
    (src / "CORE_VERSION").write_text(version + "\n")
    (src / "engine" / "coreapi.py").write_text('CONTRACT = "%s"\n' % contract)
    (src / "engine" / "x.py").write_text(marker)
    (src / "bin" / "duo").write_text("#!/bin/sh\n")
    (src / "config.json").write_text('{"secret": 1}')
    (src / "engine" / "__pycache__").mkdir(exist_ok=True)
    (src / "engine" / "__pycache__" / "x.pyc").write_text("junk")
    return src


def ok_tests(core_dir):
    return True, "OK"


def no_features(user_dir, core_dir, names=None):
    return {}


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"

    def test_install_copies_core_without_local_files_and_sets_current(self):
        d = coremgr.install(core_src(self.tmp), self.base)
        self.assertEqual(d, self.base / "core" / "1.0.0")
        self.assertEqual(coremgr.current_dir(self.base), d)
        self.assertFalse((d / "config.json").exists())
        self.assertFalse((d / "engine" / "__pycache__").exists())
        self.assertEqual(coremgr.info(self.base)["version"], "1.0.0")
        self.assertEqual(coremgr.info(self.base)["contract"], "1.0.0")

    def test_verify_names_a_core_file_that_was_changed(self):
        d = coremgr.install(core_src(self.tmp), self.base)
        self.assertEqual(coremgr.verify(self.base), [])
        (d / "engine" / "x.py").write_text("tampered")
        (d / "engine" / "new.py").write_text("added")
        self.assertEqual(sorted(coremgr.verify(self.base)), ["engine/new.py", "engine/x.py"])

    def test_install_twice_with_the_same_code_changes_nothing(self):
        src = core_src(self.tmp)
        coremgr.install(src, self.base)
        coremgr.install(src, self.base)
        self.assertEqual(sorted(p.name for p in (self.base / "core").iterdir() if p.is_dir()), ["1.0.0"])


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"
        coremgr.install(core_src(self.tmp, "1.0.0"), self.base)
        userspace.ensure(self.base)

    def feature(self, name):
        d = userspace.user_dir(self.base) / "features" / name
        d.mkdir(parents=True)
        (d / "feature.json").write_text(json.dumps({"name": name, "contract": ">=1.0"}))
        (d / "feature.py").write_text("def register(api): pass\n")

    def test_an_update_that_passes_switches_core_and_keeps_the_old_one(self):
        r = coremgr.update(core_src(self.tmp, "1.1.0"), self.base, core_tests=ok_tests, feature_tests=no_features, start_fix=None)
        self.assertEqual(r["status"], "updated")
        self.assertEqual(coremgr.info(self.base)["version"], "1.1.0")
        self.assertEqual(coremgr.info(self.base)["previous"], "1.0.0")
        self.assertTrue((self.base / "core" / "1.0.0").exists())

    def test_failing_core_tests_leave_the_running_core_untouched(self):
        r = coremgr.update(core_src(self.tmp, "1.1.0"), self.base, core_tests=lambda d: (False, "FAILED (errors=2)"),
                           feature_tests=no_features, start_fix=None)
        self.assertEqual(r["status"], "core_failed")
        self.assertIn("FAILED", r["detail"])
        self.assertEqual(coremgr.info(self.base)["version"], "1.0.0")
        self.assertFalse((self.base / "core" / "1.1.0").exists())

    def test_the_same_code_again_is_up_to_date(self):
        r = coremgr.update(core_src(self.tmp, "1.0.0"), self.base, core_tests=ok_tests, feature_tests=no_features, start_fix=None)
        self.assertEqual(r["status"], "same")

    def test_new_code_with_the_same_version_number_replaces_core_and_keeps_the_old_copy(self):
        r = coremgr.update(core_src(self.tmp, "1.0.0", marker="b"), self.base, core_tests=ok_tests,
                           feature_tests=no_features, start_fix=None)
        self.assertEqual(r["status"], "updated")
        self.assertEqual((coremgr.current_dir(self.base) / "engine" / "x.py").read_text(), "b")
        prev = self.base / "core" / coremgr.info(self.base)["previous"]
        self.assertEqual((prev / "engine" / "x.py").read_text(), "a")

    def test_a_failing_feature_is_turned_off_and_a_fix_starts_while_core_updates(self):
        self.feature("export")
        self.feature("notes")
        started = []
        r = coremgr.update(core_src(self.tmp, "1.1.0"), self.base, core_tests=ok_tests,
                           feature_tests=lambda u, c, names=None: {"export": (False, "FAIL: x\nFAILED (failures=1)"),
                                                                   "notes": (True, "OK")},
                           start_fix=lambda name, tail, core_dir: started.append((name, core_dir)) or "c9")
        self.assertEqual(r["status"], "updated")
        self.assertEqual(r["disabled"], ["export"])
        self.assertEqual(coremgr.disabled(self.base), {"export"})
        self.assertEqual(started, [("export", self.base / "core" / "1.1.0")])
        self.assertEqual(r["fixes"], ["c9"])

    def test_a_feature_that_needs_a_newer_contract_than_the_new_core_is_turned_off(self):
        self.feature("future")
        d = userspace.user_dir(self.base) / "features" / "future"
        (d / "feature.json").write_text(json.dumps({"name": "future", "contract": ">=2.0"}))
        r = coremgr.update(core_src(self.tmp, "1.1.0"), self.base, core_tests=ok_tests,
                           feature_tests=lambda u, c, names=None: {}, start_fix=None)
        self.assertEqual(r["disabled"], ["future"])

    def test_go_back_returns_to_the_previous_core(self):
        coremgr.update(core_src(self.tmp, "1.1.0"), self.base, core_tests=ok_tests, feature_tests=no_features, start_fix=None)
        self.assertEqual(coremgr.go_back(self.base), "1.0.0")
        self.assertEqual(coremgr.info(self.base)["version"], "1.0.0")
        self.assertEqual(coremgr.info(self.base)["previous"], "1.1.0")

    def test_go_back_without_a_previous_core_is_refused(self):
        with self.assertRaises(ValueError):
            coremgr.go_back(self.base)


class AutoFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"
        coremgr.install(core_src(self.tmp), self.base)
        userspace.ensure(self.base)
        coremgr._disable(self.base, "export", "Tests failed on core 1.1.0.")

    def fix_clone(self):
        v = userspace.clone(self.base, "fix export", "minor", auto_fix=True, feature="export")
        Path(v["path"], "features", "export").mkdir(parents=True)
        Path(v["path"], "features", "export", "feature.py").write_text("fixed")
        return v

    def test_a_fix_that_passes_is_rolled_up_and_the_feature_is_turned_on(self):
        v = self.fix_clone()
        userspace.update(self.base, v["id"], status="ready")
        coremgr.finish_fix(v["id"], "ready", self.base)
        self.assertEqual(coremgr.disabled(self.base), set())
        self.assertEqual(userspace.summary(self.base)["number"], "2.0.0")
        self.assertTrue(userspace.load(self.base)["rolled"][-1]["auto_fix"])
        self.assertTrue((userspace.user_dir(self.base) / "features" / "export" / "feature.py").exists())

    def test_a_fix_that_fails_leaves_the_feature_off_and_the_user_layer_unchanged(self):
        v = self.fix_clone()
        userspace.update(self.base, v["id"], status="stuck")
        coremgr.finish_fix(v["id"], "stuck", self.base)
        self.assertEqual(coremgr.disabled(self.base), {"export"})
        self.assertIn("Repair", coremgr.disabled_info(self.base)["export"]["reason"])
        self.assertEqual(userspace.summary(self.base)["number"], "1.0.0")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"
        userspace.ensure(self.base)

    def test_new_migrations_run_once_with_a_backup(self):
        src = core_src(self.tmp, "1.1.0")
        (src / "engine" / "migrations" / "m0001_mark.py").write_text(
            "from pathlib import Path\ndef migrate(home):\n    p = Path(home) / 'marks.txt'\n    p.write_text(p.read_text() + 'x' if p.exists() else 'x')\n")
        self.assertEqual(coremgr.run_migrations(src, self.base), ["m0001_mark"])
        self.assertEqual(coremgr.run_migrations(src, self.base), [])
        self.assertEqual((self.base / "marks.txt").read_text(), "x")
        self.assertTrue(any((self.base / "backups").iterdir()))

    def test_a_failing_migration_restores_the_backup_and_raises(self):
        (userspace.user_dir(self.base) / "instructions.md").write_text("before")
        src = core_src(self.tmp, "1.1.0")
        (src / "engine" / "migrations" / "m0001_bad.py").write_text(
            "from pathlib import Path\ndef migrate(home):\n    (Path(home) / 'user' / 'instructions.md').write_text('broken')\n    raise RuntimeError('nope')\n")
        with self.assertRaises(RuntimeError):
            coremgr.run_migrations(src, self.base)
        self.assertEqual((userspace.user_dir(self.base) / "instructions.md").read_text(), "before")


class WaitingRetryTests(unittest.TestCase):
    def test_versions_waiting_for_core_are_sent_again_after_an_update(self):
        base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(base)
        v = userspace.clone(base, "needs a parked hook", "major")
        userspace.update(base, v["id"], status="waiting_for_core")
        sent = []
        out = coremgr.retry_waiting(base, submit=lambda text, kind: sent.append((text, kind)) or "c2")
        self.assertEqual(sent, [("needs a parked hook", "major")])
        self.assertEqual(out, ["c2"])
        self.assertEqual(userspace.entry(base, v["id"])["status"], "retried")


if __name__ == "__main__":
    unittest.main()


class DisableKeepsCoreTests(unittest.TestCase):
    def test_a_second_message_keeps_the_core_and_start_time(self):
        base = Path(tempfile.mkdtemp()) / "Duo"
        coremgr._disable(base, "x", "first", core="1.1.0", fix="c1")
        coremgr._disable(base, "x", "second")
        d = coremgr.disabled_info(base)["x"]
        self.assertEqual((d["reason"], d["core"], d["fix"]), ("second", "1.1.0", "c1"))

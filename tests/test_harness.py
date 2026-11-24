import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from engine import harness, config


class OutcomeTests(unittest.TestCase):
    def test_progress_needs_a_change_or_log(self):
        self.assertEqual(harness.classify("progress", None, [], False), ("blocked", "no_outcome"))
        self.assertEqual(harness.classify("progress", None, ["a.py"], False), ("progress", None))
        self.assertEqual(harness.classify("progress", None, [], True), ("progress", None))

    def test_blocked_keeps_its_known_code(self):
        self.assertEqual(harness.classify("blocked", "docker_unreachable", [], False), ("blocked", "docker_unreachable"))

    def test_blocked_with_unknown_code_becomes_no_outcome(self):
        self.assertEqual(harness.classify("blocked", "bogus", [], False), ("blocked", "no_outcome"))

    def test_answered_and_needs_human_pass_through(self):
        self.assertEqual(harness.classify("answered", None, [], False), ("answered", None))
        self.assertEqual(harness.classify("needs_human", None, [], False), ("needs_human", None))

    def test_missing_outcome_keeps_old_rule(self):
        self.assertEqual(harness.classify(None, None, ["a.py"], False), ("progress", None))
        self.assertEqual(harness.classify(None, None, [], False), ("blocked", "no_outcome"))


class StallTests(unittest.TestCase):
    def test_answered_does_not_count_or_reset(self):
        self.assertEqual(harness.next_stall(1, "answered", False), 1)

    def test_no_progress_counts_any_role(self):
        self.assertEqual(harness.next_stall(1, "progress", False), 2)

    def test_progress_resets(self):
        self.assertEqual(harness.next_stall(2, "progress", True), 0)


class PreflightTests(unittest.TestCase):
    def test_sandbox_off_never_blocks(self):
        self.assertIsNone(harness.preflight(False, False, False))

    def test_docker_down_is_its_own_code(self):
        self.assertEqual(harness.preflight(True, False, False), "docker_unreachable")

    def test_container_missing(self):
        self.assertEqual(harness.preflight(True, True, False), "container_missing")

    def test_all_good(self):
        self.assertIsNone(harness.preflight(True, True, True))


class IntentTests(unittest.TestCase):
    def test_questions(self):
        self.assertEqual(harness.intent("Why did it stop"), "question")
        self.assertEqual(harness.intent("Can you tell me why"), "question")
        self.assertEqual(harness.intent("Is the paper done?"), "question")

    def test_instructions(self):
        self.assertEqual(harness.intent("Run the experiments on real data."), "instruction")
        self.assertEqual(harness.intent("Can you check logs and fix it"), "instruction")


class BudgetTests(unittest.TestCase):
    def test_active_time_bands_when_enabled(self):
        with mock.patch.dict(config.CFG, active_time_limit_enabled=True, active_time_limit_hours=6):
            self.assertEqual(harness.budget({"active_runtime_seconds":3600})[0], "ok")
            self.assertEqual(harness.budget({"active_runtime_seconds":5*3600+60})[0], "warn")
            self.assertEqual(harness.budget({"active_runtime_seconds":6*3600})[0], "over")

    def test_old_creation_date_does_not_consume_time(self):
        self.assertEqual(harness.budget({"created":"1970-01-01T00:00:00+00:00"}), ("ok", 0.0))


class LoopFingerprintTests(unittest.TestCase):
    def test_same_text_different_spacing_is_same(self):
        self.assertEqual(harness.fingerprint("Nothing  run\n"), harness.fingerprint("nothing run"))

    def test_approval_key_uses_category_and_last_line(self):
        self.assertEqual(harness.approval_key("dataset", "x\nDownload data?"), "dataset|Download data?")


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ws = self.tmp / "ws"
        (self.ws / "src").mkdir(parents=True)
        (self.ws / "src" / "a.py").write_text("one")
        (self.ws / "data").mkdir()
        (self.ws / "data" / "big.csv").write_text("skip me")
        (self.ws / ".duo").mkdir()
        (self.ws / ".duo" / "state.json").write_text("{}")

    def test_snapshot_skips_data_and_duo(self):
        info = harness.snapshot(self.ws, self.tmp / "cp")
        manifest = json.loads((self.tmp / "cp" / "manifest.json").read_text())
        self.assertEqual(sorted(manifest["files"]), ["src/a.py"])
        self.assertEqual(info["files"], 1)

    def test_restore_puts_back_a_changed_file_and_keeps_new_ones(self):
        harness.snapshot(self.ws, self.tmp / "cp")
        (self.ws / "src" / "a.py").write_text("two")
        (self.ws / "src" / "new.py").write_text("created later")
        res = harness.restore(self.ws, self.tmp / "cp")
        self.assertEqual((self.ws / "src" / "a.py").read_text(), "one")
        self.assertEqual(res["restored"], ["src/a.py"])
        self.assertIn("src/new.py", res["kept_new"])
        self.assertTrue((self.ws / "src" / "new.py").exists())

    def test_over_cap_files_are_listed_not_copied(self):
        harness.snapshot(self.ws, self.tmp / "cp", cap=1)
        manifest = json.loads((self.tmp / "cp" / "manifest.json").read_text())
        self.assertEqual(manifest["skipped"], ["src/a.py"])

    def test_prune_keeps_the_newest(self):
        root = self.tmp / "cps"
        for n in (1, 2, 3):
            (root / str(n)).mkdir(parents=True)
        harness.prune(root, keep=2)
        self.assertEqual(sorted(d.name for d in root.iterdir()), ["2", "3"])


if __name__ == "__main__":
    unittest.main()

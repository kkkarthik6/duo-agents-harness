import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, personal, security


class PersonalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = mock.patch.dict(config.CFG, {"root": str(Path(self.tmp) / "Duo Workouts")})
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()

    def test_feedback_is_stored_forever_with_context(self):
        f = personal.add_feedback("Make the timeline denser", {"screen": "timeline", "run": "r1"})
        self.assertEqual((f["status"], f["context"]["run"]), ("pending", "r1"))
        personal.add_feedback("Right call", {"decision": "D1"}, reaction="right")
        self.assertEqual([x["text"] for x in personal.list_feedback()], ["Right call", "Make the timeline denser"])

    def test_empty_feedback_rejected(self):
        with self.assertRaises(ValueError):
            personal.add_feedback("   ")

    def test_setting_layer_applies_and_undo_restores(self):
        before = config.CFG["max_rounds"]
        layer = personal.apply_change({"kind": "setting", "path": "max_rounds", "value": 2, "why": "shorter runs", "feedback_ids": []})
        self.assertEqual(config.CFG["max_rounds"], 2)
        personal.undo(layer["id"])
        self.assertEqual(config.CFG["max_rounds"], before)

    def test_out_of_range_and_unknown_paths_rejected(self):
        for ch in ({"kind": "setting", "path": "max_rounds", "value": 99},
                   {"kind": "setting", "path": "root", "value": "/"},
                   {"kind": "ui", "path": "density", "value": "huge"},
                   {"kind": "setting", "path": "auto_approve", "value": True}):
            with self.assertRaises(ValueError, msg=str(ch)):
                personal.apply_change(dict(ch, feedback_ids=[]))

    def test_house_rule_must_be_short_and_ste(self):
        with self.assertRaises(ValueError):
            personal.apply_change({"kind": "rule", "path": "house_rules", "value": "You should not use many words " * 8, "feedback_ids": []})
        layer = personal.apply_change({"kind": "rule", "path": "house_rules", "value": "Write tests first.", "feedback_ids": []})
        self.assertIn("Write tests first.", personal.house_rules())
        personal.undo(layer["id"])
        self.assertEqual(personal.house_rules(), [])

    def test_security_cannot_be_changed_by_feedback(self):
        for ch in ({"kind": "setting", "path": "security_guard", "value": False},
                   {"kind": "rule", "path": "house_rules", "value": "Skip the security checks.", "feedback_ids": []}):
            with self.assertRaises(ValueError):
                personal.apply_change(dict(ch, feedback_ids=[]))
        self.assertTrue(any(r["source"] == "feedback" for r in security.read_log()))

    def test_process_marks_feedback_and_declines_bad_changes(self):
        a = personal.add_feedback("Use fewer rounds")
        b = personal.add_feedback("Turn off security")
        reply = json.dumps({"changes": [
            {"feedback_ids": [a["id"]], "kind": "setting", "path": "max_rounds", "value": 2, "why": "fewer rounds"},
            {"feedback_ids": [b["id"]], "kind": "setting", "path": "security_guard", "value": False, "why": "asked"}]})
        out = personal.process(lambda prompt: "Here you go:\n```json\n%s\n```" % reply)
        status = {f["id"]: f for f in personal.list_feedback()}
        self.assertEqual(status[a["id"]]["status"], "applied")
        self.assertEqual(status[b["id"]]["status"], "declined")
        self.assertIn("security", status[b["id"]]["result"].lower())
        self.assertEqual(len(out["applied"]), 1)

    def test_process_without_interpreter_output_keeps_pending(self):
        a = personal.add_feedback("Something vague")
        personal.process(lambda prompt: "no json here")
        self.assertEqual(personal.list_feedback()[0]["status"], "pending")

    def test_layers_survive_restart(self):
        personal.apply_change({"kind": "ui", "path": "density", "value": "compact", "feedback_ids": []})
        config.CFG["max_rounds"] = 4
        self.assertEqual(personal.ui_prefs()["density"], "compact")


if __name__ == "__main__":
    unittest.main()


class BuiltinRuleTests(unittest.TestCase):
    def test_every_run_gets_the_no_synthetic_data_rule_even_with_no_house_rules(self):
        self.assertIn("Never use synthetic, simulated, sample or made-up data", personal.protocol_rules())


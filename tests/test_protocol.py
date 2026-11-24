import json
import unittest

from engine.protocol import parse


def reply(block, prose="The options follow."):
    return "%s\n\n```duo\n%s\n```\n<!-- END -->\n" % (prose, json.dumps(block) if not isinstance(block, str) else block)


GOOD = {"summary": "Two options for the export format.", "phase": "frame", "task_type": "programming",
        "options": [{"id": "O1", "title": "Plain functions", "scores": {"fit": [2, "Matches code"]}}],
        "decisions": [], "handoffs": [], "verdict": None}


class ProtocolTests(unittest.TestCase):
    def test_valid_reply(self):
        r = parse(reply(GOOD))
        self.assertEqual(r.errors, [])
        self.assertEqual(r.summary, GOOD["summary"])
        self.assertEqual(r.options[0]["id"], "O1")
        self.assertEqual(r.prose.strip(), "The options follow.")

    def test_missing_block(self):
        r = parse("Just prose.\n<!-- END -->")
        self.assertIn("no duo block", r.errors)
        self.assertEqual(r.prose.strip(), "Just prose.")

    def test_bad_json(self):
        r = parse(reply("{not json"))
        self.assertTrue(any(e.startswith("bad JSON") for e in r.errors))

    def test_score_without_reason(self):
        bad = dict(GOOD, options=[{"id": "O1", "title": "x", "scores": {"fit": [2, ""]}}])
        self.assertIn("O1.fit: score needs [0-3, reason]", parse(reply(bad)).errors)

    def test_score_out_of_range(self):
        bad = dict(GOOD, options=[{"id": "O1", "title": "x", "scores": {"fit": [5, "r"]}}])
        self.assertIn("O1.fit: score needs [0-3, reason]", parse(reply(bad)).errors)

    def test_summary_too_long(self):
        bad = dict(GOOD, summary=" ".join(["w"] * 21))
        self.assertIn("summary over 20 words", parse(reply(bad)).errors)

    def test_bad_option_id_and_phase(self):
        bad = dict(GOOD, phase="dance", options=[{"id": "X", "title": "x"}])
        errs = parse(reply(bad)).errors
        self.assertIn("unknown phase 'dance'", errs)
        self.assertIn("option id 'X' must look like O1", errs)

    def test_end_marker_stripped(self):
        self.assertNotIn("END", parse(reply(GOOD)).prose)

    def test_last_block_wins(self):
        text = reply({"summary": "old", "phase": "frame"}) + reply(GOOD)
        self.assertEqual(parse(text).summary, GOOD["summary"])


if __name__ == "__main__":
    unittest.main()

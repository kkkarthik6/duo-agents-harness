"""Approved work must not look changed because Duo rewrote its own indexes."""
import tempfile
import unittest
from pathlib import Path

from engine import orchestration as orch


class ReceiptTests(unittest.TestCase):
    def make(self):
        tmp = tempfile.mkdtemp()
        ws = Path(tmp)
        step = ws / "output/task-0001/step-0001"
        step.mkdir(parents=True)
        (ws / "output/task-0001/.steps.json").write_text("{}")
        (ws / "output/task-0001/contents.md").write_text("<!-- Duo outcome index -->\nv1")
        (step / ".steps.json").write_text("{}")
        (ws / "wordstat.py").write_text("print(1)\n")
        (step / "unittest.log").write_text("OK")
        return ws

    def test_duo_indexes_are_not_part_of_the_receipt(self):
        ws = self.make()
        paths = [x["path"] for x in orch.receipt(ws, "T", ["wordstat.py", "output/task-0001/contents.md"])["outputs"]]
        self.assertEqual(paths, ["wordstat.py"])

    def test_rewriting_the_index_does_not_change_approved_work(self):
        ws = self.make()
        record = orch.receipt(ws, "T", ["wordstat.py", "output/task-0001/contents.md"])
        (ws / "output/task-0001/contents.md").write_text("<!-- Duo outcome index -->\nv2, a new step was added")
        self.assertTrue(orch.unchanged(ws, record))

    def test_an_old_receipt_that_listed_the_index_is_still_judged_fairly(self):
        ws = self.make()
        record = {"task_id": "T", "outputs": [{"path": "wordstat.py", "sha256": orch.harness._sha(ws / "wordstat.py")},
                                             {"path": "output/task-0001/contents.md", "sha256": "stale"}]}
        self.assertTrue(orch.unchanged(ws, record))

    def test_a_real_change_to_the_product_is_still_caught(self):
        ws = self.make()
        record = orch.receipt(ws, "T", ["wordstat.py"])
        (ws / "wordstat.py").write_text("print(2)\n")
        self.assertFalse(orch.unchanged(ws, record))

    def test_a_missing_deliverable_counts_as_changed(self):
        ws = self.make()
        record = orch.receipt(ws, "T", ["wordstat.py"])
        (ws / "wordstat.py").unlink()
        self.assertFalse(orch.unchanged(ws, record))


if __name__ == "__main__":
    unittest.main()

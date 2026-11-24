"""Session history for features (contract 1.1.0): list past sessions and runs, read a summary, read and write files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, coreapi, sessionlog


def v2_session(root, sid="1116-0900", title="Make a PDF"):
    ws = Path(root) / "proj"
    d = ws / ".duo" / sid
    d.mkdir(parents=True)
    (ws / "report.pdf").write_text("pdf")
    (ws / "src").mkdir()
    (ws / "src" / "make.py").write_text("print(1)")
    (d / "chat.md").write_text("# chat")
    st = {"id": sid, "title": title, "task": title + " from notes", "status": "done", "created": "2026-11-16T09:00:00",
          "task_type": "programming", "workspace": str(ws), "duo_dir": str(d), "project": "proj", "detail": "Finished",
          "options": [{"id": "A", "title": "Use reportlab"}, {"id": "B", "title": "Use LaTeX"}], "chosen": "A",
          "decisions": [{"id": "D1", "title": "Pick the PDF library", "chosen": "A", "status": "decided"}],
          "entries": [{"n": 1, "author": "human", "phase": "task", "summary": "Make a PDF", "changed": []},
                      {"n": 2, "author": "claude", "phase": "deep", "summary": "Built the PDF", "changed": ["src/make.py"]},
                      {"n": 3, "author": "codex", "phase": "verify", "summary": "PDF opens, 3 pages", "changed": [], "verdict": "APPROVED"}]}
    (d / "state.json").write_text(json.dumps(st))
    (Path(root) / ".duo-index.json").write_text(json.dumps([{"id": sid, "duo_dir": str(d)}]))
    return ws


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.home = Path(tempfile.mkdtemp()) / "Duo"
        self.p = mock.patch.dict(config.CFG, {"root": str(self.root)})
        self.p.start()
        self.ws = v2_session(self.root)

    def tearDown(self):
        self.p.stop()

    def test_list_has_sessions_with_their_folder(self):
        items = sessionlog.list_all(self.home)
        self.assertEqual([(i["id"], i["kind"]) for i in items], [("1116-0900", "session")])
        self.assertEqual(items[0]["folder"], str(self.ws))

    def test_a_session_summary_has_decisions_results_and_artifacts(self):
        d = sessionlog.detail("1116-0900", self.home)
        self.assertEqual(d["chosen"], "Use reportlab")
        self.assertEqual(d["decisions"][0]["chosen"], "Use reportlab")
        self.assertEqual(d["results"], ["PDF opens, 3 pages"])
        self.assertIn("report.pdf", d["artifacts"])
        self.assertIn("src/make.py", d["changed"])
        self.assertNotIn(".duo", " ".join(d["artifacts"]))


    def test_unknown_id_is_refused(self):
        with self.assertRaises(ValueError):
            sessionlog.detail("nope", self.home)

    def test_read_file_stays_inside_the_session_folder(self):
        self.assertEqual(sessionlog.read_file("1116-0900", "src/make.py", self.home), "print(1)")
        with self.assertRaises(ValueError):
            sessionlog.read_file("1116-0900", "../../etc/passwd", self.home)



class ContextSessionTests(SessionLogTests):
    def test_the_contract_is_1_1_with_session_access(self):
        from engine import features
        self.assertTrue(features.satisfies(">=1.1", coreapi.CONTRACT))
        ctx = coreapi.Context("export", home=self.home)
        views = ctx.sessions()
        self.assertIsInstance(views[0], coreapi.SessionView)
        with self.assertRaises(Exception):
            views[0].title = "x"
        self.assertEqual(ctx.session("1116-0900")["title"], "Make a PDF")
        self.assertEqual(ctx.read_session_file("1116-0900", "report.pdf"), "pdf")

    def test_write_session_writes_only_in_the_feature_folder_of_that_session(self):
        ctx = coreapi.Context("export", home=self.home)
        p = ctx.write_session("1116-0900", "summary.md", "# Summary")
        self.assertEqual(p, (self.ws / "duo" / "features" / "export" / "summary.md").resolve())
        with self.assertRaises(ValueError):
            ctx.write_session("1116-0900", "../../../x.md", "no")


if __name__ == "__main__":
    unittest.main()

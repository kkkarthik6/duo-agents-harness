import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, session


def make_run(root, sid, status):
    ws = Path(root) / "proj"
    d = ws / ".duo" / sid[4:15]
    (d / "replies").mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({"id": sid, "status": status}))
    (d / "replies" / "0001-x.md").write_text("reply")
    (ws / "app.py").write_text("print('keep me')")
    return d


class DeleteRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.idx = Path(self.tmp) / ".duo-index.json"
        # Point both the run index and the saved-roots list at the temp folder, so a test can never see or delete a real run.
        for target in (mock.patch.object(config, "root", return_value=Path(self.tmp)),
                       mock.patch.object(config, "DUO_HOME", Path(self.tmp) / "home")):
            target.start()
            self.addCleanup(target.stop)

    def index(self, *runs):
        self.idx.write_text(json.dumps([{"id": sid, "duo_dir": str(d)} for sid, d in runs]))

    def test_finished_run_folder_and_index_entry_are_removed(self):
        sid = "run-20261115-aaaaaaaaaa"
        d = make_run(self.tmp, sid, "done")
        self.index((sid, d))
        session.delete_run(sid)
        self.assertFalse(d.exists())
        self.assertEqual(json.loads(self.idx.read_text()), [])

    def test_project_files_are_never_touched(self):
        sid = "run-20261115-aaaaaaaaaa"
        d = make_run(self.tmp, sid, "stopped")
        self.index((sid, d))
        session.delete_run(sid)
        self.assertTrue((Path(self.tmp) / "proj" / "app.py").exists())

    def test_active_run_is_refused(self):
        sid = "run-20261115-bbbbbbbbbb"
        d = make_run(self.tmp, sid, "running")
        self.index((sid, d))
        with self.assertRaisesRegex(ValueError, "still active"):
            session.delete_run(sid)
        self.assertTrue(d.exists())

    def test_open_live_run_is_refused(self):
        sid = "run-20261115-cccccccccc"
        d = make_run(self.tmp, sid, "done")
        self.index((sid, d))
        with self.assertRaisesRegex(ValueError, "open and running"):
            session.delete_run(sid, live_id=sid)
        self.assertTrue(d.exists())

    def test_folder_outside_a_dot_duo_directory_is_refused(self):
        sid = "run-20261115-dddddddddd"
        d = Path(self.tmp) / "elsewhere" / sid[4:15]
        (d).mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({"id": sid, "status": "done"}))
        self.index((sid, d))
        with self.assertRaisesRegex(ValueError, "not a run folder"):
            session.delete_run(sid)
        self.assertTrue(d.exists())

    def test_unknown_run_is_refused(self):
        self.index()
        with self.assertRaisesRegex(ValueError, "No such run"):
            session.delete_run("nope")


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine import session


def repo():
    d = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / "a.txt").write_text("1")
    subprocess.run(["git", "-C", str(d), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)
    return d


class WorktreeStateTests(unittest.TestCase):
    def test_edit_to_an_already_changed_file_is_seen(self):
        d = repo()
        (d / "a.txt").write_text("2")                       # already modified before the turn
        base = json.loads(json.dumps(session._worktree_state(d)))
        (d / "a.txt").write_text("3")                       # the agent edits it again
        after = session._worktree_state(d)
        self.assertEqual([k for k, v in after.items() if base.get(k) != v], ["a.txt"])

    def test_no_edit_is_not_a_change_after_json_round_trip(self):
        d = repo()
        (d / "a.txt").write_text("2")
        base = json.loads(json.dumps(session._worktree_state(d)))
        after = session._worktree_state(d)
        self.assertEqual([k for k, v in after.items() if base.get(k) != v], [])


if __name__ == "__main__":
    unittest.main()

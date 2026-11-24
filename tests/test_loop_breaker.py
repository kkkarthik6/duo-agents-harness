import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import phases, repeat_defect, task_output, tmux as tmuxmod

REVIEW = "REVISE. The index generator index() writes link destinations with unescaped spaces. %d BROKEN lines."


class RepeatDefectTests(unittest.TestCase):
    def prints(self, *texts):
        return [repeat_defect.fingerprint(t) for t in texts]

    def test_the_same_defect_with_changing_counts_is_a_loop(self):
        self.assertTrue(repeat_defect.looping(self.prints(*[REVIEW % n for n in (148632, 150200, 151000)])))

    def test_two_repeats_are_not_yet_a_loop(self):
        self.assertFalse(repeat_defect.looping(self.prints(REVIEW % 1, REVIEW % 2)))

    def test_different_defects_are_not_a_loop(self):
        other = "REVISE. The parser drops the last row when the file has no trailing newline."
        self.assertFalse(repeat_defect.looping(self.prints(REVIEW % 1, other, REVIEW % 2)))

    def test_the_phase_engine_stops_instead_of_revising_again(self):
        state = {"phase": "deep", "deep_step": "critique", "round": 5, "max_rounds": 100, "last_verdict": "REVISE",
                 "roles": {"builder": "codex", "critic": "claude"}, "agents": ["codex", "claude"], "chosen": "O1", "size": "standard",
                 "review_prints": self.prints(*[REVIEW % n for n in (1, 2, 3)])}
        self.assertEqual(phases.next_turns(state), "stalled")
        self.assertTrue(state["repeat_defect"])


class IndexLinkTests(unittest.TestCase):
    def test_paths_with_spaces_become_valid_link_destinations(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            folder = root / "output" / "task 1"
            folder.mkdir(parents=True)
            (folder / "replies").mkdir()
            task_output.index(root, folder, {"task_id": "t1", "kind": "build", "reply_path": str(folder / "replies" / "reply 1.md")},
                              ["output/task 1/my file (v2).md"])
            text = (folder / "handoffs.md").read_text()
            self.assertIn("(replies/reply%201.md)", text)
            self.assertNotIn(" ", text.split("](")[2].split(")")[0])


class SplitFallbackTests(unittest.TestCase):
    def test_a_full_window_gets_a_new_window_instead_of_failing(self):
        def fake(*args, check=True):
            if args[0] == "split-window":
                raise RuntimeError("tmux split-window failed: no space for a new pane")
            return "%9\n"
        with mock.patch.object(tmuxmod, "tmux", side_effect=fake) as t:
            self.assertEqual(tmuxmod.split("%1", "/tmp", "cmd"), "%9")
            self.assertEqual(t.call_args[0][0], "new-window")

    def test_other_errors_still_fail(self):
        with mock.patch.object(tmuxmod, "tmux", side_effect=RuntimeError("tmux split-window failed: can't find pane")):
            with self.assertRaises(RuntimeError):
                tmuxmod.split("%1", "/tmp", "cmd")


if __name__ == "__main__":
    unittest.main()

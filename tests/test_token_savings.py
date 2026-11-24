"""Skill ranking, the review diff, the token ledger and the round cap."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import skill_inventory, token_ledger
from engine.session import Session

ROWS = [{"name": "pdf", "description": "Read and fill PDF forms. Extra detail.", "path": "/a/pdf/SKILL.md"},
        {"name": "xlsx", "description": "Spreadsheet files as input or output", "path": "/a/xlsx/SKILL.md"},
        {"name": "exif-tools", "description": "Read photo metadata and rename photos by date", "path": "/a/exif/SKILL.md"},
        {"name": "pdf", "description": "Read and fill PDF forms. Extra detail.", "path": "/b/pdf/SKILL.md"}]


class SkillTests(unittest.TestCase):
    def test_only_skills_that_match_the_task_are_listed_and_best_first(self):
        names = [r["name"] for r in skill_inventory.rank(ROWS, "rename my photos by date")]
        self.assertEqual(names, ["exif-tools"])

    def test_a_skill_installed_twice_is_listed_once(self):
        self.assertEqual([r["name"] for r in skill_inventory.rank(ROWS, "fill a pdf form")], ["pdf"])

    def test_unrelated_skills_stay_out_but_the_full_list_is_on_disk(self):
        many = [{"name": "photo-%d" % i, "description": "photo helper. More text", "path": "/s/%d" % i} for i in range(5)]
        many += [{"name": "other%d" % i, "description": "spreadsheets", "path": "/o/%d" % i} for i in range(300)]
        with mock.patch.object(skill_inventory, "discover", return_value=many), tempfile.TemporaryDirectory() as tmp:
            text = skill_inventory.prompt(tmp, ["claude"], "photo organiser", index=str(Path(tmp) / "skills.md"))
            self.assertEqual(text.count("/s/"), 5)
            self.assertNotIn("/o/", text)
            self.assertIn("All 305 skills", text)
            self.assertEqual(len((Path(tmp) / "skills.md").read_text().splitlines()), 305)

    def test_every_matching_skill_is_listed_with_no_count_limit(self):
        many = [{"name": "photo-%d" % i, "description": "photo helper", "path": "/s/%d" % i} for i in range(80)]
        with mock.patch.object(skill_inventory, "discover", return_value=many):
            self.assertEqual(skill_inventory.prompt("/tmp", ["claude"], "photo organiser").count("/s/"), 80)

    def test_words_every_small_task_uses_do_not_match_skills(self):
        rows = [{"name": "xlsx", "description": "Read a spreadsheet file and print the text, count the lines and words", "path": "/x"},
                {"name": "pdf", "description": "Read the file, print text, count words and lines", "path": "/p"}]
        task = "Write a python script that reads a text file and prints the number of lines and words"
        self.assertEqual(skill_inventory.rank(rows, task), [])

    def test_a_skill_needs_a_name_match_or_several_real_word_hits(self):
        rows = [{"name": "exif-tools", "description": "photo metadata", "path": "/e"},
                {"name": "other", "description": "photo metadata rename dates", "path": "/o"},
                {"name": "weak", "description": "photo only", "path": "/w"}]
        names = [r["name"] for r in skill_inventory.rank(rows, "rename photos by exif metadata dates")]
        self.assertEqual(names, ["exif-tools", "other"])

    def test_a_description_keeps_its_whole_first_sentence(self):
        long = "x " * 200
        self.assertEqual(skill_inventory._summary(long + ". Second."), long.strip())




class TokenLedgerTests(unittest.TestCase):
    def test_a_turn_is_counted_when_sent_and_when_answered(self):
        st = {}
        token_ledger.record_issue(st, {"n": 3, "kind": "build", "agent": "codex", "prompt": "x" * 4000,
                                       "role_assignment": {"role": "builder"}}, "gpt-x")
        token_ledger.record_reply(st, 3, 800, 12)
        s = token_ledger.summary(st)
        self.assertEqual((s["turns"], s["in"], s["out"]), (1, 1000, 200))
        self.assertEqual(s["top"][0]["role"], "builder")

    def test_a_retry_adds_to_the_same_turn(self):
        st = {}
        pending = {"n": 4, "kind": "revise", "agent": "codex", "prompt": "x" * 400}
        token_ledger.record_issue(st, pending)
        token_ledger.record_issue(st, pending)
        row = st["tokens"][0]
        self.assertEqual((len(st["tokens"]), row["attempts"], row["prompt_chars"]), (1, 2, 800))

    def test_the_slowest_turns_come_first(self):
        st = {}
        for n, secs in ((1, 5), (2, 90), (3, 30)):
            token_ledger.record_issue(st, {"n": n, "kind": "build", "agent": "a", "prompt": "x"}, now=0)
            token_ledger.record_reply(st, n, 4, now=secs)
        self.assertEqual([r["n"] for r in token_ledger.summary(st)["top"]], [2, 3, 1])

    def test_the_ledger_records_wall_time_per_turn_and_for_the_run(self):
        st = {}
        token_ledger.record_issue(st, {"n": 2, "kind": "exchange", "agent": "a", "prompt": "x"}, now=1000)
        token_ledger.record_reply(st, 2, 40, now=1040)
        token_ledger.record_issue(st, {"n": 3, "kind": "build", "agent": "a", "prompt": "x"}, now=1040)
        token_ledger.record_reply(st, 3, 40, now=1100)
        s = token_ledger.summary(st)
        self.assertEqual(s["seconds"], 100)
        self.assertEqual(s["top"][0]["kind"], "build")
        self.assertEqual(s["by_kind"]["exchange"]["seconds"], 40)

    def test_a_retried_turn_counts_the_time_of_the_failed_attempt_too(self):
        st = {}
        p = {"n": 3, "kind": "plan_review", "agent": "a", "prompt": "x"}
        token_ledger.record_issue(st, p, now=0)
        token_ledger.record_issue(st, p, now=70)
        token_ledger.record_reply(st, 3, 10, now=150)
        self.assertEqual(st["tokens"][0]["seconds"], 150)

    def test_an_empty_run_summarises_to_zero(self):
        self.assertEqual(token_ledger.summary({})["turns"], 0)


class ReviewPatchTests(unittest.TestCase):
    def make(self, tmp):
        ws = Path(tmp) / "ws"
        (ws / ".duo/r/checkpoints/1/files").mkdir(parents=True)
        (ws / ".duo/r/checkpoints/1/files/a.py").write_text("print('old')\n")
        (ws / "a.py").write_text("print('new')\n")
        run = Session.__new__(Session)
        run.st = {"workspace": str(ws), "duo_dir": str(ws / ".duo/r"), "id": "r", "last_build_changed": ["a.py"],
                  "last_build_checkpoint": 1, "agents": [], "roles": {}}
        return run, ws

    def test_the_reviewer_gets_one_diff_file_of_the_latest_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, ws = self.make(tmp)
            rel = run._review_patch(5)
            text = (ws / rel).read_text()
            self.assertIn("-print('old')", text)
            self.assertIn("+print('new')", text)

    def test_a_large_diff_is_given_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, ws = self.make(tmp)
            (ws / "a.py").write_text("x = 1\n" * 20000)
            text = (ws / run._review_patch(5)).read_text()
            self.assertEqual(text.count("+x = 1"), 20000)
            self.assertNotIn("cut at", text)

    def test_nothing_changed_means_no_diff_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = self.make(tmp)
            run.st["last_build_changed"] = []
            self.assertIsNone(run._review_patch(5))


if __name__ == "__main__":
    unittest.main()

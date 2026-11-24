"""The brief an agent gets: complete about what must not be lost, bounded in size."""
import tempfile
import unittest
from pathlib import Path

from engine import handoff, memory

MODELS = {"lead": {"size": "large", "model": "big-1"}, "challenger": {"size": "large", "model": "big-2"},
          "builder": {"size": "small", "model": "small-1"}, "critic": {"size": "medium", "model": "mid-1"}}


def entry(n, kind, author="codex", **kw):
    e = {"n": n, "author": author, "kind": kind, "phase": "deep", "summary": "did %s" % kind, "prose": "prose %s" % kind,
         "changed": [], "cycle": 1, "file": "replies/%04d.md" % n}
    e.update(kw)
    return e


def state(entries, **kw):
    s = {"title": "Tool", "status": "running", "phase": "deep", "round": 1, "max_rounds": 8, "cycle": 1, "_role_models": MODELS,
         "entries": [entry(1, "task", "human", prose="Build a tool that prints hello", file=None)] + entries,
         "plan_verdict": "APPROVED", "stage_requests": {}}
    s.update(kw)
    return s


class TaskAndModelTests(unittest.TestCase):
    def test_the_coder_is_told_its_model_size_its_task_and_who_reads_it(self):
        text = handoff.brief(state([entry(2, "exchange"), entry(3, "plan_review", verdict="APPROVED")]), "build")
        self.assertIn("Coder (small model, small-1)", text)
        self.assertIn("Do not redesign", text)
        self.assertIn("Implement the accepted plan", text)
        self.assertIn("Code reviewer (medium model", text)

    def test_the_reviewer_is_told_to_check_not_rebuild(self):
        text = handoff.brief(state([entry(2, "build")]), "critique", patch=".duo/r/review-changes/0004.patch")
        self.assertIn("Code reviewer (medium model", text)
        self.assertIn("do not rebuild", text)
        self.assertIn(".duo/r/review-changes/0004.patch", text)

    def test_the_planner_is_told_it_carries_the_judgment(self):
        self.assertIn("carry the judgment", handoff.brief(state([]), "exchange"))


class NothingIsLostTests(unittest.TestCase):
    def test_open_defects_reach_the_coder_word_for_word(self):
        review = entry(4, "critique", verdict="REVISE", prose="REVISE\n1. a.py: off by one -> use <=\n2. b.py: no test -> add one")
        text = handoff.brief(state([entry(3, "build"), review]), "revise")
        self.assertIn("a.py: off by one -> use <=", text)
        self.assertIn("b.py: no test -> add one", text)

    def test_a_defect_is_dropped_once_a_later_build_answers_it(self):
        entries = [entry(3, "build"), entry(4, "critique", verdict="REVISE", prose="fix x"), entry(5, "revise")]
        self.assertNotIn("OPEN DEFECTS", handoff.brief(state(entries), "critique"))

    def test_user_constraints_and_messages_persist_through_every_turn(self):
        s = state([entry(3, "build")], constraints=["keep it under 200 lines"])
        s["entries"].append(entry(4, "message", "human", prose="Also support --shout", file=None))
        for kind in ("build", "critique", "revise"):
            text = handoff.brief(s, kind)
            self.assertIn("keep it under 200 lines", text)
            self.assertIn("Also support --shout", text)

    def test_files_changed_accumulate_across_builds_and_skip_run_folders(self):
        entries = [entry(3, "build", changed=["a.py", ".duo/x/state.json"]), entry(5, "revise", changed=["b.py", "a.py"])]
        text = handoff.brief(state(entries), "critique")
        self.assertIn("a.py, b.py", text)
        self.assertNotIn("state.json", text)

    def test_the_last_coder_handoff_is_quoted_for_the_reviewer(self):
        text = handoff.brief(state([entry(3, "build", prose="Added main.py. Ran pytest: 4 passed.")]), "critique")
        self.assertIn("Ran pytest: 4 passed.", text)

    def test_the_follow_up_ask_is_kept_beside_the_original(self):
        s = state([entry(3, "build")])
        s["entries"].append(entry(4, "followup", "human", prose="Now add colors", cycle=2, file=None))
        text = handoff.brief(s, "build")
        self.assertIn("Build a tool that prints hello", text)
        self.assertIn("Now add colors", text)


class GrowthTests(unittest.TestCase):
    def long_run(self, turns):
        entries = []
        for i in range(turns):
            entries.append(entry(2 + i * 2, "build", changed=["f%d.py" % i], prose="x" * 3000))
            entries.append(entry(3 + i * 2, "critique", verdict="REVISE", prose="defect %d. " % i + "y" * 3000))
        return state(entries, constraints=["c%d" % i for i in range(40)])

    def test_the_brief_does_not_grow_with_finished_turns(self):
        """Only the last few turns get a line; older ones are a count. Size grows with files and constraints, not with turns."""
        small = len(handoff.brief(state([entry(3, "build"), entry(4, "critique", verdict="APPROVED")]), "critique"))
        entries = []
        for i in range(200):
            entries += [entry(10 + i * 2, "build"), entry(11 + i * 2, "critique", verdict="APPROVED")]
        long = len(handoff.brief(state(entries), "critique"))
        self.assertLess(long - small, 400)

    def test_old_turns_collapse_to_counts(self):
        text = handoff.brief(self.long_run(30), "revise")
        self.assertIn("earlier:", text)
        self.assertLessEqual(sum(1 for l in text.splitlines() if l.startswith("- #")), handoff.RECENT_TURNS)

    def test_nothing_the_agent_needs_is_cut(self):
        s = self.long_run(60)
        s["entries"][0]["prose"] = "Ask. " + "z" * 5000
        text = handoff.brief(s, "revise")
        self.assertIn("z" * 5000, text)                        # the whole original ask
        for i in range(40):
            self.assertIn("c%d" % i, text)                      # every constraint
        for i in range(60):
            self.assertIn("f%d.py" % i, text)                   # every changed file
        self.assertIn("y" * 3000, text)                         # the whole open defect list
        self.assertIn("defect 59", text)
        self.assertNotIn("…", text)
        self.assertNotIn("more)", text)

    def test_the_task_and_open_defects_are_always_present(self):
        text = handoff.brief(self.long_run(200), "revise")
        self.assertIn("Fix every required defect", text)
        self.assertIn("OPEN DEFECTS", text)
        self.assertIn("defect 199", text)


class MemoryFileTests(unittest.TestCase):
    def test_memory_md_is_the_same_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".duo/r").mkdir(parents=True)
            memory.write(GrowthTests().long_run(100), ws / ".duo/r", ws)
            text = (ws / ".duo/r/memory.md").read_text()
            self.assertTrue(text.startswith("# Run memory"))
            self.assertIn(".duo/r/plan.md", text)


if __name__ == "__main__":
    unittest.main()

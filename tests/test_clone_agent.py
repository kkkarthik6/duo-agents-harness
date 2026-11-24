import json
import tempfile
import unittest
from pathlib import Path

from engine import clone_agent, userspace

FEATURE = "features/thing/feature.py"


def editor(rel, text):
    def agent(cwd, prompt):
        p = Path(cwd, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return True, "done"
    return agent


def passing(cwd, changed):
    return True, "OK"


def failing(tail):
    return lambda cwd, changed: (False, tail)


class Base(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(self.base)

    def make(self, kind="minor"):
        return userspace.clone(self.base, "make the widget smaller", kind)["id"]

    def v(self, vid):
        return userspace.entry(self.base, vid)


class EditTests(Base):
    def test_ready_when_the_agent_edits_a_feature_and_tests_pass(self):
        vid = self.make()
        status = clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "def register(api): pass\n"), tests=passing)
        self.assertEqual(status, "ready")
        self.assertFalse((userspace.user_dir(self.base) / FEATURE).exists())      # the user layer is not touched
        self.assertTrue(Path(self.v(vid)["diff"]).exists())
        self.assertEqual(self.v(vid)["changed"], [FEATURE])

    def test_a_change_outside_the_user_layer_files_is_blocked(self):
        for rel in ("engine/run.py", "web/index.html", "VERSION", "notes.txt"):
            vid = self.make()
            self.assertEqual(clone_agent.run(vid, "x", self.base, agent=editor(rel, "x\n"), tests=passing), "blocked", rel)

    def test_instructions_and_the_user_contract_may_change(self):
        for rel in ("instructions.md", "contract.json"):
            vid = self.make()
            self.assertEqual(clone_agent.run(vid, "x", self.base, agent=editor(rel, "{}\n"), tests=passing), "ready", rel)

    def test_no_change_is_recorded(self):
        vid = self.make()
        self.assertEqual(clone_agent.run(vid, "x", self.base, agent=lambda cwd, p: (True, ""), tests=passing), "no_change")

    def test_the_tests_get_the_changed_paths(self):
        seen = []
        vid = self.make()
        clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "x\n"), tests=lambda cwd, ch: (seen.append(ch), (True, "OK"))[1])
        self.assertEqual(seen, [[FEATURE]])


class CoreRequestTests(Base):
    def test_a_core_request_parks_the_version_and_is_saved_for_upstream(self):
        vid = self.make()
        req = json.dumps({"missing": "a hook when a task is parked", "why": "to send a message", "feature": "notify"})
        status = clone_agent.run(vid, "x", self.base, agent=editor("core_request.json", req), tests=passing)
        self.assertEqual(status, "waiting_for_core")
        saved = json.loads((self.base / "core-requests" / (vid + ".json")).read_text())
        self.assertEqual(saved["missing"], "a hook when a task is parked")
        self.assertEqual(saved["version"], self.v(vid)["number"])
        self.assertIn("core change", self.v(vid)["detail"])

    def test_a_bad_core_request_file_still_parks_the_version(self):
        vid = self.make()
        status = clone_agent.run(vid, "x", self.base, agent=editor("core_request.json", "not json"), tests=passing)
        self.assertEqual(status, "waiting_for_core")


class RepairLoopTests(Base):
    def test_the_same_error_twice_marks_the_version_stuck(self):
        vid = self.make()
        status = clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"),
                                 tests=failing("FAIL: test_x\nAssertionError: boom\nFAILED (failures=1)"))
        self.assertEqual(status, "stuck")
        self.assertEqual(self.v(vid)["repairs"], 1)
        self.assertIn("same error", self.v(vid)["detail"])

    def test_a_repair_that_fixes_the_tests_makes_the_version_ready(self):
        vid = self.make()
        answers = iter([(False, "ERROR: test_a\nKeyError: 'x'\nFAILED (errors=1)"), (True, "OK")])
        status = clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=lambda cwd, ch: next(answers))
        self.assertEqual(status, "ready")
        self.assertEqual(self.v(vid)["repairs"], 1)

    def test_five_repairs_with_changing_errors_mark_it_failed(self):
        vid = self.make()
        calls = []

        def tests(cwd, changed):
            calls.append(1)
            return False, "ERROR: t%d\nKeyError: '%d'\nFAILED (errors=%d)" % (len(calls), len(calls), 10 - len(calls) % 2)

        self.assertEqual(clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=tests), "failed")
        self.assertEqual(self.v(vid)["repairs"], clone_agent.MAX_REPAIRS)
        self.assertIn("Press Repair", self.v(vid)["detail"])

    def test_no_progress_for_two_repairs_marks_it_stuck(self):
        vid = self.make()
        counter = iter(range(100))
        tests = lambda cwd, ch: (False, "ERROR: t%d\nKeyError: '%d'\nFAILED (errors=3)" % (next(counter), next(counter)))
        self.assertEqual(clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=tests), "stuck")
        self.assertEqual(self.v(vid)["repairs"], clone_agent.STALL_LIMIT)
        self.assertIn("No progress", self.v(vid)["detail"])

    def test_repair_runs_again_keeps_the_change_and_takes_the_next_minor_number(self):
        vid = self.make(kind="major")
        self.assertEqual(self.v(vid)["number"], "1.1.0")
        clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=failing("ERROR: a\nKeyError: 'a'\nFAILED (errors=1)"))
        fixed = iter([(False, "ERROR: b\nKeyError: 'b'\nFAILED (errors=1)"), (True, "OK")])
        status = clone_agent.repair(vid, self.base, agent=lambda cwd, p: (True, "fixed"), tests=lambda cwd, ch: next(fixed))
        self.assertEqual(status, "ready")
        self.assertEqual(self.v(vid)["number"], "1.1.1")
        self.assertEqual(Path(self.v(vid)["path"], FEATURE).read_text(), "v2\n")

    def test_repair_refuses_a_version_that_did_not_fail(self):
        vid = self.make()
        clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=passing)
        with self.assertRaises(ValueError):
            clone_agent.repair(vid, self.base, agent=lambda cwd, p: (True, ""), tests=passing)



class RollUpTests(Base):
    def test_accept_rolls_up_only_ready_versions(self):
        vid = self.make()
        clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "def register(api): pass\n"), tests=passing)
        self.assertEqual(clone_agent.accept(vid, self.base), "2.0.0")
        self.assertTrue((userspace.user_dir(self.base) / FEATURE).exists())

    def test_accept_refuses_a_failed_version(self):
        vid = self.make()
        clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=failing("bad"))
        with self.assertRaises(ValueError):
            clone_agent.accept(vid, self.base)

    def test_reject_keeps_the_user_layer(self):
        vid = self.make()
        clone_agent.run(vid, "x", self.base, agent=editor(FEATURE, "v2\n"), tests=passing)
        clone_agent.reject(vid, self.base)
        self.assertEqual(self.v(vid)["status"], "rejected")
        self.assertEqual(userspace.summary(self.base)["number"], "1.0.0")


class CheckCloneTests(unittest.TestCase):
    """The default check: feature rules and each changed feature's own tests, against the real core."""

    def setUp(self):
        self.clone = Path(tempfile.mkdtemp())
        (self.clone / "features").mkdir()
        (self.clone / "features" / "__init__.py").write_text("")
        self.core = Path(__file__).resolve().parent.parent

    def feature(self, name, code, test=None):
        d = self.clone / "features" / name
        d.mkdir()
        (d / "__init__.py").write_text("")
        (d / "feature.json").write_text(json.dumps({"name": name, "version": "0.1.0", "contract": ">=1.0", "where_to_see": "Home"}))
        (d / "PLAN.md").write_text("- rule: api.instructions\n")
        (d / "feature.py").write_text(code)
        if test:
            (d / "tests").mkdir()
            (d / "tests" / "__init__.py").write_text("")
            (d / "tests" / "test_it.py").write_text(test)

    def test_a_feature_that_imports_core_internals_fails_the_check(self):
        self.feature("sneaky", "from engine import store\ndef register(api): pass\n",
                     "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n")
        ok, tail = clone_agent.check_clone(self.clone, self.core, ["features/sneaky/feature.py"])
        self.assertFalse(ok)
        self.assertIn("engine.coreapi only", tail)

    def test_a_good_feature_with_passing_tests_passes(self):
        self.feature("good", "def register(api): api.instructions('x')\n",
                     "import unittest\nfrom engine import coreapi\nclass T(unittest.TestCase):\n    def test(self): self.assertTrue(coreapi.CONTRACT)\n")
        ok, tail = clone_agent.check_clone(self.clone, self.core, ["features/good/feature.py"])
        self.assertTrue(ok, tail)

    def test_only_instructions_changed_needs_no_feature_tests(self):
        ok, _ = clone_agent.check_clone(self.clone, self.core, ["instructions.md"])
        self.assertTrue(ok)


class FailureParsingTests(unittest.TestCase):
    def test_failure_count_adds_failures_and_errors(self):
        self.assertEqual(clone_agent.failure_count("FAILED (failures=2, errors=3)"), 5)
        self.assertIsNone(clone_agent.failure_count("no summary here"))

    def test_the_signature_ignores_counts_and_order(self):
        self.assertEqual(clone_agent.signature("ERROR: test_a\nKeyError: 'x'\nRan 4 tests"),
                         clone_agent.signature("KeyError: 'x'\nERROR: test_a\nRan 9 tests"))


if __name__ == "__main__":
    unittest.main()


class CoreReferenceTests(Base):
    def test_the_agent_can_read_the_core_api_and_example_inside_its_folder(self):
        seen = {}

        def agent(cwd, prompt):
            ref = Path(cwd, "_core_ref")
            seen["api"] = (ref / "coreapi.py").read_text()
            seen["example"] = (ref / "example" / "feature.py").exists()
            seen["prompt"] = prompt
            return editor(FEATURE, "def register(api): pass\n")(cwd, prompt)
        vid = self.make()
        self.assertEqual(clone_agent.run(vid, "x", self.base, agent=agent, tests=passing), "ready")
        self.assertIn("CONTRACT", seen["api"])
        self.assertTrue(seen["example"])
        self.assertIn("_core_ref/coreapi.py", seen["prompt"])
        self.assertFalse(Path(self.v(vid)["path"], "_core_ref").exists())       # removed before roll-up
        self.assertEqual(self.v(vid)["changed"], [FEATURE])                     # and never counted as a change

    def test_an_edit_inside_the_reference_copy_is_ignored(self):
        vid = self.make()
        status = clone_agent.run(vid, "x", self.base, agent=editor("_core_ref/coreapi.py", "hacked\n"), tests=passing)
        self.assertEqual(status, "no_change")

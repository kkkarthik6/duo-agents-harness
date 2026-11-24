"""Core 1.4.0: the feedback flow reports why, checks before it starts, checks the plan, shows where to see the
change, takes screenshots, gets a review from another provider, and notes an untried roll-up."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import clone_agent, feedback, review, userspace, vshots as shots

FEATURE = "features/thing/feature.py"


def editor(rel, text, last="DUO-STATUS: done"):
    def agent(cwd, prompt):
        p = Path(cwd, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return True, "I made the change.\n" + last
    return agent


def passing(cwd, changed):
    return True, "OK"


class Base(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(self.base)
        self.vid = userspace.clone(self.base, "add a sessions page", "major")["id"]

    def v(self):
        return userspace.entry(self.base, self.vid)


class ReasonTests(Base):
    """A: when the agent changes nothing, Duo keeps its reason."""

    def test_a_blocked_agent_marks_the_version_blocked_with_its_reason(self):
        agent = lambda cwd, p: (True, "I could not read the core API.\nDUO-STATUS: blocked: cannot read _core_ref/coreapi.py")
        self.assertEqual(clone_agent.run(self.vid, "x", self.base, agent=agent, tests=passing), "blocked")
        self.assertIn("cannot read _core_ref/coreapi.py", self.v()["detail"])

    def test_no_change_without_a_status_keeps_the_agents_last_words(self):
        agent = lambda cwd, p: (True, "Nothing to do here, the page already exists.")
        self.assertEqual(clone_agent.run(self.vid, "x", self.base, agent=agent, tests=passing), "no_change")
        self.assertIn("the page already exists", self.v()["detail"])

    def test_the_status_line_is_parsed(self):
        self.assertEqual(clone_agent.agent_status("x\nDUO-STATUS: blocked: no network"), ("blocked", "no network"))
        self.assertEqual(clone_agent.agent_status("DUO-STATUS: done"), ("done", ""))
        self.assertEqual(clone_agent.agent_status("no line"), (None, ""))


class PreflightTests(Base):
    """B: Duo checks the copy and the agent before the agent starts."""

    def test_a_missing_agent_command_blocks_before_any_edit(self):
        with mock.patch.object(clone_agent.shutil, "which", return_value=None), \
                mock.patch.object(clone_agent.subprocess, "run") as run:
            status = clone_agent.run(self.vid, "x", self.base, agent=clone_agent.claude_agent, tests=passing)
        self.assertEqual(status, "blocked")
        self.assertIn("claude", self.v()["detail"])
        run.assert_not_called()

    def test_a_missing_copy_blocks(self):
        import shutil
        shutil.rmtree(self.v()["path"])
        self.assertEqual(clone_agent.run(self.vid, "x", self.base, agent=editor(FEATURE, "x"), tests=passing), "blocked")
        self.assertIn("copy", self.v()["detail"])

    def test_preflight_finds_the_core_reference(self):
        clone = Path(self.v()["path"])
        clone_agent.stage_ref(clone)
        self.assertEqual(clone_agent.preflight(clone, agent=None), [])
        (clone / "_core_ref" / "coreapi.py").unlink()
        self.assertTrue(any("coreapi.py" in p for p in clone_agent.preflight(clone, agent=None)))


class PlanAndWhereTests(unittest.TestCase):
    """C and H: each changed feature has PLAN.md and says where to see it."""

    def setUp(self):
        self.clone = Path(tempfile.mkdtemp())
        (self.clone / "features").mkdir()
        (self.clone / "features" / "__init__.py").write_text("")
        self.core = Path(__file__).resolve().parent.parent
        d = self.d = self.clone / "features" / "pg"
        (d / "tests").mkdir(parents=True)
        (d / "__init__.py").write_text("")
        (d / "tests" / "__init__.py").write_text("")
        (d / "tests" / "test_it.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n")
        (d / "feature.py").write_text("def register(api): api.instructions('x')\n")

    def manifest(self, **extra):
        m = {"name": "pg", "version": "0.1.0", "contract": ">=1.2"}
        m.update(extra)
        (self.d / "feature.json").write_text(json.dumps(m))

    def test_a_feature_without_a_plan_fails(self):
        self.manifest(where_to_see="Home")
        ok, tail = clone_agent.check_clone(self.clone, self.core, ["features/pg/feature.py"])
        self.assertFalse(ok)
        self.assertIn("PLAN.md", tail)

    def test_a_feature_without_where_to_see_fails(self):
        self.manifest()
        (self.d / "PLAN.md").write_text("- rule: api.instructions\n")
        ok, tail = clone_agent.check_clone(self.clone, self.core, ["features/pg/feature.py"])
        self.assertFalse(ok)
        self.assertIn("where_to_see", tail)

    def test_plan_and_where_to_see_pass(self):
        self.manifest(where_to_see="Side bar: Sessions")
        (self.d / "PLAN.md").write_text("- page: api.page\n")
        ok, tail = clone_agent.check_clone(self.clone, self.core, ["features/pg/feature.py"])
        self.assertTrue(ok, tail)


class ReadyWhereTests(Base):
    def test_a_ready_version_and_its_notice_say_where_to_see_it(self):
        def agent(cwd, p):
            d = Path(cwd, "features", "pg")
            d.mkdir(parents=True)
            (d / "feature.json").write_text(json.dumps({"name": "pg", "where_to_see": "Side bar: Sessions"}))
            return True, "DUO-STATUS: done"
        feedback._write(self.base, {"request": "x", "version": self.vid, "status": "editing"})
        self.assertEqual(clone_agent.run(self.vid, "x", self.base, agent=agent, tests=passing), "ready")
        self.assertEqual(self.v()["where"], ["Side bar: Sessions"])
        self.assertIn("Where to see it: Side bar: Sessions", feedback.notice(self.base)["text"])


class ShotsAndReviewTests(Base):
    """F and G: screenshots of the changed screens, and a review from another provider."""

    def test_screens_and_the_review_are_recorded_on_a_ready_version(self):
        status = clone_agent.run(self.vid, "x", self.base, agent=editor(FEATURE, "x"), tests=passing,
                                 shoot=lambda clone, changed: (["/tmp/a.png"], ""),
                                 review=lambda clone, fb, diff, pics: (True, "Looks right."))
        self.assertEqual(status, "ready")
        self.assertEqual(self.v()["shots"], ["/tmp/a.png"])
        self.assertEqual(self.v()["review"], {"result": "approved", "notes": "Looks right."})

    def test_a_review_that_asks_for_changes_starts_a_repair_with_its_notes(self):
        prompts, verdicts = [], iter([(False, "The list has no Export button."), (True, "Fixed.")])

        def agent(cwd, p):
            prompts.append(p)
            Path(cwd, "features", "thing").mkdir(parents=True, exist_ok=True)
            Path(cwd, FEATURE).write_text("v%d" % len(prompts))
            return True, "DUO-STATUS: done"
        status = clone_agent.run(self.vid, "x", self.base, agent=agent, tests=passing,
                                 shoot=lambda c, ch: ([], "No Chrome."), review=lambda *a: next(verdicts))
        self.assertEqual(status, "ready")
        self.assertEqual(self.v()["repairs"], 1)
        self.assertIn("The list has no Export button.", prompts[1])

    def test_an_unavailable_reviewer_does_not_block(self):
        status = clone_agent.run(self.vid, "x", self.base, agent=editor(FEATURE, "x"), tests=passing,
                                 shoot=lambda c, ch: ([], "No Chrome found."), review=lambda *a: (None, "codex is not installed."))
        self.assertEqual(status, "ready")
        self.assertEqual(self.v()["review"]["result"], "skipped")
        self.assertIn("No Chrome found.", self.v()["shots_note"])


class ReviewParseTests(unittest.TestCase):
    def test_review_lines(self):
        self.assertEqual(review.parse("ok\nREVIEW: approve"), (True, ""))
        self.assertEqual(review.parse("REVIEW: changes: add tests; fix title"), (False, "add tests; fix title"))
        self.assertEqual(review.parse("no verdict")[0], None)

    def test_no_codex_means_no_review(self):
        with mock.patch.object(review.shutil, "which", return_value=None):
            ok, why = review.codex_review(Path("."), "x", "", [])
        self.assertIsNone(ok)
        self.assertIn("codex", why)


class ShotPlanTests(unittest.TestCase):
    def test_pages_and_panels_map_to_screens(self):
        comps = [{"kind": "page", "feature": "a", "key": "f:a:list"}, {"kind": "panel", "feature": "a", "slot": "home"},
                 {"kind": "panel", "feature": "b", "slot": "home"}, {"kind": "panel", "feature": "a", "slot": "settings"}]
        self.assertEqual(shots.screens(comps, {"a"}), ["#/f/a/list", "#/home"])

    def test_no_chrome_means_no_screenshots(self):
        with mock.patch.object(shots, "find_chrome", return_value=None):
            pics, note = shots.take(Path("."), ["features/a/feature.py"])
        self.assertEqual(pics, [])
        self.assertIn("Chrome", note)


class TriedTests(Base):
    """I: Duo notes when a version is rolled up without a try."""

    def test_using_a_version_marks_it_tried(self):
        userspace.update(self.base, self.vid, status="ready")
        self.assertFalse(self.v().get("tried"))
        userspace.try_version(self.base, self.vid)
        self.assertTrue(self.v()["tried"])


class CoreRequestCountTests(Base):
    """D: the number of open core requests is in /api/health."""

    def test_health_counts_versions_waiting_for_core(self):
        import threading
        from engine import config, features
        from engine.server import Engine, make_server
        from tests.test_server import call
        userspace.update(self.base, self.vid, status="waiting_for_core")
        prev = features.active()
        with mock.patch.object(config, "DUO_HOME", self.base):
            srv = make_server(0, Engine(agents=[], tick=False, home=self.base))
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                self.assertEqual(call(port, "/api/health")[1]["core_requests"], 1)
            finally:
                srv.shutdown()
                features.activate(prev)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shots.find_chrome(), "Chrome is not installed")
class RealScreenshotTests(unittest.TestCase):
    def test_a_changed_page_and_home_panel_are_photographed(self):
        base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(base)
        clone = Path(userspace.clone(base, "add a page", "major")["path"])
        d = clone / "features" / "pg"
        (d / "ui").mkdir(parents=True)
        (d / "__init__.py").write_text("")
        (d / "feature.json").write_text(json.dumps({"name": "pg", "contract": ">=1.2", "where_to_see": "Side bar: Sessions"}))
        (d / "feature.py").write_text("def register(api):\n    api.page('sessions', 'Sessions', 'ui/p.js')\n    api.slot('home', 'ui/p.js')\n")
        (d / "ui" / "p.js").write_text("export default (el) => { el.innerHTML = '<h2>Sessions list</h2>'; };")
        pics, note = shots.take(clone, ["features/pg/feature.py"], base)
        self.assertEqual(len(pics), 2, note)
        for p in pics:
            self.assertGreater(Path(p).stat().st_size, 5000)

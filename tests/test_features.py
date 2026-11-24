import json
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from engine import coreapi, features


def make_feature(user, name, code, manifest=None, tests=None):
    d = Path(user) / "features" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("")
    m = {"name": name, "version": "0.1.0", "contract": ">=1.0", "description": "test feature"}
    m.update(manifest or {})
    (d / "feature.json").write_text(json.dumps(m))
    (d / "feature.py").write_text(textwrap.dedent(code))
    if tests is not None:
        (d / "tests").mkdir(exist_ok=True)
        (d / "tests" / "__init__.py").write_text("")
        (d / "tests" / "test_it.py").write_text(textwrap.dedent(tests))
    return d


class ContractRangeTests(unittest.TestCase):
    def test_ranges(self):
        self.assertTrue(features.satisfies(">=1.0", "1.2.0"))
        self.assertFalse(features.satisfies(">=1.3", "1.2.0"))
        self.assertTrue(features.satisfies(">=1.0,<2", "1.9.9"))
        self.assertFalse(features.satisfies(">=1.0,<2", "2.0.0"))
        self.assertTrue(features.satisfies("^1.1", "1.4.0"))
        self.assertFalse(features.satisfies("^1.1", "2.0.0"))
        self.assertTrue(features.satisfies("1.2.0", "1.2.0"))

    def test_a_bad_range_is_refused(self):
        with self.assertRaises(ValueError):
            features.satisfies("about one", "1.0.0")


class StaticCheckTests(unittest.TestCase):
    def setUp(self):
        self.user = tempfile.mkdtemp()

    def test_coreapi_and_stdlib_imports_are_allowed(self):
        d = make_feature(self.user, "ok", "import json\nfrom engine import coreapi\nfrom engine.coreapi import Stage\nfrom . import helper\n")
        (d / "helper.py").write_text("X = 1\n")
        self.assertEqual(features.static_check(d), [])

    def test_core_internals_are_refused(self):
        for code in ("from engine import store\n",
                     "import importlib\nimportlib.import_module('engine.store')\n", "__import__('engine.security')\n"):
            d = make_feature(self.user, "bad", code)
            self.assertTrue(features.static_check(d), code)

    def test_a_syntax_error_is_reported(self):
        d = make_feature(self.user, "broken", "def (\n")
        self.assertIn("syntax", features.static_check(d)[0].lower())


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.user = tempfile.mkdtemp()

    def test_a_feature_registers_hooks_routes_slots_and_instructions(self):
        make_feature(self.user, "export", """
            from engine import coreapi
            SEEN = []
            def register(api):
                api.on("run.done", lambda e: SEEN.append(e["run"].id))
                api.route("GET", "/count", lambda req: (200, {"n": len(SEEN)}))
                api.slot("home", "ui/home.js")
                api.instructions("Always write an export note.")
        """)
        (Path(self.user) / "features" / "export" / "ui").mkdir()
        (Path(self.user) / "features" / "export" / "ui" / "home.js").write_text("export default () => {}")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, ["export"])
        run = coreapi.RunView.from_state({"id": "1116-1", "folder": "/x", "stage": "done", "status": "done"})
        reg.emit("run.done", {"run": run}, wait=True)
        self.assertEqual(reg.call_route("export", "GET", "/count", {}, {}), (200, {"n": 1}))
        self.assertEqual(reg.slots["home"], [("export", "ui/home.js")])
        self.assertEqual(reg.instruction_text(), "Always write an export note.")

    def test_a_feature_that_needs_a_newer_contract_is_not_loaded(self):
        make_feature(self.user, "future", "def register(api): pass\n", {"contract": ">=9.0"})
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, [])
        self.assertIn("contract", reg.problems["future"][0])

    def test_a_feature_that_imports_core_internals_is_not_loaded(self):
        make_feature(self.user, "sneaky", "from engine import store\ndef register(api): pass\n")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, [])
        self.assertTrue(reg.problems["sneaky"])

    def test_a_feature_that_raises_in_register_is_skipped_and_leaves_nothing_behind(self):
        make_feature(self.user, "half", """
            def register(api):
                api.route("GET", "/x", lambda req: (200, {}))
                raise RuntimeError("boom")
        """)
        make_feature(self.user, "good", "def register(api): api.instructions('ok')\n")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, ["good"])
        self.assertNotIn(("half", "GET", "/x"), reg.routes)
        self.assertIn("boom", reg.problems["half"][0])

    def test_disabled_features_are_not_loaded(self):
        make_feature(self.user, "off", "def register(api): pass\n")
        reg = features.load(self.user, disabled={"off"})
        self.assertEqual(reg.loaded, [])
        self.assertEqual(reg.problems["off"], ["Turned off."])

    def test_unknown_events_and_slots_are_refused(self):
        make_feature(self.user, "typo", "def register(api): api.on('run.finished', print)\n")
        make_feature(self.user, "slot", "def register(api): api.slot('sidebar', 'ui/x.js')\n")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, [])

    def test_a_hook_that_raises_does_not_stop_other_hooks(self):
        make_feature(self.user, "a", "def register(api): api.on('app.started', lambda e: 1/0)\n")
        make_feature(self.user, "b", """
            HIT = []
            def register(api): api.on('app.started', lambda e: HIT.append(1))
        """)
        reg = features.load(self.user)
        reg.emit("app.started", {}, wait=True)
        self.assertTrue(any("ZeroDivisionError" in e["error"] for e in reg.errors))
        import sys
        self.assertEqual(sys.modules["duo_user_features.b.feature"].HIT, [1])

    def test_a_route_that_raises_returns_500_with_the_reason(self):
        make_feature(self.user, "r", "def register(api): api.route('POST', '/go', lambda req: 1/0)\n")
        reg = features.load(self.user)
        code, body = reg.call_route("r", "POST", "/go", {}, {})
        self.assertEqual(code, 500)
        self.assertIn("ZeroDivisionError", body["error"])

    def test_an_unknown_route_is_404(self):
        self.assertEqual(features.load(self.user).call_route("none", "GET", "/x", {}, {})[0], 404)


class StageAndGateTests(unittest.TestCase):
    def setUp(self):
        self.user = tempfile.mkdtemp()
        self.folder = Path(tempfile.mkdtemp())

    def test_a_feature_stage_runs_after_its_core_stage_and_writes_only_its_folder(self):
        make_feature(self.user, "export", """
            from engine import coreapi
            class ExportStage(coreapi.Stage):
                name = "export"
                after = "deliver"
                def run(self, ctx):
                    ctx.write("summary.md", "run " + ctx.run.id)
                    ctx.note("exported")
            def register(api): api.stage(ExportStage)
        """)
        reg = features.load(self.user)
        run = coreapi.RunView.from_state({"id": "r1", "folder": str(self.folder), "stage": "done"})
        notes = []
        out = reg.after_stage("deliver", run, self.folder, notes.append)
        self.assertEqual(out, [])
        self.assertEqual((self.folder / "duo" / "features" / "export" / "summary.md").read_text(), "run r1")
        self.assertEqual(notes, ["export: exported"])

    def test_ctx_write_refuses_paths_outside_the_feature_folder(self):
        make_feature(self.user, "esc", """
            from engine import coreapi
            class S(coreapi.Stage):
                name = "s"; after = "plan"
                def run(self, ctx): ctx.write("../../escape.md", "x")
            def register(api): api.stage(S)
        """)
        reg = features.load(self.user)
        run = coreapi.RunView.from_state({"id": "r1", "folder": str(self.folder)})
        reg.after_stage("plan", run, self.folder, lambda t: None)
        self.assertFalse((self.folder / "escape.md").exists())
        self.assertTrue(any("outside" in e["error"] for e in reg.errors))

    def test_a_stage_cannot_take_a_core_stage_name_or_an_unknown_after(self):
        make_feature(self.user, "clash", """
            from engine import coreapi
            class S(coreapi.Stage):
                name = "plan"; after = "plan"
                def run(self, ctx): pass
            def register(api): api.stage(S)
        """)
        make_feature(self.user, "lost", """
            from engine import coreapi
            class S(coreapi.Stage):
                name = "x"; after = "nowhere"
                def run(self, ctx): pass
            def register(api): api.stage(S)
        """)
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, [])

    def test_a_slow_stage_is_stopped_waiting_and_recorded(self):
        make_feature(self.user, "slow", """
            import time
            from engine import coreapi
            class S(coreapi.Stage):
                name = "slow"; after = "plan"; timeout = 0.2
                def run(self, ctx): time.sleep(2)
            def register(api): api.stage(S)
        """)
        reg = features.load(self.user)
        t0 = time.time()
        reg.after_stage("plan", coreapi.RunView.from_state({"id": "r"}), self.folder, lambda t: None)
        self.assertLess(time.time() - t0, 1.5)
        self.assertTrue(any("time" in e["error"] for e in reg.errors))




class RunViewTests(unittest.TestCase):
    def test_run_view_is_read_only(self):
        v = coreapi.RunView.from_state({"id": "r", "stage": "plan", "extra": 1})
        with self.assertRaises(Exception):
            v.stage = "done"
        self.assertEqual(v.stage, "plan")


class FeatureTestRunnerTests(unittest.TestCase):
    def test_each_feature_runs_its_own_tests_against_core(self):
        user = tempfile.mkdtemp()
        (Path(user) / "features" / "__init__.py").parent.mkdir(parents=True)
        (Path(user) / "features" / "__init__.py").write_text("")
        make_feature(user, "pass_me", "def register(api): pass\n", tests="""
            import unittest
            from engine import coreapi
            class T(unittest.TestCase):
                def test_ok(self): self.assertTrue(coreapi.CONTRACT)
        """)
        make_feature(user, "fail_me", "def register(api): pass\n", tests="""
            import unittest
            class T(unittest.TestCase):
                def test_bad(self): self.assertEqual(1, 2)
        """)
        core = Path(__file__).resolve().parent.parent
        out = features.feature_tests(user, core)
        self.assertTrue(out["pass_me"][0], out["pass_me"][1])
        self.assertFalse(out["fail_me"][0])
        self.assertIn("FAILED", out["fail_me"][1])


if __name__ == "__main__":
    unittest.main()

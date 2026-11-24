"""Core contract tests. `duo update` runs these in a new core before it switches to it.

They check what user features rely on: the names in engine.coreapi, loading a feature, calling it from a
run and from the server, and the user layer. Keep them fast (well under the 110-second limit).
"""
import json
import shutil
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from engine import coreapi, features, userspace

ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLE = ROOT / "tests" / "fixtures" / "feature_export"


def user_with_example():
    user = Path(tempfile.mkdtemp())
    (user / "features").mkdir()
    (user / "features" / "__init__.py").write_text("")
    shutil.copytree(EXAMPLE, user / "features" / "feature_export")
    return user


class ContractNamesTests(unittest.TestCase):
    def test_contract_1_names_are_present(self):
        self.assertEqual(coreapi.CONTRACT.split(".")[0], "1")
        for name in ("RunView", "SessionView", "Context", "Stage", "Gate", "Api", "EVENTS", "SLOTS", "CORE_STAGES", "COMPONENT_KINDS"):
            self.assertTrue(hasattr(coreapi, name), name)
        for e in ("app.started", "run.started", "stage.changed", "gate.opened", "task.done", "run.done"):
            self.assertIn(e, coreapi.EVENTS)
        for s in ("home", "header", "settings"):
            self.assertIn(s, coreapi.SLOTS)
        for m in ("on", "route", "slot", "page", "component", "instructions", "setting", "stage", "gate"):
            self.assertTrue(callable(getattr(coreapi.Api("x"), m)), m)
        for m in ("write", "read", "note", "setting", "runs", "sessions", "session", "read_session_file", "write_session"):
            self.assertTrue(callable(getattr(coreapi.Context("x"), m)), m)


class ExampleFeatureTests(unittest.TestCase):
    def setUp(self):
        self.prev = features.active()

    def tearDown(self):
        features.activate(self.prev)

    def test_the_example_feature_loads_and_answers_its_route(self):
        reg = features.load(user_with_example())
        self.assertEqual(reg.loaded, ["feature_export"], reg.problems)
        reg.runs_fn = lambda: [coreapi.RunView.from_state({"id": "r1", "status": "done"})]
        self.assertEqual(reg.call_route("feature_export", "GET", "/runs", {}, {})[1]["runs"][0]["id"], "r1")
        kinds = sorted((c["kind"], c.get("key") or c.get("slot")) for c in reg.components)
        self.assertEqual(kinds, [("page", "f:feature_export:runs"), ("panel", "home")])

    def test_every_component_kind_has_a_core_adapter(self):
        from engine import components
        self.assertEqual(set(components.ADAPTERS), set(coreapi.COMPONENT_KINDS))

    def test_the_example_feature_passes_its_own_tests_against_this_core(self):
        user = user_with_example()
        ok, tail = features.feature_tests(user, ROOT)["feature_export"]
        self.assertTrue(ok, tail)



class ServerContractTests(unittest.TestCase):
    def test_the_server_serves_versions_features_and_feature_routes(self):
        from engine.server import Engine, make_server
        from tests.test_server import call
        prev = features.active()
        home = Path(tempfile.mkdtemp()) / "Duo"
        from engine import config
        isolated = mock.patch.dict(config.CFG, {"root": str(home / "Projects"), "tmux_session": "duo-contract-" + home.parent.name})
        isolated.start()
        self.addCleanup(isolated.stop)
        userspace.ensure(home)
        shutil.copytree(EXAMPLE, userspace.user_dir(home) / "features" / "feature_export")
        srv = make_server(0, Engine(agents=[], tick=False, home=home))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertEqual(call(port, "/api/health")[1]["number"], "1.0.0")
            self.assertEqual(call(port, "/api/versions")[1]["number"], "1.0.0")
            self.assertEqual(call(port, "/api/features")[1]["features"][0]["name"], "feature_export")
            self.assertEqual(call(port, "/api/f/feature_export/runs"), (200, {"runs": []}))
        finally:
            srv.shutdown()
            features.activate(prev)


class UserLayerContractTests(unittest.TestCase):
    def test_clone_roll_up_and_go_back(self):
        home = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(home)
        v = userspace.clone(home, "add export", "major")
        self.assertEqual(v["number"], "1.1.0")
        userspace.update(home, v["id"], status="ready")
        self.assertEqual(userspace.roll_up(home, v["id"]), "2.0.0")
        self.assertEqual(userspace.go_back(home), "1.0.0")


if __name__ == "__main__":
    unittest.main()

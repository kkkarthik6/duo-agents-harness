"""Server routes for the user layer, the sealed core and user features."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine import config, coremgr, features, feedback as feedback_mod, userspace
from engine.server import Engine, make_server
from tests.test_server import call

FEATURE = '''
from engine import coreapi
def register(api):
    api.setting("page_size", 20, minimum=1, maximum=100)
    api.route("GET", "/settings", lambda req: (200, {"page_size": req["ctx"].setting("page_size")}))
    api.route("GET", "/hello", lambda req: (200, {"hi": req["query"].get("name", "you")}))
    api.route("POST", "/echo", lambda req: (200, {"got": req["body"]}))
    api.slot("home", "ui/home.js")
'''


class FeatureServerTests(unittest.TestCase):
    def setUp(self):
        self.prev = features.active()
        self.home = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(self.home)
        d = userspace.user_dir(self.home) / "features" / "hello"
        (d / "ui").mkdir(parents=True)
        (d / "__init__.py").write_text("")
        (d / "feature.json").write_text(json.dumps({"name": "hello", "version": "0.2.0", "contract": ">=1.0"}))
        (d / "feature.py").write_text(FEATURE)
        (d / "ui" / "home.js").write_text("export default function (el) { el.textContent = 'hi'; }")
        (d / "secret.txt").write_text("no")
        self.patch = mock.patch.object(config, "DUO_HOME", self.home)
        self.patch.start()
        self.engine = Engine(agents=[], tick=False, home=self.home)
        self.srv = make_server(0, self.engine)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.patch.stop()
        features.activate(self.prev)

    def raw(self, path):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as ex:
            return ex.code, b""

    def test_features_are_listed_with_their_slots(self):
        code, body = call(self.port, "/api/features")
        self.assertEqual(code, 200)
        self.assertEqual(body["features"][0]["name"], "hello")
        self.assertEqual(body["slots"]["home"], [{"feature": "hello", "file": "ui/home.js"}])

    def test_extension_controls_persist_and_reach_routes(self):
        with mock.patch.dict(config.CFG, {"features": {}, "feature_controls": {}}):
            code, body = call(self.port, "/api/extensions")
            self.assertEqual(code, 200)
            self.assertEqual(body["features"][0]["controls"][0]["key"], "page_size")
            self.assertEqual(call(self.port, "/api/extensions/configure", {"feature": "hello", "values": {"page_size": 7}})[0], 200)
            self.assertEqual(call(self.port, "/api/f/hello/settings")[1]["page_size"], 7)
            saved = json.loads((self.home / "config.json").read_text())
            self.assertEqual(saved["features"]["hello"]["page_size"], 7)
            self.assertEqual(call(self.port, "/api/extensions/configure", {"feature": "hello", "values": {"page_size": 1000}})[0], 400)

    def test_lifecycle_changes_wait_for_active_runs(self):
        with mock.patch.object(self.engine, "has_active_runs", return_value=True):
            self.assertEqual(call(self.port, "/api/extensions/configure", {"feature": "hello", "enabled": False})[0], 400)

    def test_disabled_feature_has_no_routes_until_enabled_again(self):
        with mock.patch.dict(config.CFG, {"feature_controls": {}, "features": {}}):
            self.assertEqual(call(self.port, "/api/extensions/configure", {"feature": "hello", "enabled": False})[0], 200)
            self.assertEqual(call(self.port, "/api/f/hello/hello")[0], 404)
            self.assertEqual(call(self.port, "/api/extensions/configure", {"feature": "hello", "enabled": True})[0], 200)
            self.assertEqual(call(self.port, "/api/f/hello/hello")[0], 200)

    def test_feature_routes_answer_get_and_post(self):
        self.assertEqual(call(self.port, "/api/f/hello/hello?name=Vamsi"), (200, {"hi": "Vamsi"}))
        self.assertEqual(call(self.port, "/api/f/hello/echo", {"a": 1}), (200, {"got": {"a": 1}}))
        self.assertEqual(call(self.port, "/api/f/hello/nope")[0], 404)

    def test_only_registered_slot_files_are_served(self):
        code, data = self.raw("/f/hello/ui/home.js")
        self.assertEqual((code, data[:6]), (200, b"export"))
        self.assertEqual(self.raw("/f/hello/secret.txt")[0], 404)
        self.assertEqual(self.raw("/f/hello/../../user-versions.json")[0], 404)

    def test_versions_show_user_number_core_and_next_numbers(self):
        code, v = call(self.port, "/api/versions")
        self.assertEqual(code, 200)
        self.assertEqual(v["number"], "1.0.0")
        self.assertEqual(v["next"], {"major": "1.1.0", "minor": "1.0.1"})
        self.assertIn("core", v)

    def test_roll_up_reloads_features_and_go_back_undoes_it(self):
        with mock.patch.object(feedback_mod, "spawn_worker"):
            code, body = call(self.port, "/api/feedback", {"text": "add a bye feature", "kind": "minor"})
        vid = body["reference"]["version"]
        d = Path(body["reference"]["path"]) / "features" / "bye"
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("")
        (d / "feature.json").write_text(json.dumps({"name": "bye", "contract": ">=1.0"}))
        (d / "feature.py").write_text("def register(api): api.route('GET', '/x', lambda r: (200, {'bye': 1}))\n")
        userspace.update(self.home, vid, status="ready")
        code, body = call(self.port, "/api/versions/rollup", {"id": vid})
        self.assertEqual((code, body["number"]), (200, "2.0.0"))
        self.assertEqual(call(self.port, "/api/f/bye/x"), (200, {"bye": 1}))
        code, body = call(self.port, "/api/versions/goback", {})
        self.assertEqual((code, body["number"]), (200, "1.0.0"))
        self.assertEqual(call(self.port, "/api/f/bye/x")[0], 404)

    def test_use_this_version_loads_the_copy_and_stop_returns(self):
        with mock.patch.object(feedback_mod, "spawn_worker"):
            body = call(self.port, "/api/feedback", {"text": "add a bye feature", "kind": "major"})[1]
        vid = body["reference"]["version"]
        d = Path(body["reference"]["path"]) / "features" / "bye"
        d.mkdir(parents=True)
        (d / "feature.json").write_text(json.dumps({"name": "bye", "contract": ">=1.0"}))
        (d / "feature.py").write_text("def register(api): api.route('GET', '/x', lambda r: (200, {'bye': 1}))\n")
        userspace.update(self.home, vid, status="ready")
        code, body = call(self.port, "/api/versions/use", {"id": vid})
        self.assertEqual((code, body["trying"]), (200, "1.1.0"))
        self.assertEqual(call(self.port, "/api/f/bye/x"), (200, {"bye": 1}))
        self.assertEqual(call(self.port, "/api/versions")[1]["trying"]["id"], vid)
        self.assertEqual(call(self.port, "/api/health")[1]["trying"], "1.1.0")
        self.assertEqual(call(self.port, "/api/versions/stop", {})[0], 200)
        self.assertEqual(call(self.port, "/api/f/bye/x")[0], 404)

    def test_roll_up_of_an_unready_version_is_refused(self):
        with mock.patch.object(feedback_mod, "spawn_worker"):
            body = call(self.port, "/api/feedback", {"text": "x", "kind": "minor"})[1]
        self.assertEqual(call(self.port, "/api/versions/rollup", {"id": body["reference"]["version"]})[0], 400)

    def test_core_go_back_without_an_earlier_core_is_refused(self):
        self.assertEqual(call(self.port, "/api/core/goback", {})[0], 400)

    def test_feedback_with_a_bad_kind_is_refused(self):
        self.assertEqual(call(self.port, "/api/feedback", {"text": "x", "kind": "patch"})[0], 400)

    def test_a_disabled_feature_is_not_loaded_and_says_why(self):
        coremgr._disable(self.home, "hello", "Its tests failed on core 1.1.0.")
        self.engine.load_features()
        body = call(self.port, "/api/features")[1]
        self.assertFalse(body["features"][0]["loaded"])
        self.assertIn("hello", body["disabled"])
        self.assertEqual(call(self.port, "/api/f/hello/hello")[0], 404)


if __name__ == "__main__":
    unittest.main()


class PageTests(unittest.TestCase):
    def test_the_page_has_the_slots_and_the_version_controls(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
        for needle in ('id="slotHeader"', 'id="slotHome"', 'id="slotSettings"', "data-rollup", "data-copyreq", "data-try",
                       'id="homeBtn"', "function showHome", "/api/versions/use", "/api/versions/stop",
                       "Auto fix after Duo update", "/api/versions/goback", "/api/core/goback", 'value="major"', 'value="minor"'):
            self.assertIn(needle, html)


class FeedbackDuringRunTests(unittest.TestCase):
    """Feedback goes to the feedback log and a version copy, never into the running task."""

    def test_feedback_does_not_reach_an_active_run(self):
        from engine import personal, session
        home = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(home)
        prev = features.active()
        with mock.patch.object(config, "DUO_HOME", home), mock.patch.object(feedback_mod, "spawn_worker") as spawn, \
                mock.patch.object(personal, "add_feedback", side_effect=lambda text, ctx, reaction=None: {"id": "F1", "text": text, "status": "pending"}), \
                mock.patch.object(personal, "_mark"):
            engine = Engine(agents=[], tick=False, home=home)
            run = mock.Mock()
            run.st = {"status": "running"}
            engine.session = run
            with mock.patch.object(session, "ACTIVE", ("running",)):
                rec = engine.add_feedback({"text": "Add an export button", "context": {"screen": "timeline"}})
            run.human_message.assert_not_called()
            self.assertEqual(rec["text"], "Add an export button")
        features.activate(prev)

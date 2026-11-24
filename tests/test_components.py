"""Contract 1.2: features add components (pages, panels) through adapters."""
import json
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine import components, config, coreapi, features, settings, userspace


def feature(user, name, code, files=("ui/page.js",), contract=">=1.2"):
    d = Path(user) / "features" / name
    (d / "ui").mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("")
    (d / "feature.json").write_text(json.dumps({"name": name, "version": "0.1.0", "contract": contract}))
    (d / "feature.py").write_text(textwrap.dedent(code))
    for f in files:
        (d / f).write_text("export default function (el, duo) { el.textContent = 'hi'; }")
    return d


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "ui").mkdir()
        (self.dir / "ui" / "page.js").write_text("x")

    def test_every_kind_in_the_contract_has_an_adapter(self):
        self.assertEqual(set(components.ADAPTERS), set(coreapi.COMPONENT_KINDS))

    def test_the_page_adapter_turns_a_spec_into_a_page_description(self):
        out = components.adapt("history", "page", {"id": "sessions", "title": "Sessions", "file": "ui/page.js"}, self.dir)
        self.assertEqual(out, {"kind": "page", "feature": "history", "id": "sessions", "key": "f:history:sessions",
                               "title": "Sessions", "module": "/f/history/ui/page.js", "nav": "side", "order": 100,
                               "files": ["ui/page.js"]})

    def test_the_panel_adapter_keeps_the_slot(self):
        out = components.adapt("history", "panel", {"slot": "home", "file": "ui/page.js"}, self.dir)
        self.assertEqual((out["kind"], out["slot"], out["module"]), ("panel", "home", "/f/history/ui/page.js"))

    def test_bad_specs_are_refused_with_a_reason(self):
        bad = [("page", {"id": "Bad Id", "title": "x", "file": "ui/page.js"}),
               ("page", {"id": "ok", "title": "", "file": "ui/page.js"}),
               ("page", {"id": "ok", "title": "x", "file": "ui/missing.js"}),
               ("page", {"id": "ok", "title": "x", "file": "../escape.js"}),
               ("page", {"id": "ok", "title": "x", "file": "ui/page.js", "nav": "top"}),
               ("page", {"id": "ok", "title": "x" * 41, "file": "ui/page.js"}),
               ("panel", {"slot": "sidebar", "file": "ui/page.js"})]
        for kind, spec in bad:
            with self.assertRaises(ValueError, msg=spec):
                components.adapt("f", kind, spec, self.dir)

    def test_a_kind_without_an_adapter_asks_for_a_core_request(self):
        with self.assertRaises(ValueError) as cm:
            components.adapt("f", "toolbar", {}, self.dir)
        self.assertIn("core request", str(cm.exception))


class ApiTests(unittest.TestCase):
    def test_page_and_slot_are_shortcuts_for_components(self):
        api = coreapi.Api("history")
        api.page("sessions", "Sessions", "ui/page.js")
        api.slot("home", "ui/panel.js")
        self.assertEqual([k for k, _ in api.components], ["page", "panel"])
        self.assertEqual(api.slots, [("home", "ui/panel.js")])          # contract 1.0 view still filled

    def test_an_unknown_kind_is_refused_at_register_time(self):
        with self.assertRaises(ValueError):
            coreapi.Api("x").component("toolbar", file="ui/x.js")

    def test_contract_is_1_3(self):
        self.assertEqual(coreapi.CONTRACT, "1.3.0")


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.user = Path(tempfile.mkdtemp())
        self.prev = features.active()

    def tearDown(self):
        features.activate(self.prev)

    def test_a_feature_page_is_loaded_and_its_file_may_be_served(self):
        feature(self.user, "history", "def register(api): api.page('sessions', 'Sessions', 'ui/page.js')\n")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, ["history"])
        self.assertEqual(reg.components[0]["key"], "f:history:sessions")
        self.assertTrue(reg.serves("history", "ui/page.js"))
        self.assertFalse(reg.serves("history", "feature.py"))

    def test_a_feature_with_a_bad_component_is_not_loaded(self):
        feature(self.user, "broken", "def register(api): api.page('sessions', 'Sessions', 'ui/nothere.js')\n")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, [])
        self.assertIn("nothere.js", reg.problems["broken"][0])

    def test_two_pages_with_the_same_key_keep_the_first(self):
        feature(self.user, "a", "def register(api):\n    api.page('p', 'One', 'ui/page.js')\n    api.page('p', 'Two', 'ui/page.js')\n")
        reg = features.load(self.user)
        self.assertEqual(reg.loaded, [])
        self.assertIn("twice", reg.problems["a"][0])

    def test_pages_come_in_order(self):
        feature(self.user, "a", "def register(api):\n    api.page('z', 'Zed', 'ui/page.js', order=5)\n    api.page('y', 'Why', 'ui/page.js', order=1)\n")
        reg = features.load(self.user)
        self.assertEqual([c["id"] for c in reg.view()["components"] if c["kind"] == "page"], ["y", "z"])

    def test_old_slot_features_still_show_as_panels(self):
        feature(self.user, "old", "def register(api): api.slot('home', 'ui/page.js')\n", contract=">=1.0")
        reg = features.load(self.user)
        self.assertEqual(reg.view()["components"][0]["kind"], "panel")
        self.assertEqual(reg.slots["home"], [("old", "ui/page.js")])


class StartPageSettingTests(unittest.TestCase):
    def test_start_page_values(self):
        for ok in ("home", "new", "f:history:sessions"):
            self.assertEqual(settings.check_global({"start_page": ok}), {"start_page": ok})
        for bad in ("", "f:Bad:x", "anywhere", 3):
            with self.assertRaises(ValueError):
                settings.check_global({"start_page": bad})


class ServerTests(unittest.TestCase):
    def test_features_route_lists_components_and_the_start_page(self):
        from engine.server import Engine, make_server
        from tests.test_server import call
        prev = features.active()
        home = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(home)
        feature(userspace.user_dir(home), "history", "def register(api): api.page('sessions', 'Sessions', 'ui/page.js')\n")
        with mock.patch.object(config, "DUO_HOME", home), mock.patch.dict(config.CFG, {"start_page": "f:history:sessions"}):
            srv = make_server(0, Engine(agents=[], tick=False, home=home))
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                body = call(port, "/api/features")[1]
                self.assertEqual(body["components"][0]["key"], "f:history:sessions")
                self.assertEqual(body["start_page"], "f:history:sessions")
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:%d/f/history/ui/page.js" % port) as r:
                    self.assertEqual(r.status, 200)
            finally:
                srv.shutdown()
                features.activate(prev)


class PageShellTests(unittest.TestCase):
    def test_the_page_has_the_component_adapters_and_routing(self):
        html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
        for needle in ("DuoComponents", "adapters", "page:", "panel:", "#/f/", "id=\"featureNav\"", "gStart", "start_page"):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()


class PageScriptTests(unittest.TestCase):
    """The page's main script is a module; check its syntax and keep one-time setup out of connect()."""

    def script(self):
        import re
        html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
        return re.findall(r'<script type="module">(.*?)</script>', html, re.S)[0]

    def test_the_module_script_parses(self):
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        f = Path(tempfile.mkdtemp()) / "page.mjs"
        f.write_text(self.script())
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_connect_only_opens_the_event_stream(self):
        js = self.script()
        body = js[js.index("function connect() {"):]
        body = body[:body.index("\n}\n")]
        self.assertNotIn("setInterval", body)
        self.assertNotIn("function ", body[len("function connect() {"):])

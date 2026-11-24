"""Integration tests: the HTTP surface and the screenshot step, with the real server and no agents.
Each test finishes in well under a second; nothing here waits on a real agent or a real Docker daemon."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from engine import config, session, settings, sandbox, shots
from engine.agents import Agent
from engine.server import Engine, make_server


def call(port, path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read() or b"null")


class HttpSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(agents=[Agent("codex", "Codex", "codex", "codex", "1.0")], tick=False)
        cls.srv = make_server(0, cls.engine)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_health_reports_the_app_version_from_the_version_file(self):
        code, body = call(self.port, "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["app"], (config.APP_DIR / "VERSION").read_text().strip())

    def test_config_lists_only_the_global_settings(self):
        code, body = call(self.port, "/api/config")
        self.assertEqual(code, 200)
        self.assertEqual(set(body["config"]), set(settings.GLOBAL))

    def test_config_save_writes_and_returns_the_new_values(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DUO_HOME", Path(tmp)), \
                mock.patch.dict(config.CFG, {}, clear=False):
            code, body = call(self.port, "/api/config", {"stall_minutes": 30})
            self.assertEqual(code, 200)
            self.assertEqual(body["config"]["stall_minutes"], 30)
            self.assertIn('"stall_minutes": 30', (Path(tmp) / "config.json").read_text())

    def test_config_refuses_a_key_outside_the_allow_list(self):
        code, body = call(self.port, "/api/config", {"port": 1})
        self.assertEqual(code, 400)
        self.assertIn("cannot be changed here", body["error"])

    def test_config_refuses_a_bad_value_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DUO_HOME", Path(tmp)):
            code, body = call(self.port, "/api/config", {"stall_minutes": 99999})
            self.assertEqual(code, 400)
            self.assertFalse((Path(tmp) / "config.json").exists())

    def test_ready_lists_agents_docker_and_tmux(self):
        code, body = call(self.port, "/api/ready")
        self.assertEqual(code, 200)
        ids = [c["id"] for c in body["checks"]]
        self.assertIn("docker", ids)
        self.assertIn("tmux", ids)
        self.assertTrue(any(i.startswith("agent:") for i in ids))

    def test_shot_for_an_unknown_run_is_not_found(self):
        code, _ = call(self.port, "/api/shot?id=nope&f=round-1.png")
        self.assertEqual(code, 404)

    def test_steer_without_a_run_is_refused(self):
        self.engine.session = None
        code, body = call(self.port, "/api/steer", {"action": "constrain", "value": "x"})
        self.assertGreaterEqual(code, 400)
        self.assertIn("error", body)

    def test_events_on_connect_send_the_state(self):
        req = urllib.request.Request("http://127.0.0.1:%d/api/events?since=-1" % self.port)
        with urllib.request.urlopen(req, timeout=5) as r:
            first = r.readline().decode()
        self.assertTrue(first.startswith("data:"), first)


class ScreenshotStepTests(unittest.TestCase):
    """The screenshot step runs after a passing smoke test. It must never fail the run, and it must always stop the app."""

    def make(self, tmp):
        run_dir, ws = Path(tmp) / "run", Path(tmp) / "ws"
        run_dir.mkdir()
        ws.mkdir()
        s = session.Session.__new__(session.Session)
        s.st = {"id": "ABC123", "round": 2, "shots": [], "duo_dir": str(run_dir), "workspace": str(ws)}
        return s

    def test_a_running_app_is_photographed_and_then_stopped(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sandbox, "serve", return_value=(True, "")) as serve, \
                mock.patch.object(sandbox, "unserve") as unserve, \
                mock.patch.object(shots, "capture", return_value={"kind": "image", "file": "round-2.png", "warning": None}) as cap, \
                mock.patch.object(time, "sleep"):
            s = self.make(tmp)
            s._shot({"ports": [8000]}, "smoke ok")
        serve.assert_called_once()
        cap.assert_called_once()
        unserve.assert_called_once_with("ABC123")
        self.assertEqual(s.st["shots"][0]["file"], "round-2.png")
        self.assertEqual(s.st["shots"][0]["round"], 2)

    def test_an_app_that_will_not_start_keeps_the_smoke_log_as_a_text_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sandbox, "serve", return_value=(False, "port in use")), \
                mock.patch.object(sandbox, "unserve") as unserve, \
                mock.patch.object(shots, "capture", return_value={"kind": "text", "file": "round-2.txt", "warning": None}):
            s = self.make(tmp)
            s._shot({"ports": [8000]}, "smoke ok")
        unserve.assert_called_once_with("ABC123")
        shot = s.st["shots"][0]
        self.assertEqual(shot["kind"], "text")
        self.assertEqual(shot["file"], "round-2.txt")
        self.assertIn("did not start", shot["warning"])

    def test_an_exception_becomes_a_warning_and_still_stops_the_app(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sandbox, "serve", side_effect=RuntimeError("docker gone")), \
                mock.patch.object(sandbox, "unserve") as unserve:
            s = self.make(tmp)
            s._shot({"ports": [8000]}, "smoke ok")
        unserve.assert_called_once_with("ABC123")
        self.assertIn("docker gone", s.st["shots"][0]["warning"])


if __name__ == "__main__":
    unittest.main()

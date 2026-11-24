import json
import types
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from engine.agents import Agent
from engine.server import Engine, code_id, make_server


def call(port, path, body=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as ex:
        return ex.code, json.loads(ex.read() or b"null")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(agents=[Agent("codex", "Codex", "codex", "codex", "1.0")], tick=False)
        cls.srv = make_server(0, cls.engine)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_health(self):
        code, body = call(self.port, "/api/health")
        self.assertEqual((code, body["ok"], body["version"]), (200, True, "2"))
        self.assertEqual(body["code"], code_id())

    def test_security_log_endpoint(self):
        code, body = call(self.port, "/api/security")
        self.assertEqual(code, 200)
        self.assertIsInstance(body["log"], list)

    def test_answer_without_session_400(self):
        code, _ = call(self.port, "/api/answer", {"agent": "codex", "approve": True})
        self.assertEqual(code, 400)

    def test_agents_listed(self):
        code, body = call(self.port, "/api/agents")
        self.assertEqual((code, [a["id"] for a in body]), (200, ["codex"]))

    def test_decide_without_pause_400(self):
        code, body = call(self.port, "/api/decide", {"choice": "O1"})
        self.assertEqual(code, 400)
        self.assertIn("error", body)

    def test_start_validates_mode(self):
        code, body = call(self.port, "/api/start", {"task": "x", "mode": "sometimes"})
        self.assertEqual(code, 400)
        self.assertIn("mode", body["error"])

    def raw_post(self, path, body, headers):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as ex:
            return ex.code

    def test_rejects_text_plain_post(self):
        self.assertEqual(self.raw_post("/api/control", b'{"action":"stop"}', {"Content-Type": "text/plain"}), 403)

    def test_rejects_foreign_origin(self):
        h = {"Content-Type": "application/json", "Origin": "https://evil.example"}
        self.assertEqual(self.raw_post("/api/keys", b'{"agent":"claude","text":"x"}', h), 403)

    def test_rejects_foreign_host(self):
        h = {"Content-Type": "application/json", "Host": "evil.example:8787"}
        self.assertEqual(self.raw_post("/api/control", b'{"action":"stop"}', h), 403)

    def test_allows_own_origin(self):
        h = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port}
        self.assertEqual(self.raw_post("/api/decide", b'{"choice":"O1"}', h), 400)   # reaches the handler

    def test_restart_without_session_400(self):
        self.assertEqual(call(self.port, "/api/restart", {"agent": "codex"})[0], 400)

    def test_resume_unknown_session_400(self):
        self.assertEqual(call(self.port, "/api/resume", {"id": "nope"})[0], 400)

    def test_resume_clears_pause(self):
        stub = mock.Mock()
        stub.st = {"id": "s1", "status": "stopped", "paused": True}
        old, self.engine.session = self.engine.session, stub
        try:
            self.engine.resume("s1")
        finally:
            self.engine.session = old
        self.assertFalse(stub.st["paused"])
        stub.resume.assert_called_once()

    def test_index_served(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=5) as r:
            self.assertIn(b"<!DOCTYPE html>", r.read()[:200])


if __name__ == "__main__":
    unittest.main()



class ContinueTests(unittest.TestCase):
    def setUp(self):
        import json, tempfile
        from pathlib import Path
        from engine import config
        self.json, self.Path = json, Path
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = mock.patch.dict(config.CFG, {"root": str(self.tmp)})
        self.cfg.start()
        self.tm = mock.patch("engine.server.tmux")
        self.tm.start()

    def tearDown(self):
        self.tm.stop()
        self.cfg.stop()

    def add_run(self, rid, status, exists=True):
        d = self.tmp / ("proj-" + rid) / ".duo" / "1113"
        if exists:
            d.mkdir(parents=True)
            (d / "state.json").write_text(self.json.dumps({"id": rid, "title": "Run " + rid, "created": "2026-11-13T2%s" % rid,
                                                           "status": status, "phase": "scout", "detail": "", "entries": []}))
        idx = self.tmp / ".duo-index.json"
        items = self.json.loads(idx.read_text()) if idx.exists() else []
        items.append({"id": rid, "title": "Run " + rid, "duo_dir": str(d), "created": "2026"})
        idx.write_text(self.json.dumps(items))

    def test_continue_missing_project(self):
        self.add_run("1", "stopped", exists=False)
        eng = Engine(agents=[], tick=False)
        with self.assertRaises(ValueError) as cm:
            eng.continue_run("1")
        self.assertIn("project folder is gone", str(cm.exception))




class FeedbackApiTests(unittest.TestCase):
    def test_feedback_roundtrip_and_apply(self):
        import tempfile
        from pathlib import Path
        from engine import config, personal
        tmp = tempfile.mkdtemp()
        home = Path(tmp) / "Duo"
        from engine import feedback as feedback_mod, userspace
        with mock.patch.dict(config.CFG, {"root": str(Path(tmp) / "DW")}), \
                mock.patch.object(config, "DUO_HOME", home), mock.patch.object(feedback_mod, "spawn_worker"):
            engine = Engine(agents=[], tick=False, home=home)
            srv = make_server(0, engine)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                code, body = call(port, "/api/feedback", {"text": "Fewer rounds please", "kind": "major", "context": {"screen": "timeline"}})
                self.assertEqual(code, 200)
                self.assertEqual(body["reference"]["number"], "1.1.0")
                fid = body["feedback"]["id"]
                reply = json.dumps({"changes": [{"feedback_ids": [fid], "kind": "setting", "path": "max_rounds", "value": 2, "why": "fewer"}]})
                engine.apply_feedback(lambda prompt: reply)
                for _ in range(50):
                    if not engine.applying:
                        break
                    import time
                    time.sleep(0.1)
                code, st = call(port, "/api/state")
                self.assertEqual(st["personal"]["feedback"][0]["status"], "cloned")
                code, vers = call(port, "/api/versions")
                self.assertEqual(code, 200)
                newest = vers["versions"][0]
                self.assertEqual(newest["feedback"], "Fewer rounds please")
                self.assertEqual((newest["number"], newest["base"]), ("1.1.0", "1.0.0"))
                self.assertEqual(vers["number"], "1.0.0")
            finally:
                srv.shutdown()
                config.CFG["max_rounds"] = 4



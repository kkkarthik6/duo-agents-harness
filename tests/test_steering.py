"""Unit tests for steering, the decision clock, the error record and the sandbox value mapping.
No tmux and no agents: each session is a bare object with its state dict filled in by hand."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import config, notify, session


def fake_session(**extra):
    s = session.Session.__new__(session.Session)
    st = {"status": "running", "phase": "decide", "chosen": None, "options": [
        {"id": "O1", "title": "One", "detail": "", "proposer": "a", "impact": "low", "probe": ""},
        {"id": "O2", "title": "Two", "detail": "", "proposer": "b", "impact": "low", "probe": ""}],
        "ranking": ["O1", "O2"], "entries": [], "decisions": [], "detail": "", "error": None}
    st.update(extra)
    s.st = st
    s.saved = 0
    s.save = lambda: setattr(s, "saved", s.saved + 1)
    s.write_options = lambda: None
    s._title = lambda oid: oid
    s._decision_summary = lambda d: "summary"
    s._decision_text = lambda d: "text"
    s._recorded = []

    def add_entry(author, phase, kind, prose, summary="", n=None, **extra):
        e = {"n": len(s.st["entries"]) + 1, "author": author, "phase": phase, "kind": kind, "prose": prose, "summary": summary}
        e.update(extra)
        s.st["entries"].append(e)
        return e
    s.add_entry = add_entry
    s.human_message = lambda text: s.add_entry("human", "decide", "message", text)
    return s


def decision(paused_minutes_ago=None, **extra):
    d = {"id": "D1", "status": "paused", "chosen": None, "ranking": [{"id": "O1"}, {"id": "O2"}],
         "reasons": ["big call"], "titles": {}}
    if paused_minutes_ago is not None:
        d["paused_at"] = time.time() - paused_minutes_ago * 60
    d.update(extra)
    return d


class SandboxValueTests(unittest.TestCase):
    def test_words_and_booleans_map_to_one_yes_or_no(self):
        self.assertIs(session.resolve_sandbox(True), True)
        self.assertIs(session.resolve_sandbox(False), False)
        self.assertIs(session.resolve_sandbox("on"), True)
        self.assertIs(session.resolve_sandbox("off"), False)

    def test_auto_uses_docker_when_it_runs(self):
        with mock.patch.object(session.sandbox, "available", return_value=(True, "")):
            self.assertIs(session.resolve_sandbox("auto"), True)
        with mock.patch.object(session.sandbox, "available", return_value=(False, "no docker")):
            self.assertIs(session.resolve_sandbox("auto"), False)

    def test_unknown_value_is_refused(self):
        with self.assertRaises(ValueError):
            session.resolve_sandbox("maybe")


class GlobalConfigTests(unittest.TestCase):
    def test_save_global_writes_config_json_and_updates_running_config(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(config, "DUO_HOME", Path(tmp)), \
                mock.patch.dict(config.CFG, {"stall_minutes": 5}, clear=False):
            out = config.save_global({"stall_minutes": 9})
            self.assertEqual(out["stall_minutes"], 9)
            self.assertEqual(config.CFG["stall_minutes"], 9)
            self.assertIn('"stall_minutes": 9', (Path(tmp) / "config.json").read_text())

    def test_save_global_keeps_existing_keys_in_the_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DUO_HOME", Path(tmp)), \
                mock.patch.dict(config.CFG, {}, clear=False):
            (Path(tmp) / "config.json").write_text('{"port": 9000}')
            config.save_global({"auto_approve": False})
            text = (Path(tmp) / "config.json").read_text()
            self.assertIn('"port": 9000', text)
            self.assertIn('"auto_approve": false', text)


class SharedConfigTests(unittest.TestCase):
    """Settings live in ~/Duo/config.json, so every version reads the same ones."""

    def dirs(self, tmp):
        home, version = Path(tmp) / "Duo", Path(tmp) / "Duo" / "clones" / "v4"
        version.mkdir(parents=True)
        return home, version

    def test_a_saved_setting_is_read_by_another_version(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(config.CFG, {}, clear=False):
            home, version = self.dirs(tmp)
            with mock.patch.object(config, "DUO_HOME", home), mock.patch.object(config, "APP_DIR", home / "source"):
                config.save_global({"root": str(Path(tmp) / "Projects")})
            with mock.patch.object(config, "DUO_HOME", home), mock.patch.object(config, "APP_DIR", version):
                self.assertEqual(config.load()["root"], str(Path(tmp) / "Projects"))
            self.assertFalse((version / "config.json").exists())

    def test_shared_settings_win_over_a_version_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, version = self.dirs(tmp)
            (version / "config.json").write_text('{"stall_minutes": 3, "port": 9000}')
            (home / "config.json").write_text('{"stall_minutes": 9}')
            with mock.patch.object(config, "DUO_HOME", home), mock.patch.object(config, "APP_DIR", version):
                cfg = config.load()
            self.assertEqual((cfg["stall_minutes"], cfg["port"]), (9, 9000))

    def test_first_save_keeps_the_settings_from_the_version_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(config.CFG, {}, clear=False):
            home, version = self.dirs(tmp)
            (version / "config.json").write_text('{"decision_timeout_min": 30}')
            with mock.patch.object(config, "DUO_HOME", home), mock.patch.object(config, "APP_DIR", version):
                config.save_global({"stall_minutes": 7})
            saved = json.loads((home / "config.json").read_text())
            self.assertEqual(saved, {"decision_timeout_min": 30, "stall_minutes": 7})

    def test_a_broken_shared_file_does_not_stop_duo(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, version = self.dirs(tmp)
            (home / "config.json").write_text("{not json")
            with mock.patch.object(config, "DUO_HOME", home), mock.patch.object(config, "APP_DIR", version):
                self.assertEqual(config.load()["stall_minutes"], config.DEFAULTS["stall_minutes"])


class SteerTests(unittest.TestCase):
    def test_drop_refuses_the_chosen_option(self):
        s = fake_session(chosen="O1")
        with self.assertRaises(ValueError):
            s.steer("drop", "O1")

    def test_drop_refuses_an_unknown_option(self):
        with self.assertRaises(ValueError):
            fake_session().steer("drop", "O9")

    def test_constrain_adds_the_text_and_tells_the_agents(self):
        s = fake_session()
        s.steer("constrain", "  use   python  3.9 ")
        self.assertEqual(s.st["constraints"], ["use python 3.9"])
        self.assertTrue(any("Constraint for the rest of the run: use python 3.9" in e["prose"] for e in s.st["entries"]))

    def test_constrain_refuses_empty_text(self):
        with self.assertRaises(ValueError):
            fake_session().steer("constrain", "   ")

    def test_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            fake_session().steer("delete", None)


class ErrorAndEntriesTests(unittest.TestCase):
    def test_record_error_keeps_the_message_on_the_run(self):
        s = fake_session()
        s.record_error(RuntimeError("tmux went away"))
        self.assertEqual(s.st["error"]["message"], "tmux went away")
        self.assertIn("at", s.st["error"])

    def test_entries_after_returns_only_newer_entries(self):
        s = fake_session(entries=[{"n": 1}, {"n": 2}, {"n": 3}])
        self.assertEqual([e["n"] for e in s.entries_after(1)], [2, 3])
        self.assertEqual([e["n"] for e in s.entries_after(3)], [])
        self.assertEqual(len(s.entries_after(-1)), 3)


if __name__ == "__main__":
    unittest.main()


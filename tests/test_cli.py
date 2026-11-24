import os
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

CLI = SourceFileLoader("duo_cli", str(Path(__file__).resolve().parent.parent / "bin" / "duo")).load_module()


class CliTests(unittest.TestCase):
    def test_ensure_engine_reuses(self):
        spawn = mock.Mock()
        with mock.patch.object(CLI, "engine_code", return_value=CLI.code_id()):
            self.assertTrue(CLI.ensure_engine(8787, spawn=spawn))
        spawn.assert_not_called()

    def test_ensure_engine_spawns(self):
        spawn = mock.Mock()
        with mock.patch.object(CLI, "engine_code", side_effect=[None, None, CLI.code_id()]), mock.patch.object(CLI.time, "sleep"):
            self.assertTrue(CLI.ensure_engine(8787, spawn=spawn))
        spawn.assert_called_once()

    def test_ensure_engine_gives_up(self):
        with mock.patch.object(CLI, "engine_code", return_value=None), mock.patch.object(CLI.time, "sleep"):
            self.assertFalse(CLI.ensure_engine(8787, spawn=mock.Mock(), wait=1))

    def test_outdated_engine_is_replaced(self):
        spawn, stop = mock.Mock(), mock.Mock()
        with mock.patch.object(CLI, "engine_code", side_effect=["old", None, CLI.code_id()]), \
                mock.patch.object(CLI, "stop_engine", stop), mock.patch.object(CLI.time, "sleep"):
            self.assertTrue(CLI.ensure_engine(8787, spawn=spawn))
        stop.assert_called_once_with(8787)
        spawn.assert_called_once()

    def test_stop_engine_falls_back_for_old_engines(self):
        run = mock.Mock()
        with mock.patch.object(CLI, "post_json", side_effect=Exception("404")), \
                mock.patch.object(CLI, "engine_code", side_effect=[""] * 40 + [None]), mock.patch.object(CLI.time, "sleep"):
            CLI.stop_engine(8787, run=run)
        run.assert_called_once_with(CLI.PKILL_ENGINE, capture_output=True)

    def test_pkill_pattern_really_stops_an_engine_process(self):
        import subprocess
        p = subprocess.Popen(["bash", "-c", 'exec -a "python3 -m engine serve 8799" sleep 30'])
        time.sleep(0.3)
        r = subprocess.run(CLI.PKILL_ENGINE, capture_output=True, text=True)
        self.assertEqual(r.stderr, "")
        self.assertIsNotNone(p.wait(timeout=5))

    def test_quit_stops_engine_and_agents(self):
        run = mock.Mock()
        with mock.patch.object(CLI, "post_json", side_effect=Exception("down")), \
                mock.patch.object(CLI, "stop_engine") as stop:
            CLI.quit_all(8787, run=run)
        stop.assert_called_once_with(8787)
        run.assert_any_call(["tmux", "kill-session", "-t", "duo"], capture_output=True)

    def test_open_ui_falls_back_to_browser(self):
        run = mock.Mock()
        CLI.open_ui(8787, run=run, app_paths=[Path("/nonexistent/Duo.app")])
        run.assert_called_once_with(["open", "http://127.0.0.1:8787"])

    def test_open_ui_prefers_app(self):
        app = Path(tempfile.mkdtemp()) / "Duo.app"
        app.mkdir()
        run = mock.Mock()
        CLI.open_ui(8787, run=run, app_paths=[app])
        run.assert_called_once_with(["open", str(app)])

    def test_task_arg_posts_start(self):
        with mock.patch.object(CLI, "ensure_engine", return_value=True), \
                mock.patch.object(CLI, "post_json", return_value={"ok": True}) as post, \
                mock.patch.object(CLI, "open_ui") as ui:
            self.assertEqual(CLI.main(["rename", "my", "photos"]), 0)
        post.assert_called_once_with(8787, "/api/start", {"task": "rename my photos"})
        ui.assert_called_once()

    def test_engine_env_adds_tool_dirs(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": "/Users/x"}), \
                mock.patch.object(CLI, "login_path", return_value="/custom/bin"):
            path = CLI.engine_env()["PATH"].split(":")
        for d in ("/custom/bin", "/opt/homebrew/bin", "/usr/local/bin", "/Users/x/.local/bin", "/usr/bin"):
            self.assertIn(d, path)
        self.assertEqual(len(path), len(set(path)))


if __name__ == "__main__":
    unittest.main()


class SealedCoreCliTests(unittest.TestCase):
    def test_update_without_a_source_says_how(self):
        out = []
        with mock.patch.object(CLI, "say", out.append):
            self.assertEqual(CLI.update([]), 1)
        self.assertIn("--from", out[0])

    def test_version_before_setup_says_so(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(CLI, "DUO_HOME", Path(d)):
            self.assertIn("not set up", CLI.version())

    def test_install_helper_installs_core_and_a_legacy_number(self):
        import json
        import subprocess
        import sys
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "Duo"
            home.mkdir()
            (home / "versions.json").write_text(json.dumps({"current": "source", "versions": [{"id": "source", "number": "3.0.0", "path": "x"}]}))
            r = subprocess.run([sys.executable, str(root / "scripts" / "install_core.py"), str(root), str(home)],
                               capture_output=True, text=True, timeout=100)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("Your Duo is 3.0.0", r.stdout)
            state = json.loads((home / "core" / "state.json").read_text())
            self.assertTrue((home / "core" / state["current"] / "bin" / "duo").exists())
            self.assertFalse((home / "core" / state["current"] / "config.json").exists())

    def test_install_helper_moves_source_settings_to_the_shared_file_once(self):
        import shutil
        import subprocess
        import sys
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            shutil.copytree(root, src, ignore=shutil.ignore_patterns("app", ".git", "__pycache__", "config.json"))
            (src / "config.json").write_text('{"stall_minutes": 9}')
            home = Path(d) / "Duo"
            r = subprocess.run([sys.executable, str(src / "scripts" / "install_core.py"), str(src), str(home)],
                               capture_output=True, text=True, timeout=100)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((home / "config.json").read_text(), '{"stall_minutes": 9}')

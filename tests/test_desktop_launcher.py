import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import desktop_launcher, update_desktop_launcher


class DesktopBridgeTests(unittest.TestCase):
    def test_bridge_selects_current_core_without_loading_old_bundled_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "core/1.8.0/bin").mkdir(parents=True)
            (home / "core/1.8.0/bin/duo").write_text("new launcher")
            (home / "core/state.json").write_text('{"current":"1.8.0"}')
            with mock.patch.dict(os.environ, {"DUO_HOME": folder}), mock.patch.object(desktop_launcher.os, "execv") as execute, \
                    mock.patch("sys.argv", ["duo", "serve-bg"]):
                desktop_launcher.main()
            self.assertEqual(execute.call_args.args[1][-2:], [str(home / "core/1.8.0/bin/duo"), "serve-bg"])

    def test_bridge_rejects_core_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "core").mkdir()
            (home / "core/state.json").write_text('{"current":"../../other"}')
            with mock.patch.dict(os.environ, {"DUO_HOME": folder}), mock.patch.object(desktop_launcher.os, "execv") as execute:
                with self.assertRaises(SystemExit):
                    desktop_launcher.main()
            execute.assert_not_called()

    def test_update_preserves_original_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = Path(__file__).resolve().parents[1]
            app = root / "Duo.app"
            target = app / "Contents/Resources/_duo/bin/duo"
            target.parent.mkdir(parents=True)
            target.write_text("old source launcher")
            with mock.patch("builtins.print"):
                self.assertEqual(update_desktop_launcher.update(root / "home", source, [app], sealed=lambda a: False), [str(app)])
                self.assertEqual(update_desktop_launcher.update(root / "home", source, [app], sealed=lambda a: False), [])
            self.assertEqual(target.read_text(), (source / "scripts/desktop_launcher.py").read_text())
            backups = list((root / "home/backups/desktop-launchers").rglob("duo-*.py"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "old source launcher")

    def test_signed_resources_are_not_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = root / "Duo.app"
            target = app / "Contents/Resources/_duo/bin/duo"
            target.parent.mkdir(parents=True)
            target.write_text("signed launcher")
            with mock.patch("builtins.print"):
                self.assertEqual(update_desktop_launcher.update(root / "home", Path(__file__).resolve().parents[1], [app], sealed=lambda a: True), [])
            self.assertEqual(target.read_text(), "signed launcher")

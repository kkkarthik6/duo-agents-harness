"""Folder trust must never corrupt an agent's config, and a corrupt Codex config must be found and repairable."""
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from engine import codex_config, trust

ROOT = Path(__file__).resolve().parent.parent


class TrustWriteTests(unittest.TestCase):
    def home(self):
        tmp = Path(tempfile.mkdtemp())
        return tmp, mock.patch.dict(os.environ, {"CODEX_HOME": str(tmp), "HOME": str(tmp)})

    def test_a_folder_is_added_once_and_the_file_still_loads(self):
        tmp, env = self.home()
        (tmp / "config.toml").write_text('model = "x"\n')
        with env:
            self.assertTrue(trust.trust_codex(Path("/work/a")))
            self.assertTrue(trust.trust_codex(Path("/work/a")))
        data = tomllib.loads((tmp / "config.toml").read_text())
        self.assertEqual(list(data["projects"]), ["/work/a"])
        self.assertEqual(data["model"], "x")

    def test_a_config_codex_cannot_load_is_left_alone(self):
        tmp, env = self.home()
        broken = 'model = "x"\nlo"]\n'
        (tmp / "config.toml").write_text(broken)
        with env:
            self.assertFalse(trust.trust_codex(Path("/work/a")))
        self.assertEqual((tmp / "config.toml").read_text(), broken)

    def test_many_processes_trusting_folders_at_once_never_corrupt_or_lose_entries(self):
        tmp, env = self.home()
        (tmp / "config.toml").write_text('model = "x"\n')
        code = ("import sys; sys.path.insert(0, %r); from pathlib import Path; from engine import trust; "
                "trust.trust_codex(Path('/work/p' + sys.argv[1]))" % str(ROOT))
        procs = [subprocess.Popen([sys.executable, "-c", code, str(i)], env=dict(os.environ, CODEX_HOME=str(tmp), HOME=str(tmp)))
                 for i in range(24)]
        for p in procs:
            p.wait()
        data = tomllib.loads((tmp / "config.toml").read_text())        # parses: nothing half-written
        self.assertEqual(len(data["projects"]), 24)                    # and no entry was overwritten by another process
        self.assertEqual(list(tmp.glob("*.tmp")), [])

    def test_a_claude_config_that_is_not_json_is_left_alone(self):
        tmp, env = self.home()
        (tmp / ".claude.json").write_text("{broken")
        with env:
            self.assertFalse(trust.trust_claude(Path("/work/a")))
        self.assertEqual((tmp / ".claude.json").read_text(), "{broken")


class RepairTests(unittest.TestCase):
    def broken(self):
        tmp = Path(tempfile.mkdtemp())
        real = "/Users/me/real-project"
        gone = "/private/var/folders/xx/yy/T/tmpab12cd34/Duo Workouts/old-test"
        cfg = tmp / "config.toml"
        cfg.write_text('model = "gpt-x"\n\n[projects."%s"]\ntrust_level = "trusted"\nlo"]\n\n[projects."%s"]\ntrust_level = "trusted"\n\n'
                       '[projects."/Users/me/keep"]\ntrust_level = "trusted"\n' % (real, gone))
        return tmp, cfg, real

    def test_a_broken_config_is_reported(self):
        _, cfg, _ = self.broken()
        self.assertIn("does not load", codex_config.check(cfg))

    def test_a_good_or_missing_config_is_not_reported(self):
        tmp, cfg, _ = self.broken()
        self.assertIsNone(codex_config.check(tmp / "missing.toml"))
        cfg.write_text('model = "x"\n')
        self.assertIsNone(codex_config.check(cfg))

    def test_by_default_it_only_reports_and_changes_nothing(self):
        _, cfg, _ = self.broken()
        before = cfg.read_text()
        report = codex_config.repair(cfg)
        self.assertEqual((report["removed_test_folders"], len(report["removed_fragments"]), report["applied"]), (1, 1, False))
        self.assertEqual(cfg.read_text(), before)

    def test_applying_keeps_real_settings_and_projects_and_writes_a_backup(self):
        tmp, cfg, real = self.broken()
        original = cfg.read_text()
        report = codex_config.repair(cfg, apply=True, backup_dir=tmp / "bak")
        data = tomllib.loads(cfg.read_text())
        self.assertEqual(data["model"], "gpt-x")
        self.assertEqual(set(data["projects"]), {real, "/Users/me/keep"})
        self.assertEqual(Path(report["backup"]).read_text(), original)
        self.assertIsNone(codex_config.check(cfg))

    def test_a_test_folder_goes_even_while_it_still_exists_but_a_real_project_in_tmp_stays(self):
        tmp = Path(tempfile.mkdtemp())
        mkdtemp_folder = Path(tempfile.mkdtemp())                        # tmpXXXXXXXX inside the system temp dir
        cfg = tmp / "config.toml"
        cfg.write_text('[projects."%s"]\ntrust_level = "trusted"\n\n[projects."/tmp/my-real-project"]\ntrust_level = "trusted"\n'
                       '\n[projects."/Users/me/work/tmpabcdefgh"]\ntrust_level = "trusted"\n' % mkdtemp_folder)
        report = codex_config.repair(cfg)
        self.assertEqual((report["removed_test_folders"], report["projects_kept"]), (1, 2))


class TestsStayAwayFromTheRealHomeTests(unittest.TestCase):
    def test_the_test_process_uses_a_throwaway_home(self):
        real = Path("/Users") / os.environ.get("USER", "nobody")
        self.assertNotEqual(Path(os.environ["HOME"]), real)
        self.assertTrue(os.environ["CODEX_HOME"].startswith(os.environ["HOME"]))
        self.assertTrue(os.environ["DUO_HOME"].startswith(os.environ["HOME"]))


if __name__ == "__main__":
    unittest.main()

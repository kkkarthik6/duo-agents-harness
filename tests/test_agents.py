import subprocess
import unittest
from pathlib import Path

from engine import agents, config


def fake_which(installed):
    return lambda name: "/bin/" + name if name in installed else None


def fake_run(fail=()):
    def run(cmd, **kw):
        code = 1 if cmd[0].split("/")[-1] in fail else 0
        return subprocess.CompletedProcess(cmd, code, stdout="%s 1.0\nextra" % cmd[0], stderr="")
    return run


class AgentTests(unittest.TestCase):
    def test_detects_installed_only(self):
        found = agents.detect(which=fake_which({"claude", "agy"}), run=fake_run())
        self.assertEqual([a.id for a in found], ["claude", "agy"])
        self.assertEqual(found[0].version, "/bin/claude 1.0")

    def test_version_failure_excluded(self):
        found = agents.detect(which=fake_which({"claude", "codex"}), run=fake_run(fail={"codex"}))
        self.assertEqual([a.id for a in found], ["claude"])

    def test_single_agent(self):
        self.assertEqual(len(agents.detect(which=fake_which({"codex"}), run=fake_run())), 1)

    def test_launch_commands_block_self_update(self):
        a = {x.id: x for x in agents.detect(which=fake_which({"claude", "codex"}), run=fake_run())}
        self.assertIn("DISABLE_AUTOUPDATER=1", a["claude"].cmd_auto)
        self.assertNotIn("--settings", a["claude"].cmd_auto)
        self.assertIn("--permission-mode auto", a["claude"].cmd_auto)
        self.assertIn("check_for_update_on_startup=false", a["codex"].cmd_auto)
        self.assertIn("check_for_update_on_startup=false", a["codex"].cmd_manual)
        self.assertIn("disable_paste_burst=true", a["codex"].cmd_auto)

    def test_config_override(self):
        config.CFG["agents"] = {"codex": {"auto": "codex --x"}}
        try:
            found = agents.detect(which=fake_which({"codex"}), run=fake_run())
            self.assertIn("--x", found[0].cmd_auto)
            self.assertIn("--no-daemon", found[0].cmd_auto)
            self.assertIn("CODEX_HOME=", found[0].cmd_auto)
        finally:
            config.CFG["agents"] = {}


if __name__ == "__main__":
    unittest.main()


class OtherAgentTests(unittest.TestCase):
    CATALOG = {"hermes": {"label": "Hermes", "bins": ["hermes"], "dirs": [".hermes"]},
               "gemini": {"label": "Gemini CLI", "bins": ["gemini"], "dirs": []},
               "aider": {"label": "Aider", "bins": ["aider"], "dirs": []}}

    def test_installed_other_clis_are_listed_and_never_launchable(self):
        import tempfile
        found = agents.detect_other(which=fake_which({"hermes", "gemini", "claude"}), run=fake_run(),
                                    catalog=self.CATALOG, home=tempfile.mkdtemp())
        self.assertEqual([a["id"] for a in found], ["hermes", "gemini"])
        self.assertTrue(all(a["launchable"] is False for a in found))
        self.assertEqual(found[0]["found_by"], "path")

    def test_a_cli_with_its_folder_but_not_on_path_is_still_found(self):
        import tempfile
        home = tempfile.mkdtemp()
        (Path(home) / ".hermes").mkdir()
        found = agents.detect_other(which=fake_which(set()), run=fake_run(), catalog=self.CATALOG, home=home)
        self.assertEqual(found[0]["found_by"], "folder")
        self.assertEqual(found[0]["version"], "")

    def test_known_agents_are_not_repeated_as_other(self):
        import tempfile
        catalog = dict(self.CATALOG, claude={"label": "Claude", "bins": ["claude"], "dirs": []})
        found = agents.detect_other(which=fake_which({"claude"}), run=fake_run(), catalog=catalog, home=tempfile.mkdtemp())
        self.assertEqual(found, [])

    def test_a_version_failure_still_lists_the_cli_without_a_version(self):
        import tempfile
        found = agents.detect_other(which=fake_which({"aider"}), run=fake_run(fail={"aider"}), catalog=self.CATALOG,
                                    home=tempfile.mkdtemp())
        self.assertEqual(found[0]["version"], "")

    def test_the_shipped_catalog_has_a_label_and_bins_for_each_entry(self):
        for aid, spec in agents.OTHER_AGENTS.items():
            self.assertTrue(spec["label"] and spec["bins"], aid)

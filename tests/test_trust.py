import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"HOME": self.tmp}, clear=False)
        self.env.start()
        os.environ.pop("CODEX_HOME", None)
        os.environ.pop("CLAUDE_CONFIG_DIR", None)
        from engine import config, trust
        config.CFG["root"] = str(Path(self.tmp) / "Duo Workouts")
        config.CFG["auto_trust"] = True
        self.trust = trust
        self.proj = Path(self.tmp) / "Duo Workouts" / "p"
        self.proj.mkdir(parents=True)

    def tearDown(self):
        self.env.stop()

    def test_trust_codex_appends_once(self):
        self.trust.trust_folder(self.proj)
        self.trust.trust_folder(self.proj)
        text = (Path(self.tmp) / ".codex" / "config.toml").read_text()
        self.assertEqual(text.count('[projects."%s"]' % self.proj.resolve()), 1)

    def test_trust_codex_skips_inline_table(self):
        cfg = Path(self.tmp) / ".codex" / "config.toml"
        cfg.parent.mkdir()
        cfg.write_text("projects = { }\n")
        self.assertFalse(self.trust.trust_codex(self.proj))
        self.assertEqual(cfg.read_text(), "projects = { }\n")

    def test_trust_claude_keeps_other_keys(self):
        p = Path(self.tmp) / ".claude.json"
        p.write_text(json.dumps({"numStartups": 3}))
        self.trust.trust_folder(self.proj)
        data = json.loads(p.read_text())
        self.assertEqual(data["numStartups"], 3)
        self.assertIs(data["projects"][str(self.proj.resolve())]["hasTrustDialogAccepted"], True)

    def test_unique_project_dir(self):
        (Path(self.tmp) / "Duo Workouts" / "Todo").mkdir()
        self.assertEqual(self.trust.unique_project_dir("Todo").name, "Todo-2")


if __name__ == "__main__":
    unittest.main()

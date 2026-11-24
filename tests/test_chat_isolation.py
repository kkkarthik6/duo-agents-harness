"""Duo launches must not use the personal Codex server or session folder."""
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import agents, helper, personal, review


class ChatIsolationTests(unittest.TestCase):
    def test_defaults_and_overrides_use_private_home_without_shared_server(self):
        for cmd in (agents.AGENT_SPECS["codex"]["auto"],
                    agents.AGENT_SPECS["codex"]["manual"],
                    "codex -m example", "CODEX_HOME=/wrong codex -m example"):
            words = shlex.split(agents.launch_command("codex", cmd))
            self.assertIn("CODEX_HOME=" + str(agents.CODEX_HOME), words)
            self.assertNotIn("CODEX_HOME=/wrong", words)
            self.assertEqual(words.count("--no-daemon"), 1)

    def test_private_home_with_spaces_survives_shell_quoting(self):
        with mock.patch.object(agents, "CODEX_HOME", Path("/a folder/codex-home")):
            words = shlex.split(agents.launch_command("codex", "codex -m example"))
        self.assertEqual(words[0], "CODEX_HOME=/a folder/codex-home")

    def test_headless_calls_do_not_depend_on_inherited_home(self):
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(agents, "CODEX_HOME", Path(folder) / "codex-home"), \
                mock.patch.dict(os.environ, {"CODEX_HOME": "/personal"}), \
                mock.patch("shutil.which", return_value="/bin/codex"), \
                mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "REVIEW: approve", "")
            helper.run_cli("codex", folder, "question")
            review.codex_review(folder, "request", "diff", [])
            with mock.patch.object(personal.shutil, "which", side_effect=lambda a: a if a == "codex" else None):
                personal.cli_runner("feedback")
            self.assertEqual(run.call_count, 3)
            for call in run.call_args_list:
                self.assertIn("--no-daemon", call.args[0])
                self.assertIn("--ephemeral", call.args[0])
                self.assertEqual(call.kwargs["env"]["CODEX_HOME"], str(Path(folder) / "codex-home"))

    def test_other_agent_commands_are_unchanged(self):
        self.assertEqual(agents.launch_command("claude", "claude --example"), "claude --example")

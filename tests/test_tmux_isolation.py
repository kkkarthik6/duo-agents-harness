"""Tests must never share a tmux server with the user's real sessions."""
import os
import unittest
from unittest import mock

from engine import tmux as tmuxmod, jobs


class TmuxSocketTests(unittest.TestCase):
    def run_with(self, env):
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("engine.tmux.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            tmuxmod.tmux("list-sessions", check=False)
            return run.call_args[0][0]

    def test_a_private_socket_is_used_when_requested(self):
        self.assertEqual(self.run_with({"DUO_TMUX_SOCKET": "duo-test-1"})[:3], ["tmux", "-L", "duo-test-1"])

    def test_the_default_server_is_used_otherwise(self):
        env = dict(os.environ)
        env.pop("DUO_TMUX_SOCKET", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("engine.tmux.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            tmuxmod.tmux("list-sessions", check=False)
        self.assertEqual(run.call_args[0][0], ["tmux", "list-sessions"])

    def test_the_test_suite_runs_on_a_private_socket(self):
        self.assertTrue(os.environ.get("DUO_TMUX_SOCKET", "").startswith("duo-test-"))

    def test_job_polling_uses_the_same_socket(self):
        with mock.patch.dict(os.environ, {"DUO_TMUX_SOCKET": "duo-test-2"}), \
             mock.patch("engine.jobs.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="")
            try:
                jobs.pane_pid("%1")
            except Exception:
                pass
            self.assertEqual(run.call_args[0][0][:3], ["tmux", "-L", "duo-test-2"])


if __name__ == "__main__":
    unittest.main()

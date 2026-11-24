"""A tmux server started by another process must not decide where an agent looks for its login."""
import os
import unittest
from unittest import mock

from engine import tmux


class PaneEnvTests(unittest.TestCase):
    def test_every_pane_is_started_with_the_real_home(self):
        with mock.patch.dict(os.environ, {"HOME": "/Users/someone"}):
            self.assertEqual(tmux.pane_env(), ["-e", "HOME=/Users/someone"])

    def test_no_home_means_no_override(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tmux.pane_env(), [])

    def test_a_pane_gets_the_home_even_when_the_tmux_server_has_another(self):
        import subprocess
        sock = "duo-test-env-%d" % os.getpid()
        base = ["tmux", "-L", sock]
        try:
            subprocess.run(base + ["new-session", "-d", "-s", "x", "sleep 30"], env=dict(os.environ, HOME="/tmp/not-your-home"), check=True)
            with mock.patch.dict(os.environ, {"HOME": "/Users/someone", "DUO_TMUX_SOCKET": sock}):
                out = tmux.tmux("new-session", *tmux.pane_env(), "-d", "-s", "y", "-P", "-F", "#{pane_id}", "echo $HOME > /tmp/duo-pane-home; sleep 1").strip()
                self.assertTrue(out.startswith("%"))
            import time
            time.sleep(1.5)
            self.assertEqual(open("/tmp/duo-pane-home").read().strip(), "/Users/someone")
        finally:
            subprocess.run(base + ["kill-server"], capture_output=True)
            if os.path.exists("/tmp/duo-pane-home"):
                os.remove("/tmp/duo-pane-home")


if __name__ == "__main__":
    unittest.main()

"""Shared fixture for tests that run real tmux sessions with fake agents."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import tmux as tmuxmod
from engine import config
from engine.agents import Agent

FAKE = Path(__file__).resolve().parent.parent / "fake_agent.py"


def fake(aid, *flags):
    cmd = " ".join(shlex.quote(x) for x in [sys.executable, str(FAKE), aid] + list(flags))
    return Agent(aid, aid.title(), cmd, cmd, "test")


class SessionBase(unittest.TestCase):
    """Shared setup; holds no tests so subclasses do not re-run the flow tests."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = mock.patch.dict(os.environ, {"HOME": self.tmp})
        self.env.start()
        self.duo_home = mock.patch.object(config, 'DUO_HOME', Path(self.tmp) / 'Duo')
        self.duo_home.start()
        self.cfg = mock.patch.dict(config.CFG, {"root": str(Path(self.tmp) / "Duo Workouts"),
                                                "tmux_session": "duotest%d" % os.getpid(),
                                                "boot_seconds": 1, "submit_delay": 0.1, "fast_close": False, "triage": "full",
                                                "auto_completion_summary": False})
        self.cfg.start()
        self.guard = mock.patch("engine.agents.guarded_command", side_effect=lambda a, cmd, *args, **kw: cmd)
        self.guard.start()
        from engine import session
        self.session = session

    def tearDown(self):
        from engine import session, workers
        for item in session.list_sessions(all_projects=False):
            saved=session.find(item['id'],all_projects=False)
            if saved:
                workers.close(saved.st)
        subprocess.run(tmuxmod.command("kill-session", "-t", config.CFG["tmux_session"]), capture_output=True)
        self.cfg.stop()
        self.duo_home.stop()
        self.guard.stop()
        self.env.stop()

    def run_until(self, s, cond, timeout=40):
        end = time.time() + timeout
        while time.time() < end:
            s.tick()
            if cond(s):
                return True
            time.sleep(0.25)
        self.fail("timed out; status=%s detail=%s phase=%s" % (s.st["status"], s.st["detail"], s.st["phase"]))

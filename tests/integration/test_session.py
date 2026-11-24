"""Real-tmux integration tests. Run on demand: python3.13 -m unittest discover -s tests/integration -t ."""
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import tmux as tmuxmod, config
from tests.helpers.session_base import SessionBase, fake


class SessionTests(SessionBase):
    def test_full_programming_session_reaches_done(self):
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex")], mode="auto")
        self.run_until(s, lambda s: s.st["status"] == "done")
        phases = [e["phase"] for e in s.st["entries"] if e["author"] not in ("human", "system")]
        order = [p for i, p in enumerate(phases) if i == 0 or phases[i - 1] != p]
        self.assertEqual(order[:2], ["frame", "deep"])
        self.assertTrue(s.st["verify_passed"])

    def test_login_prompt_blocks(self):
        s = self.session.Session.create("Say hi", [fake("claude", "--login"), fake("codex")], mode="auto")
        self.run_until(s, lambda s: s.st["status"] == "waiting", timeout=15)
        self.assertEqual(s.st["detail"], "Sign in to %s in its pane" % s.label("claude"))
        self.assertEqual(s.st["pending"], [])

    def kill_agent(self, s, agent):
        subprocess.run(tmuxmod.command("send-keys", "-t", s.st["panes"][agent], "C-c"), capture_output=True)
        time.sleep(1)

    def test_dead_agent_restarts_automatically(self):
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex")], mode="auto")
        self.run_until(s, lambda s: s.st["pending"])
        self.kill_agent(s, s.st["pending"][0]["agent"])
        self.run_until(s, lambda s: s.st["status"] == "done", timeout=90)
        self.assertTrue(s.st["verify_passed"])

    def test_stopped_session_resumes_and_finishes(self):
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex")], mode="auto")
        self.run_until(s, lambda s: s.st["phase"] == "frame")
        s.control("close")
        self.assertEqual(s.st["status"], "stopped")
        s.resume()
        self.assertIn(s.st["status"], ("booting", "running"))
        self.run_until(s, lambda s: s.st["status"] == "done", timeout=60)
        rejoin = [p for p in Path(s.st["duo_dir"]).glob("replies/*")]
        self.assertTrue(rejoin)

    def test_pause_then_resume_continues(self):
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex")], mode="auto")
        self.run_until(s, lambda s: s.st["phase"] == "frame")
        s.control("pause")
        self.assertTrue(s.st["paused"])
        phase = s.st["phase"]
        for _ in range(8):
            s.tick()
            time.sleep(0.25)
        self.assertEqual(s.st["phase"], phase)
        s.control("resume")
        self.run_until(s, lambda s: s.st["status"] == "done", timeout=150)




class WatchTests(SessionBase):
    """Agents asking for approval, stalls, and the security guard on commands."""

    def setUp(self):
        super().setUp()
        from engine import notify
        self.notify = notify
        notify.SENT.clear()
        notify.ENABLED = False                        # record only; no real notifications in tests
        self.aa = mock.patch.dict(config.CFG, {"auto_approve": False})   # these tests cover the manual path
        self.aa.start()
        self.addCleanup(self.aa.stop)

    def run_waiting(self, s):
        self.run_until(s, lambda s: any(p.get("waiting") for p in s.st["pending"]), timeout=30)
        return next(p["waiting"] for p in s.st["pending"] if p.get("waiting"))

    def test_approval_waits_and_notifies(self):
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex", "--ask", "ls -la")], mode="auto")
        w = self.run_waiting(s)
        self.assertEqual((w["kind"], w["verdict"]), ("approval", "review"))
        self.assertTrue(any(s.label("codex") in x for _, x in self.notify.SENT))
        s.answer_wait("codex", True)
        self.run_until(s, lambda s: not any(p.get("waiting") for p in s.st["pending"]), timeout=30)

    def test_native_approval_is_not_auto_answered(self):
        config.CFG["auto_approve"] = True
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex", "--ask", "ls -la")], mode="auto")
        w = self.run_waiting(s)
        self.assertEqual(w["category"], "native")
        self.assertEqual(s.st["approval_counts"]["auto"], 0)
        s.answer_wait("codex", True)
        self.run_until(s, lambda s: not any(p.get("waiting") for p in s.st["pending"]), timeout=30)

    def test_stall_notifies_once(self):
        config.CFG["stall_minutes"] = 0.03
        s = self.session.Session.create("Build a tiny tool that prints hello", [fake("claude", "--hang"), fake("codex")], mode="auto")
        self.run_until(s, lambda s: any((p.get("waiting") or {}).get("kind") == "stall" for p in s.st["pending"]), timeout=30)
        for _ in range(12):
            s.tick()
            time.sleep(0.25)
        self.assertEqual(sum("seems stuck" in x for _, x in self.notify.SENT), 1)


for _name in [n for n in dir(SessionTests) if n.startswith("test_")]:
    if _name not in WatchTests.__dict__:
        setattr(WatchTests, _name, None)            # inherited tests run once, in SessionTests


class SettingsTests(SessionBase):
    """Run settings that change mid-run: mode, rounds, roles, sandbox and approvals."""

    def make(self):
        return self.session.Session.create("Build a tiny tool that prints hello", [fake("claude"), fake("codex")], mode="auto")

    def test_manual_approvals_rewrites_commands_and_relaunches_agents(self):
        from engine.agents import AGENT_SPECS
        s = self.make()
        with mock.patch.object(self.session.Session, "restart_agent") as restart:
            result = s.update_settings({"approvals": "manual"})
        self.assertTrue(result["relaunched"])
        self.assertEqual(s.st["approvals"], "manual")
        self.assertEqual(s.st["agent_info"]["claude"]["cmd"], AGENT_SPECS["claude"]["manual"])
        self.assertEqual(s.st["agent_info"]["codex"]["cmd"], AGENT_SPECS["codex"]["manual"])
        self.assertEqual(restart.call_count, 2)
        restart.assert_has_calls([mock.call("claude"), mock.call("codex")], any_order=True)

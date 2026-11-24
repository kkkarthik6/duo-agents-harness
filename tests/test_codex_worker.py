import contextlib
import io
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import agents, codex_worker


class EphemeralWorkerTests(unittest.TestCase):
    def test_interactive_launches_use_worker_and_headless_calls_use_ephemeral(self):
        with tempfile.TemporaryDirectory() as folder:
            for original in ("codex", "codex --sandbox workspace-write", agents.AGENT_SPECS["codex"]["auto"]):
                cmd = shlex.split(agents.guarded_command("codex", original, folder))
                self.assertTrue(any(x.endswith("/codex_worker.py") for x in cmd))
                self.assertIn('history.persistence="none"', cmd)
            cmd = agents.guarded_argv("codex", ["codex", "exec", "prompt"], folder)
            self.assertEqual(cmd[0], "codex")
            self.assertIn("--ephemeral", cmd)
            self.assertNotIn("codex_worker.py", cmd)
            for original in ("codex resume --last", "codex --remote ws://host", "codex exec --worktree prompt",
                             "codex exec --ephemeral resume --last", "codex exec --sandbox read-only fork old"):
                with self.assertRaises(ValueError):
                    agents.guarded_command("codex", original, folder)

    def test_each_turn_is_new_ephemeral_exec_with_memory_context(self):
        worker = codex_worker.Worker(["codex", "--no-daemon"])
        worker.model = "example-model"
        worker.history.append(("build T1", "output: report.html"))
        cmd = worker.argv()
        self.assertIn("--ephemeral", cmd)
        self.assertIn("exec", cmd)
        self.assertNotIn("resume", cmd)
        self.assertIn("example-model", cmd)
        prompt = worker.context("review T1")
        self.assertIn("build T1", prompt)
        self.assertIn("output: report.html", prompt)
        self.assertTrue(prompt.endswith("review T1"))

    def test_context_preserves_all_history_and_current_assignment(self):
        worker = codex_worker.Worker(["codex"])
        worker.history = [("old", "x" * codex_worker.MAX_CONTEXT), ("recent", "saved result")]
        text = worker.context("continue T1")
        self.assertIn("Previous assignment:\nold", text)
        self.assertIn("recent", text)
        self.assertEqual(len(worker.history), 2)

    def test_terminal_worker_runs_multiple_turns_without_session_resume(self):
        # Use an executable fixture to exercise real child I/O, not a mocked Popen.
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / "codex"
            fake.write_text('#!' + sys.executable + '\n' +
                'import sys,json\n'
                'assert "--ephemeral" in sys.argv and "resume" not in sys.argv\n'
                'p=sys.stdin.read()\n'
                'answer="SECOND_OK" if "FIRST_OK" in p else "FIRST_OK"\n'
                'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":answer}}))\n')
            fake.chmod(0o700)
            result = subprocess.run([sys.executable, str(Path(codex_worker.__file__)), "--", str(fake)],
                                    input="first\nsecond\n/exit\n", capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("FIRST_OK", result.stdout)
            self.assertIn("SECOND_OK", result.stdout)
            self.assertEqual(result.stdout.count("codex>"), 3)

    def test_interruption_stops_child_before_returning_to_prompt(self):
        child = mock.Mock()
        child.stdout.__iter__ = mock.Mock(side_effect=KeyboardInterrupt)
        child.stdin.closed = False
        child.poll.return_value = None
        worker = codex_worker.Worker(["codex"])
        with mock.patch.object(codex_worker.subprocess, "Popen", return_value=child), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                worker.turn("build")
        child.send_signal.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)
        self.assertFalse(worker.history)

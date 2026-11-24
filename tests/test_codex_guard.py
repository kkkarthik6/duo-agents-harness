"""Duo must never leave its prompts in the user's Codex history."""
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from engine import cli_adapters, codex_guard, helper

ENGINE = Path(__file__).resolve().parent.parent / "engine"


class EveryCodexCallIsEphemeralTests(unittest.TestCase):
    def test_the_adapter_builds_an_ephemeral_exec(self):
        argv = cli_adapters.get("codex").argv(["codex"], "m", "prompt")
        self.assertIn("exec", argv)
        self.assertIn("--ephemeral", argv)

    def test_the_helper_calls_are_ephemeral(self):
        self.assertIn("--ephemeral", helper.CLI["codex"]("question"))

    def test_no_source_line_runs_codex_exec_without_ephemeral(self):
        bad = []
        for path in sorted(ENGINE.glob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                code = line.split("#", 1)[0]
                if re.search(r"""["']exec["']""", code) and re.search(r"""["']codex["']|\bexe\b""", code) and "--ephemeral" not in code:
                    nearby = "\n".join(path.read_text().splitlines()[max(0, number - 3):number + 3])
                    if "--ephemeral" not in nearby:
                        bad.append("%s:%d %s" % (path.name, number, line.strip()))
        self.assertEqual(bad, [])

    def test_a_saved_thread_or_remote_launch_is_refused(self):
        from engine import agents
        for command in ("codex resume --last", "codex fork abc", "codex --remote ws://x", "codex review"):
            with self.assertRaises(ValueError, msg=command):
                agents.guarded_command("codex", command, "/tmp")

    def test_a_normal_codex_launch_never_saves_history_or_a_session(self):
        from engine import agents
        launched = agents.guarded_command("codex", "codex --approve-for-me", "/tmp")
        self.assertIn("codex_worker.py", launched)               # per-assignment ephemeral exec in a worker
        self.assertIn("history.persistence", launched)
        direct = agents.guarded_command("codex", "codex exec --json", "/tmp")
        self.assertIn("--ephemeral", direct)


class LeakDetectionTests(unittest.TestCase):
    def make(self, text, age=0):
        root = Path(tempfile.mkdtemp())
        now = time.time()
        day = time.localtime(now - age)
        folder = root / ("%04d" % day.tm_year) / ("%02d" % day.tm_mon) / ("%02d" % day.tm_mday)
        folder.mkdir(parents=True)
        path = folder / "rollout-2026-11-23T10-00-00-abc.jsonl"
        path.write_text(json.dumps({"payload": {"cwd": "/x"}}) + "\n" + text + "\n")
        return root, path, now

    def test_a_saved_session_that_starts_with_a_duo_prompt_is_found(self):
        root, path, now = self.make("DUO assignment: read /x/a.md and complete the assigned task.")
        self.assertEqual(codex_guard.leaked(now - 60, root=root, now=now + 1), [str(path)])

    def test_the_users_own_codex_sessions_are_left_alone(self):
        root, _, now = self.make("Can you review my options monitor and fix the chart?")
        self.assertEqual(codex_guard.leaked(now - 60, root=root, now=now + 1), [])

    def test_a_session_from_before_the_run_is_not_blamed_on_it(self):
        root, path, now = self.make("DUO turn #3: you are builder")
        import os
        os.utime(path, (now - 3600, now - 3600))
        self.assertEqual(codex_guard.leaked(now - 60, root=root, now=now), [])

    def test_a_missing_sessions_folder_is_fine(self):
        self.assertEqual(codex_guard.leaked(time.time() - 60, root=Path(tempfile.mkdtemp()) / "none"), [])


if __name__ == "__main__":
    unittest.main()

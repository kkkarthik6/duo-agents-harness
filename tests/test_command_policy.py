import json
import shlex
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from engine import agents, command_policy as policy, guard_hook


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="duo-guard-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def decision(self, command, container=None):
        return policy.classify(command, str(self.root), container)[0]

    def test_catalog_covers_broad_workflows_and_records_provenance(self):
        data, _ = policy.catalog()
        self.assertGreaterEqual(len(data["allow"]), 400)
        self.assertGreaterEqual(len(data["read_hosts"]), 100)
        self.assertEqual({x["id"] for x in data["sources"]}, {"codex-rust-v0.106.0", "claude-permissions", "duo"})


    def test_deny_overrides_direct_nested_and_absolute_commands(self):
        for cmd in ("git status", "/usr/bin/git --version", "env FOO=1 git log", "timeout 3 git status",
                    "bash -lc 'git status'", "ls; rm -rf build", "find . -delete", "xargs rm", "printenv",
                    "./.duo/sbx 'git status'", "docker exec duo-task sh -lc 'rm -rf /work'",
                    "cat ~/.ssh/id_rsa", "cat .env", "cat /etc/shadow", "echo $AWS_SECRET_ACCESS_KEY"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.decision(cmd), "deny")
        self.assertEqual(self.decision('python3 -c \'print(open(".env").read())\''), "deny")

    def test_direct_docker_calls_are_allowed_with_safe_arguments(self):
        for cmd in ("docker --version", "docker version", "docker info", "docker ps", "docker images", "docker build -t test .",
                    "docker run --rm -v %s:/work python:3.13 python --version" % shlex.quote(str(self.root)),
                    "docker exec duo-task python -m pytest"):
            self.assertEqual(self.decision(cmd, "duo-task"), "allow", cmd)

    def test_docker_guard_blocks_host_access_and_wrong_container_escalation(self):
        for cmd in ("docker run --privileged ubuntu", "docker run -v/:/host ubuntu", "docker run -v../:/host ubuntu",
                    "docker --host ssh://remote ps", "docker -Htcp://remote ps", "docker build /",
                    "docker run --mount type=bind,src=/etc,dst=/host ubuntu", "docker run --pid=host ubuntu"):
            self.assertEqual(self.decision(cmd, "duo-task"), "deny", cmd)
        self.assertEqual(self.decision("docker exec unrelated sh", "duo-task"), "review")

    def test_shell_substitution_and_download_output_cannot_escape_checks(self):
        for cmd in ("echo $(git status)", "echo `rm file`", "curl --output=/etc/hosts https://docs.python.org/3/",
                    "curl -o/etc/hosts https://docs.python.org/3/", "wget -O/etc/hosts https://docs.python.org/3/", "docker plugin install unsafe/plugin"):
            self.assertEqual(self.decision(cmd), "deny", cmd)

    def test_compose_checks_rendered_settings(self):
        safe = {"services": {"app": {"volumes": [{"type": "bind", "source": str(self.root)}]}}}
        result = mock.Mock(returncode=0, stdout=json.dumps(safe))
        with mock.patch.object(policy.subprocess, "run", return_value=result):
            self.assertEqual(self.decision("docker compose up -d"), "allow")
            self.assertEqual(self.decision("docker-compose up -d"), "allow")
            safe["services"]["app"]["privileged"] = True
            result.stdout = json.dumps(safe)
            self.assertEqual(self.decision("docker compose up -d"), "deny")
            self.assertEqual(self.decision("docker-compose up -d"), "deny")

    def test_the_retired_guard_hook_never_votes_so_native_clis_decide_permissions(self):
        """Core 1.12.3 removed Duo's file and command vetoes. Deleting, secret and core-file protection
        is the native CLI's job, so this hook must stay a no-op rather than half-enforce a policy."""
        for event in (
                {"tool_name": "Read", "tool_input": {"file_path": str(self.root / ".env")}},
                {"tool_name": "Write", "tool_input": {"file_path": "/etc/hosts"}},
                {"tool_name": "apply_patch", "tool_input": {"command": "*** Delete File: app.py\n"}}):
            self.assertEqual(guard_hook.check(event, str(self.root))[0], "review")
        pre = {"hook_event_name": "PreToolUse"}
        for decision in ("deny", "review", "allow"):
            for provider in ("codex", "claude"):
                self.assertEqual(guard_hook.response(pre, decision, "reason", provider), {})

    def test_native_launches_do_not_attach_duo_tool_checks(self):
        for agent in ("codex", "claude"):
            words = shlex.split(agents.guarded_command(agent, agents.AGENT_SPECS[agent]["auto"], self.root))
            self.assertFalse(any("hooks." in word or "guard_hook.py" in word for word in words))
            self.assertNotIn("--dangerously-bypass-hook-trust", words)
            if agent == "claude":
                self.assertNotIn("--settings", words)
            else:
                self.assertIn("--approve-for-me", words)

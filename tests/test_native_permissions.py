"""Native permission decisions must not pass through Duo's command policy."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from engine import agents, config
from engine.session import Session


class NativePermissionTests(unittest.TestCase):
    def test_legacy_engine_surfaces_requests_without_policy_or_keypress(self):
        for agent in ("codex", "claude"):
            s = object.__new__(Session)
            s.st = {"pending": [], "actor_ids": {agent: "Worker"}, "agents": []}
            p = {"agent": agent}
            s.st["pending"].append(p)
            with mock.patch.object(s, "label", return_value="Worker"), mock.patch.object(s, "save"), \
                 mock.patch("engine.session.security.analyze", side_effect=AssertionError("Duo checked a native command")), \
                 mock.patch("engine.session.send_key") as key, mock.patch("engine.session.notify.send"):
                s._on_approval(p, "curl https://unlisted.example/data.csv", 42)
            key.assert_not_called()
            self.assertEqual(p["waiting"]["category"], "native")
            self.assertEqual(s.st["approval_counts"]["waiting"], 1)


    def test_all_native_launch_paths_have_no_duo_hooks_or_url_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".duo").mkdir()
            (root / ".duo/sbx").write_text("docker exec duo-example sh")
            for agent, commands in {
                "codex": ["codex exec prompt", agents.AGENT_SPECS["codex"]["auto"]],
                "claude": ["claude -p prompt", agents.AGENT_SPECS["claude"]["auto"]],
            }.items():
                for command in commands:
                    result = agents.guarded_command(agent, command, root, "duo-example")
                    for marker in ("guard_hook", "hooks.PreToolUse", "hooks.PermissionRequest", "allowedDomains", "strictAllowlist", "network_proxy"):
                        self.assertNotIn(marker, result)
                    self.assertEqual(agents.boxed(agent, command), command)

    def test_saved_codex_launch_keeps_its_own_network_settings_and_adds_no_domain_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            cmd = agents.guarded_command("codex", "codex --sandbox workspace-write --ask-for-approval on-request", folder)
            self.assertIn("--sandbox workspace-write --ask-for-approval on-request", cmd)
            self.assertNotIn("network_access", cmd)
            self.assertNotIn("network_proxy", cmd)
            cmd = agents.guarded_command("codex", "codex -c sandbox_workspace_write.network_access=false", folder)
            self.assertIn("sandbox_workspace_write.network_access=false", cmd)
            self.assertNotIn("sandbox_workspace_write.network_access=true", cmd)

    def test_saved_claude_default_is_removed_but_custom_settings_remain(self):
        import json
        import shlex
        settings = {"permissions": {"allow": ["WebSearch"] + ["WebFetch(domain:%s)" % domain for domain in
                    ("arxiv.org", "export.arxiv.org", "aclanthology.org", "openreview.net", "proceedings.neurips.cc",
                     "doi.org", "api.crossref.org", "dl.acm.org")]},
                    "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "allowUnsandboxedCommands": False}}
        with tempfile.TemporaryDirectory() as folder:
            command = shlex.join(["claude", "--settings", json.dumps(settings)])
            self.assertNotIn("--settings", shlex.split(agents.guarded_command("claude", command, folder)))
            settings["sandbox"]["excludedCommands"] = ["./.duo/sbx *", "docker *", "docker-compose *"]
            command = shlex.join(["claude", "--settings", json.dumps(settings)])
            self.assertNotIn("--settings", shlex.split(agents.guarded_command("claude", command, folder)))
            settings["permissions"]["deny"] = ["WebFetch(domain:example.org)"]
            command = shlex.join(["claude", "--settings", json.dumps(settings)])
            result = shlex.split(agents.guarded_command("claude", command, folder))
            self.assertEqual(json.loads(result[result.index("--settings") + 1]), settings)

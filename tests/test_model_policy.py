import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, model_policy as policy, orchestration, codex_worker


class RolePolicyTests(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.dict(config.CFG, {"role_model_routing": True, "role_models": {}})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.state = {"agents": ["claude", "codex"], "role_model_routing": True, "roles": {}}

    def test_claude_uses_an_explicit_model_and_agentic_permission_mode(self):
        worker = codex_worker.Worker(['claude', '--permission-mode', 'acceptEdits', '--model', 'old-model'])
        worker.model = 'claude-haiku-5-5'
        command = worker.argv()
        self.assertEqual(command.count('--model'), 1)
        self.assertNotIn('old-model', command)
        self.assertIn('--print', command)
        self.assertIn('--no-session-persistence', command)
        self.assertIn('acceptEdits', command)
        with self.assertRaises(RuntimeError):
            worker.event({'type':'system', 'subtype':'init', 'model':'wrong-model'}, [])

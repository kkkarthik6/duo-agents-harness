"""Official Antigravity model and headless protocol contracts; no live model calls."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from engine import agents, cli_adapters, codex_worker, model_catalog, roleflow


class AntigravityTests(unittest.TestCase):
    def test_catalog_has_ten_documented_families_and_explicit_tiers(self):
        rows = [r for r in model_catalog.entries() if r['cli'] == 'agy']
        self.assertEqual(len(rows), 10)
        self.assertEqual({r['tier'] for r in rows}, {'large', 'medium', 'small'})
        self.assertTrue(all(r['source_url'].startswith('https://antigravity.google/') for r in rows))
        self.assertTrue(all(r['account_access'] == 'unverified' for r in rows))
        self.assertFalse(any('orchestrator' in r['id'] or '4-6' in r['id'] for r in rows))
        self.assertEqual(next(r for r in rows if r['display_name'] == 'Gemini 3.1 Pro')['id'], 'gemini-3.1-pro-preview')

    def test_native_stream_uses_one_fresh_assignment_and_no_resume(self):
        worker = codex_worker.Worker(['agy', '--mode', 'accept-edits'])
        worker.model = 'gemini-4-neon'
        argv = worker.argv('task')
        self.assertIn('--input-format', argv)
        self.assertNotIn('--continue', argv)
        self.assertNotIn('--conversation', argv)
        self.assertEqual(json.loads(worker.adapter.encode_prompt('task')), {'event': 'user', 'message': {'content': 'task'}})
        self.assertEqual(argv[argv.index('--model') + 1], worker.model)
        self.assertNotIn('--dangerously-skip-permissions', argv)

    def test_success_error_and_model_events_are_normalized(self):
        adapter = cli_adapters.get('agy')
        self.assertEqual(list(adapter.events({'event': 'init', 'init': {'model': 'gemini-4-neon'}})),
                         [('model', 'gemini-4-neon')])
        success = {'event': 'result', 'result': {'status': 'SUCCESS', 'response': 'Done', 'usage': {'input_tokens': 12}}}
        self.assertEqual(list(adapter.events(success)), [('usage', {'input_tokens': 12}), ('text', 'Done')])
        error = {'event': 'step_update', 'step_update': {'tool_info': {'error': {'message': 'Check failed'}}}}
        self.assertEqual(list(adapter.events(error))[0][0], 'diagnostic')
        for status in ('ERROR', 'WAITING', 'CANCELED', 'INVALID', 'RUNNING'):
            event = {'event': 'result', 'result': {'status': status, 'error': 'authentication required'}}
            self.assertEqual(list(adapter.events(event))[0][0], 'failure')
        worker = codex_worker.Worker(['agy'])
        with contextlib.redirect_stdout(io.StringIO()):
            worker.event({'event': 'result', 'result': {'status': 'ERROR', 'error': 'model is not recognized as a known model'}}, [])
        self.assertTrue(worker.failed)

    def test_discovery_checks_the_existing_agy_command_and_flags(self):
        import subprocess
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, '--model --input-format --output-format 1.5.7', '')
        found = agents.detect(which=lambda name: '/bin/agy' if name == 'agy' else None, run=run)
        self.assertEqual([a.id for a in found], ['agy'])
        self.assertTrue(found[0].capability_check['ready'])
        self.assertIn('codex_worker.py', agents.guarded_command('agy', found[0].cmd_auto, '.'))
        for command in ('agy --continue', 'agy -c', 'agy --conversation previous', 'agy --conversation=previous'):
            with self.assertRaisesRegex(ValueError, 'fresh Antigravity'):
                agents.guarded_command('agy', command, '.')

    def test_agy_alone_can_fill_four_fresh_roles(self):
        profile = roleflow.ensure({'workflow': 'roles', 'agents': ['agy'], 'roles': {}})
        self.assertTrue(all(r['agent'] == 'agy' for r in profile.values()))
        self.assertEqual(profile['lead']['size'], 'large')
        self.assertEqual(profile['challenger']['size'], 'large')
        self.assertEqual(profile['builder']['size'], 'small')

    def test_real_worker_fixture_reads_native_json_input(self):
        import tempfile
        import sys
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'agy'
            executable.write_text('#!' + sys.executable + '\nimport sys,json\n'
                                  'assert "--continue" not in sys.argv\n'
                                  'messages=[json.loads(s) for s in sys.stdin if s.strip()]\n'
                                  'assert len(messages)==1 and messages[0]["event"]=="user"\n'
                                  'print(json.dumps({"event":"result","result":{"status":"SUCCESS","response":"Saved output.","usage":{}}}))\n')
            executable.chmod(0o700)
            worker = codex_worker.Worker([str(executable)])
            worker.model = 'gemini-4-neon'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(worker.turn('Complete the task.'), 0)
            self.assertEqual(worker.history[-1][1], 'Saved output.')


if __name__ == '__main__':
    unittest.main()

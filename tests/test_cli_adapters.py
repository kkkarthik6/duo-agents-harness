"""Transport edge cases use recorded event shapes, never live provider calls."""
import contextlib
import io
import unittest
from unittest.mock import patch

from engine import config, cli_adapters, agent_failures, model_catalog, roleflow, codex_worker


class AdapterEventTests(unittest.TestCase):
    def test_claude_reports_tools_permissions_usage_and_failed_results(self):
        adapter = cli_adapters.get('claude')
        events = [
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Saved output.'}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'is_error': True, 'content': 'Tool failed'}]}},
            {'type': 'result', 'permission_denials': [{'tool_name': 'Bash'}], 'is_error': True,
             'api_error_status': 429, 'result': 'monthly spend limit'},
            {'type': 'result', 'usage': {'input_tokens': 10}, 'result': 'Done'},
        ]
        operations = [op for event in events for op, value in adapter.events(event)]
        self.assertEqual(operations, ['text', 'diagnostic', 'permission', 'failure', 'usage', 'text'])
        worker = codex_worker.Worker(['claude'])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            for event in events:
                worker.event(event, [])
        self.assertTrue(worker.failed)
        self.assertEqual(agent_failures.from_screen(output.getvalue())['kind'], 'spend_limit')

    def test_codex_reports_command_errors_and_failed_turns(self):
        adapter = cli_adapters.get('codex')
        event = {'type': 'item.completed', 'item': {'type': 'command_execution', 'exit_code': 1,
                                                  'aggregated_output': 'Check failed'}}
        self.assertEqual([op for op, value in adapter.events(event)], ['diagnostic', 'output'])
        self.assertEqual(list(adapter.events({'type': 'turn.completed', 'usage': {'output_tokens': 12}}))[0][0], 'usage')
        self.assertEqual(list(adapter.events({'type': 'turn.failed', 'error': {'message': 'unavailable'}}))[0][0], 'failure')

    def test_portable_jsonl_and_argument_prompt(self):
        settings = {'local': {'executable': 'local-agent', 'protocol': 'jsonl', 'prompt_mode': 'arg', 'prompt_flag': '--prompt'}}
        with patch.dict(config.CFG, {'cli_adapters': settings}):
            adapter = cli_adapters.get('local-agent')
            self.assertEqual(adapter.argv(['local-agent'], 'small', 'task text'),
                             ['local-agent', '--model', 'small', '--prompt', 'task text'])
            for event, expected in [({'type': 'text', 'text': 'Done'}, 'text'),
                                    ({'type': 'usage', 'usage': {}}, 'usage'),
                                    ({'type': 'error', 'message': 'Failed'}, 'failure'),
                                    ({'type': 'model', 'model': 'small'}, 'model')]:
                self.assertEqual(list(adapter.events(event))[0][0], expected)

    def test_invalid_adapter_configuration_is_rejected(self):
        for spec in ({}, {'executable': 4}, {'executable': 'a', 'protocol': 'unknown'},
                     {'executable': 'a', 'prompt_mode': 'unknown'}, {'executable': 'a', 'args': 'run'}):
            with self.subTest(spec=spec), patch.dict(config.CFG, {'cli_adapters': {'bad': spec}}):
                with self.assertRaises(ValueError):
                    cli_adapters.registry()
        with self.assertRaisesRegex(ValueError, 'No unambiguous'):
            cli_adapters.get('unconfigured-cli')
        with self.assertRaisesRegex(ValueError, 'model selection'):
            cli_adapters.Adapter('x', 'x', 'x', 'x', 'text', model_flag='').argv(['x'], 'm')


class RoutingFailureTests(unittest.TestCase):
    def test_bad_catalog_entries_are_rejected(self):
        row = {'cli': 'codex', 'id': 'm', 'tier': 'small', 'available': True}
        for catalog in (None, [{}], [dict(row, capabilities='tools')], [row, row]):
            with self.subTest(catalog=catalog), patch.dict(config.CFG, {'model_catalog': catalog}):
                with self.assertRaises(ValueError):
                    model_catalog.entries()

    def test_provider_alias_override_and_incapable_models(self):
        state = {'workflow': 'roles', 'agents': ['codex'], 'roles': {}}
        with patch.dict(config.CFG, {'role_models': {'builder': {'provider': 'codex', 'model': 'gpt-6-luna'}}}):
            self.assertEqual(roleflow.ensure(state)['builder']['model'], 'gpt-6-luna')
        row = {'cli': 'codex', 'id': 'm', 'tier': 'small', 'capabilities': [], 'available': True}
        with patch.dict(config.CFG, {'model_catalog': [row]}):
            self.assertEqual(model_catalog.candidates(state, 'small'), [])

    def test_failure_scopes_and_malformed_diagnostics(self):
        self.assertEqual(agent_failures.classify({'message': 'model not found'})['kind'], 'model_unavailable')
        self.assertEqual(agent_failures.classify({'message': 'permission denied'})['kind'], 'permission_denied')
        self.assertEqual(agent_failures.from_screen('DUO_WORKER_FAILURE:{broken')['kind'], 'process_failure')
        self.assertEqual(agent_failures.from_screen('DUO_WORKER_FAILURE:[]')['kind'], 'process_failure')
        state = {'agents': ['codex', 'claude']}
        agent_failures.record(state, 'codex', 'gpt-6-astra', {'kind': 'rate_limit', 'retry_after': 'unknown'}, now=10)
        self.assertEqual(state['agent_availability']['openai']['until'], 70)
        legacy = agent_failures.replacement(state, 'codex', 'gpt-6-astra', 'build')
        self.assertEqual(legacy['agent'], 'claude')
        agent_failures.record(state, 'claude', 'claude-opus-5-5', {'kind': 'auth_expired'}, now=10)
        agent_failures.record(state, 'codex', 'gpt-6-astra', {'kind': 'spend_limit'}, now=10)
        with self.assertRaisesRegex(ValueError, 'No eligible'):
            agent_failures.replacement(state, 'codex', 'gpt-6-astra', 'build')
        agent_failures.record(state, 'unknown', 'm', {'kind': 'process_failure'}, now=10)
        self.assertIn('unknown:m', state['agent_availability'])

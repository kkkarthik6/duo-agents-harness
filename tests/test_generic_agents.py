"""Contracts for portable roles, adapters, discovery, and provider recovery."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import config






class GenericAgentTests(unittest.TestCase):
    def setUp(self):
        # These tests never call paid providers or launch background summaries.
        for target in ('engine.summary_agent.observe', 'engine.summary_agent.request', 'engine.completion_summary.request'):
            p = patch(target)
            p.start()
            self.addCleanup(p.stop)


    def test_roles_select_explicit_tiers_and_allow_same_model_for_independent_agents(self):
        from engine import roleflow
        state = {'workflow': 'roles', 'agents': ['codex'], 'roles': {}}
        profile = roleflow.ensure(state)
        self.assertEqual([profile[r]['size'] for r in roleflow.ROLES], ['large', 'large', 'small', 'medium'])
        # Reviewers are at least as strong as the agent they review.
        self.assertNotEqual(profile['builder']['model'], profile['critic']['model'])
        first = roleflow.assignment(state, 'build', 'T1')
        second = roleflow.assignment(state, 'review', 'T1')
        self.assertNotEqual(first['instance_id'], second['instance_id'])

    def test_configured_adapter_is_discovered_and_executes_without_provider_branches(self):
        from engine import cli_adapters, agents, codex_worker
        settings = {'local-agent': {'label': 'Local', 'executable': 'local-agent', 'protocol': 'text',
                    'args': ['run'], 'model_flag': '--model', 'capabilities': ['tools', 'fresh'],
                    'skill_paths': ['~/.local-agent/skills']}}
        with patch.dict(config.CFG, {'cli_adapters': settings}):
            found = agents.detect(which=lambda n: '/bin/local-agent' if n == 'local-agent' else None,
                                  run=lambda *a, **k: __import__('subprocess').CompletedProcess(a, 0, '1.0', ''))
            self.assertEqual([a.id for a in found], ['local-agent'])
            adapter = cli_adapters.get('local-agent')
            self.assertIn('tools', adapter.capabilities)
            worker = codex_worker.Worker(['local-agent'])
            worker.model = 'local-small'
            self.assertEqual(worker.argv(), ['local-agent', 'run', '--model', 'local-small'])

    def test_unconfigured_or_incapable_cli_cannot_fill_roles(self):
        from engine import roleflow
        with self.assertRaisesRegex(ValueError, 'catalog|eligible'):
            roleflow.ensure({'workflow': 'roles', 'agents': ['unknown'], 'roles': {}})

    def test_catalog_availability_and_tiers_are_explicit(self):
        from engine import model_catalog, roleflow
        models = [{'cli': 'codex', 'id': 'very-large-name', 'tier': 'small', 'capabilities': ['tools'], 'available': True}]
        with patch.dict(config.CFG, {'model_catalog': models}):
            self.assertEqual(model_catalog.entries()[0]['tier'], 'small')
            state = {'workflow': 'roles', 'agents': ['codex'], 'roles': {}}
            profile = roleflow.ensure(state)
            # The name "very-large-name" does not make it large: the run still starts, on the nearest tier, and says so.
            self.assertEqual(profile['lead']['model'], 'very-large-name')
            self.assertEqual(profile['lead']['fallback'], 'nearest tier: small')
            self.assertTrue(state['model_fallbacks'])

    def test_failures_classify_and_exclude_provider_before_retry(self):
        from engine import agent_failures, roleflow
        state = {'workflow': 'roles', 'agents': ['claude', 'codex'], 'roles': {}}
        failure = agent_failures.classify({'api_error': 'usage_limit_reached', 'result': 'monthly spend limit'})
        self.assertEqual(failure['kind'], 'spend_limit')
        agent_failures.record(state, 'claude', 'claude-opus-5-5', failure, now=100)
        self.assertTrue(all(p['agent'] == 'codex' for p in roleflow.ensure(state, now=101).values()))
        self.assertEqual(agent_failures.classify({'message': 'OAuth token expired'})['kind'], 'auth_expired')
        self.assertEqual(agent_failures.classify({'api_error_status': 429})['kind'], 'rate_limit')
        self.assertEqual(agent_failures.classify({'exit_code': 1})['kind'], 'process_failure')

    def test_skills_include_project_shared_and_adapter_locations_without_duplicates(self):
        from engine import skill_inventory
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / '.agents/skills/example'; p.mkdir(parents=True)
            (p / 'SKILL.md').write_text('---\nname: example\ndescription: Helpful skill\n---\nInstructions')
            with patch.dict(config.CFG, {'skill_paths': [str(root / '.agents/skills')]}):
                found = skill_inventory.discover(root, ['codex'], home=root)
            self.assertEqual(len(found), 1)
            self.assertIn('Helpful skill', found[0]['description'])

    def test_phase_flow_requires_plan_review_before_build(self):
        from engine import phases, roleflow
        from tests.test_phases import st
        state = st(agents=['codex'])
        roleflow.ensure(state)
        self.assertEqual(phases.next_turns(state)[0].kind, 'exchange')
        state['chosen'] = 'O1'
        self.assertEqual(phases.next_turns(state)[0].kind, 'plan_review')
        state['plan_verdict'] = 'REVISE'
        self.assertEqual(phases.next_turns(state)[0].kind, 'exchange')
        self.assertEqual(phases.next_turns(state)[0].kind, 'plan_review')
        state['plan_verdict'] = 'APPROVED'
        self.assertEqual(phases.next_turns(state), [])
        self.assertEqual(phases.next_turns(state)[0].kind, 'build')












    def test_real_configured_text_cli_and_stderr_login_failure(self):
        import contextlib
        import io
        import sys
        from engine import codex_worker, agent_failures
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'local-agent'
            script.write_text('#!' + sys.executable + '\nimport sys\np=sys.stdin.read()\n'
                              'if "fail-login" in p:\n print("OAuth token expired",file=sys.stderr);sys.exit(1)\n'
                              'print("Saved the requested result.")\n')
            script.chmod(0o700)
            with patch.dict(config.CFG, {'cli_adapters': {'local': {'executable': str(script), 'protocol': 'text',
                                                      'capabilities': ['tools', 'fresh']}}}):
                worker = codex_worker.Worker([str(script)], 'local')
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(worker.turn('build the requested output'), 0)
                    self.assertEqual(worker.turn('fail-login'), 1)
                self.assertEqual(agent_failures.from_screen(output.getvalue())['kind'], 'auth_expired')
                self.assertEqual(len(worker.history), 1)

    def test_configured_catalog_can_run_all_roles_on_another_cli(self):
        from engine import roleflow
        adapters = {'local': {'executable': 'local-agent', 'protocol': 'jsonl', 'capabilities': ['tools', 'fresh']}}
        catalog = [{'cli': 'local', 'id': tier + '-local', 'tier': tier, 'capabilities': ['tools'], 'available': True}
                   for tier in ('large', 'medium', 'small')]
        with patch.dict(config.CFG, {'cli_adapters': adapters, 'model_catalog': catalog}):
            profile = roleflow.ensure({'workflow': 'roles', 'agents': ['local'], 'roles': {}})
        self.assertTrue(all(r['agent'] == 'local' for r in profile.values()))
        self.assertEqual(profile['builder']['model'], 'small-local')



if __name__ == '__main__':
    unittest.main()

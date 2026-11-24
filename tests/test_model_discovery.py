"""Live discovery contracts use injected CLI inventories and model responses."""
import unittest
from unittest.mock import patch
from engine import config, model_catalog


class ModelDiscoveryTests(unittest.TestCase):
    def setUp(self):
        from engine import model_discovery
        self.discovery = model_discovery
        model_discovery.clear()
        self.addCleanup(model_discovery.clear)

    def snapshot(self, models, now=100, force=False):
        return self.discovery.refresh(force=force, now=now, cwd='/private/tmp',
            which=lambda exe: '/bin/codex' if exe == 'codex' else None,
            probe=lambda adapter, path, cwd: {'models': models, 'source': 'test_cli'})

    def test_only_installed_clis_and_reported_models_are_offered(self):
        snap = self.snapshot([{'id': 'gpt-6-astra'}])
        rows = self.discovery.catalog(snap, model_catalog.entries())
        self.assertEqual([(r['cli'], r['id']) for r in rows], [('codex', 'gpt-6-astra')])
        self.assertEqual(rows[0]['tier'], 'large')
        self.assertEqual(rows[0]['account_access'], 'not_tested')
        self.assertEqual(rows[0]['checked_at'], 100)

    def test_new_models_appear_without_guessing_tiers(self):
        rows = self.discovery.catalog(self.snapshot([{'id': 'new-model'}]), model_catalog.entries())
        self.assertEqual(rows[0]['id'], 'new-model')
        self.assertIsNone(rows[0]['tier'])
        self.assertFalse(rows[0]['available'])
        self.assertEqual(rows[0]['status'], 'needs_tier')

    def test_additive_tier_policy_enables_a_newly_listed_model(self):
        snap = self.snapshot([{'id': 'new-model'}])
        with patch.dict(config.CFG, {'model_tiers': {'codex': {'new-model': 'large'}}}):
            row = self.discovery.catalog(snap, model_catalog.entries())[0]
        self.assertEqual(row['tier'], 'large')
        self.assertTrue(row['available'])

    def test_invalid_tier_policy_is_rejected(self):
        for value in (None, {'codex': []}, {'codex': {'new-model': 'guess'}}):
            with patch.dict(config.CFG, {'model_tiers': value}), self.assertRaises(ValueError):
                model_catalog.entries()

    def test_model_and_login_failures_invalidate_the_matching_workspace(self):
        from engine import agent_failures
        state = {'agents': ['codex'], 'dynamic_models': True, 'workspace': '/private/tmp'}
        with patch.object(self.discovery, 'invalidate') as invalidate:
            agent_failures.record(state, 'codex', 'm', {'kind': 'model_unavailable'})
        invalidate.assert_called_once_with('/private/tmp')

    def test_refresh_removes_retired_models_and_expired_snapshots_are_not_current(self):
        self.snapshot([{'id': 'old'}])
        snap = self.snapshot([{'id': 'new'}], now=101, force=True)
        self.assertEqual([r['id'] for r in self.discovery.catalog(snap, [])], ['new'])
        self.assertFalse(self.discovery.is_fresh(snap, now=10000))

    def test_failed_refresh_does_not_relabel_old_results_as_live(self):
        self.snapshot([{'id': 'gpt-6-astra'}])
        def failed(*args):
            raise TimeoutError('discovery timed out')
        snap = self.discovery.refresh(force=True, now=101, cwd='/private/tmp',
            which=lambda exe: '/bin/codex' if exe == 'codex' else None, probe=failed)
        self.assertEqual(self.discovery.catalog(snap, model_catalog.entries()), [])
        row = next(c for c in snap['clis'] if c['id'] == 'codex')
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['models'], [])

    def test_cache_has_a_ttl_and_force_refresh_bypasses_it(self):
        snap = self.snapshot([{'id': 'first'}])
        cached = self.snapshot([{'id': 'second'}], now=101)
        self.assertEqual(snap, cached)
        fresh = self.snapshot([{'id': 'second'}], now=10000)
        self.assertEqual(fresh['clis'][0]['models'][0]['id'], 'second')

    def test_claude_aliases_use_resolved_model_without_duplicate_default(self):
        rows = self.discovery.normalize([
            {'value': 'default', 'resolvedModel': 'claude-opus-5-5', 'displayName': 'Default'},
            {'value': 'opus', 'resolvedModel': 'claude-opus-5-5', 'displayName': 'Opus'}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 'claude-opus-5-5')
        self.assertIn('opus', rows[0]['aliases'])

    def test_custom_json_and_agy_text_are_parsed_without_headers(self):
        rows = self.discovery.parse_models('gemini-4-neon-medium   Gemini 4 Neon (Medium)\n', 'text')
        self.assertEqual(rows[0]['id'], 'gemini-4-neon-medium')
        self.assertEqual(self.discovery.parse_models('{"models":[{"id":"local"}]}', 'json')[0]['id'], 'local')
        with self.assertRaises(ValueError):
            self.discovery.parse_models('not JSON', 'json')

    def test_dynamic_routing_excludes_a_model_removed_from_the_live_list(self):
        snap = self.snapshot([{'id': 'gpt-6-luna'}])
        state = {'agents': ['codex'], 'dynamic_models': True, 'workspace': '/private/tmp'}
        with patch.object(self.discovery, 'refresh', return_value=snap):
            self.assertEqual(model_catalog.candidates(state, 'large'), [])
            self.assertEqual([r['id'] for r in model_catalog.candidates(state, 'small')], ['gpt-6-luna'])

    def test_uninstalled_cli_is_removed_without_waiting_for_ttl(self):
        self.snapshot([{'id': 'gpt-6-astra'}])
        snap = self.discovery.refresh(now=101, cwd='/private/tmp', which=lambda exe: None,
                                      probe=lambda *args: self.fail('An absent CLI must not be invoked'))
        self.assertEqual(self.discovery.catalog(snap, model_catalog.entries()), [])

    def test_auth_metadata_change_invalidates_cached_listing_without_reading_credentials(self):
        with patch.object(self.discovery, '_stamp', return_value=['metadata', 1]):
            self.snapshot([{'id': 'first'}])
        with patch.object(self.discovery, '_stamp', return_value=['metadata', 2]), \
                patch('pathlib.Path.read_text', side_effect=AssertionError('Discovery must not read credential contents')):
            snap = self.snapshot([{'id': 'second'}], now=101)
        self.assertEqual(snap['clis'][0]['models'][0]['id'], 'second')

    def test_unsafe_identifiers_and_hidden_choices_are_rejected(self):
        for identifier in ('--option', 'model\nnew task', 'x' * 513):
            with self.assertRaises(ValueError):
                self.discovery.normalize([identifier])
        self.assertEqual(self.discovery.normalize([{'id': 'hidden', 'hidden': True}, {'id': 'blocked', 'available': False}]), [])
        with self.assertRaises(ValueError):
            self.discovery.parse_models('This is not a model listing.', 'text')

    def test_configured_disable_remains_effective(self):
        snap = self.snapshot([{'id': 'gpt-6-astra'}])
        policy = [{'cli': 'codex', 'id': 'gpt-6-astra', 'tier': 'large', 'available': False}]
        row = self.discovery.catalog(snap, policy)[0]
        self.assertFalse(row['available'])
        self.assertEqual(row['status'], 'disabled')

    def test_effort_variant_uses_explicit_family_policy(self):
        snap = {'clis': [{'id': 'agy', 'installed': True, 'status': 'ready', 'checked_at': 100,
                         'models': self.discovery.normalize([{'id': 'gemini-4-neon-high'}])}]}
        row = self.discovery.catalog(snap, model_catalog.entries())[0]
        self.assertEqual(row['id'], 'gemini-4-neon-high')
        self.assertEqual(row['tier'], 'large')


class MetadataTransportTests(unittest.TestCase):
    def test_metadata_timeout_and_early_exit_leave_no_successful_listing(self):
        import tempfile
        import sys
        from pathlib import Path
        from engine import model_discovery, cli_adapters
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'claude'
            path.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(5)\n')
            path.chmod(0o700)
            with patch.dict(config.CFG, {'model_discovery_timeout_seconds': 0.05}):
                with self.assertRaises(TimeoutError):
                    model_discovery.probe_cli(cli_adapters.get('claude'), str(path), directory)
            path.write_text('#!' + sys.executable + '\nprint("not a JSON message",flush=True)\n')
            with self.assertRaises(RuntimeError):
                model_discovery.probe_cli(cli_adapters.get('claude'), str(path), directory)

    def test_real_stdio_fixtures_receive_only_metadata_requests(self):
        import tempfile
        import sys
        from pathlib import Path
        from engine import model_discovery, cli_adapters
        script = '''import sys,json
for line in sys.stdin:
 r=json.loads(line)
 if "request" in r:
  assert r["request"]["subtype"]=="initialize"
  print(json.dumps({"type":"control_response","response":{"subtype":"success","request_id":r["request_id"],"response":{"models":[{"value":"opus","resolvedModel":"claude-opus-5-5"}]}}}),flush=True)
 elif r.get("method")=="initialize":
  print(json.dumps({"id":r["id"],"result":{}}),flush=True)
 elif r.get("method")=="initialized":
  continue
 else:
  assert r["method"]=="model/list"
  page=1 if not r["params"].get("cursor") else 2
  print(json.dumps({"id":r["id"],"result":{"data":[{"model":"m"+str(page)}],"nextCursor":"next" if page==1 else None}}),flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            for cli in ('codex', 'claude'):
                path = Path(directory) / cli
                path.write_text('#!' + sys.executable + '\n' + script)
                path.chmod(0o700)
                response = model_discovery.probe_cli(cli_adapters.get(cli), str(path), directory)
                self.assertEqual([r['id'] for r in response['models']], ['m1', 'm2'] if cli == 'codex' else ['claude-opus-5-5'])

    def test_command_discovery_custom_json_timeout_and_unsupported_adapter(self):
        import subprocess
        from engine import model_discovery, cli_adapters
        adapter = cli_adapters.Adapter('local', 'local', 'Local', 'local', 'jsonl', model_list_args=('models', '--json'))
        with patch.object(model_discovery.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '["local-model"]', '')) as run:
            result = model_discovery.probe_cli(adapter, '/bin/local', '.')
            self.assertEqual(result['models'][0]['id'], 'local-model')
            self.assertEqual(run.call_args.args[0], ['/bin/local', 'models', '--json'])
        with patch.object(model_discovery.subprocess, 'run', side_effect=subprocess.TimeoutExpired('models', 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                model_discovery.probe_cli(adapter, '/bin/local', '.')
        plain = cli_adapters.Adapter('local', 'local', 'Local', 'local', 'text')
        self.assertEqual(model_discovery.probe_cli(plain, '/bin/local', '.')['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()

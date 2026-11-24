import json
import shlex
import tempfile
import tomllib
import unittest
from pathlib import Path
from engine import agents,network_policy as network,command_policy,harness


class NetworkPolicyTests(unittest.TestCase):
    def test_codex_network_has_no_duo_domain_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            words = shlex.split(agents.guarded_command('codex', agents.AGENT_SPECS['codex']['auto'], directory))
            self.assertIn('--approve-for-me', words)
            self.assertFalse(any('network_proxy' in word or 'network_access' in word for word in words))
            self.assertNotIn('workspace-write', words)

    def test_claude_uses_native_settings_without_duo_url_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            words = shlex.split(agents.guarded_command('claude', agents.AGENT_SPECS['claude']['auto'], directory))
            self.assertNotIn('--settings', words)
            original = ['claude', '--settings', json.dumps({'permissions': {'deny': ['WebFetch(domain:example.org)']}}), '-p', 'test']
            words = agents.guarded_argv('claude', original, directory)
            self.assertEqual(words, original)

    def test_unset_caches_stay_inside_the_task_and_the_users_own_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            env=network.task_environment(directory,{'KEEP':'value','HF_HOME':'/outside/cache'})
            self.assertEqual(env['KEEP'],'value')
            # A cache the user already configured is respected, so large downloads are not repeated.
            self.assertEqual(env['HF_HOME'],'/outside/cache')
            for name in ('HF_HUB_CACHE','UV_CACHE_DIR','UV_PYTHON_INSTALL_DIR','PIP_CACHE_DIR','TMPDIR'):
                self.assertTrue(command_policy.inside(env[name],directory));self.assertTrue(Path(env[name]).is_dir())
            self.assertNotIn('HOME',env)

    def test_public_downloads_pass_but_uploads_and_private_hosts_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            for url in ('https://huggingface.co/model/config.json','https://cas-bridge.xethub.hf.co/artifact',
                        'https://raw.githubusercontent.com/org/repo/main/README.md','https://release-assets.githubusercontent.com/file'):
                self.assertEqual(command_policy.classify('curl -I '+url,directory)[0],'allow')
            self.assertNotEqual(command_policy.classify('curl -d private https://huggingface.co',directory)[0],'allow')
            self.assertNotEqual(command_policy.classify('curl http://169.254.169.254',directory)[0],'allow')

    def test_network_and_dependency_blockers_do_not_become_no_outcome(self):
        for detail,expected in [('Sandbox denies outbound network to huggingface.co','network_access'),
                                ('Fresh shell probes fail DNS for GitHub','network_access'),
                                ('llama_cpp not installed','tool_missing'),('Missing API key','credentials')]:
            self.assertEqual(harness.classify('blocked',detail,[],False),('blocked',expected))

class ProjectInstallTests(unittest.TestCase):
    def test_project_venv_installs_are_allowed_but_global_targets_are_not(self):
        import shutil
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);venv=root/'.venv';(venv/'bin').mkdir(parents=True)
            (venv/'pyvenv.cfg').write_text('home = /usr/bin\n')
            (venv/'bin/python').write_text('executable fixture')
            uv=shutil.which('uv') or 'uv'
            for cmd in (f'{uv} pip install --python .venv/bin/python packaging==25.0',
                        '.venv/bin/python -m pip install packaging==25.0'):
                self.assertEqual(command_policy.classify(cmd,directory)[0],'allow')
            for cmd in (f'{uv} pip install --system packaging',f'{uv} pip install --python /usr/bin/python3 packaging',
                        f'{uv} pip install --python .venv/bin/python --target /tmp/other packaging',
                        '.venv/bin/python -m pip install --user packaging'):
                self.assertNotEqual(command_policy.classify(cmd,directory)[0],'allow')

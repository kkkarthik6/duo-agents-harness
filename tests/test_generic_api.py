"""Local HTTP API contracts for generic model and adapter metadata."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import config, features
from engine.server import Engine, make_server
from tests.test_server import call


class GenericApiTests(unittest.TestCase):
    def test_catalog_and_unavailable_roles_are_readable_without_a_cli(self):
        previous = features.active()
        with tempfile.TemporaryDirectory() as directory, patch.dict(config.CFG, {'workflow': 'roles', 'root': directory}):
            server = make_server(0, Engine(agents=[], tick=False, home=Path(directory) / 'Duo'))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                code, catalog = call(port, '/api/models/catalog')
                self.assertEqual(code, 200)
                self.assertEqual(catalog['models'], [])
                self.assertEqual({row['tier'] for row in catalog['configured_models']}, {'large', 'medium', 'small'})
                self.assertEqual({a['id'] for a in catalog['adapters']}, {'claude', 'codex', 'agy'})
                code, roles = call(port, '/api/models/roles')
                self.assertEqual(code, 200)
                self.assertEqual(roles['roles'], [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                features.activate(previous)

    def test_catalog_uses_the_current_discovery_snapshot_and_force_refresh(self):
        previous = features.active()
        with tempfile.TemporaryDirectory() as directory, patch.dict(config.CFG, {'workflow': 'roles', 'root': directory}):
            engine = Engine(agents=[], tick=False, home=Path(directory) / 'Duo')
            snapshot = {'checked_at': 100, 'expires_at': 160, 'clis': [
                {'id': 'codex', 'installed': True, 'status': 'ready', 'checked_at': 100,
                 'models': [{'id': 'gpt-6-astra', 'aliases': []}, {'id': 'new-model', 'aliases': []}]}]}
            server = make_server(0, engine)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(engine, 'model_inventory', return_value=snapshot) as inventory:
                    code, result = call(server.server_address[1], '/api/models/catalog?refresh=1')
                self.assertEqual(code, 200)
                self.assertEqual([r['id'] for r in result['models']], ['gpt-6-astra', 'new-model'])
                self.assertEqual(result['models'][1]['status'], 'needs_tier')
                self.assertEqual(result['discovery']['checked_at'], 100)
                inventory.assert_called_once_with(force=True)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                features.activate(previous)

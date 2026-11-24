"""Cross-project history must stay visible without changing execution."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine import config, session, session_index, sessionlog
from engine.server import Engine, make_server
from tests.test_server import call


class SessionIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / 'Duo'
        self.old = self.base / 'Duo Workouts' / 'cognitive_core'
        self.current = self.base / 'ORB'
        self.current.mkdir()
        self.enterContext(mock.patch.object(config, 'DUO_HOME', self.home))
        self.enterContext(mock.patch.dict(config.CFG, root=str(self.current)))
        self.add_run(self.old, 'cognitive', '2026-11-19T15:45:16')
        self.add_run(self.current, 'orb', '2026-11-19T17:36:10')

    def add_run(self, root, sid, created, status='stopped'):
        d = root / 'project' / '.duo' / sid
        d.mkdir(parents=True)
        state = dict(id=sid, title=sid, created=created, status=status, entries=[],
                     workspace=str(d.parent.parent), duo_dir=str(d), project=root.name)
        (d / 'state.json').write_text(json.dumps(state))
        p = root / '.duo-index.json'
        idx = json.loads(p.read_text()) if p.exists() else []
        p.write_text(json.dumps(idx + [dict(id=sid, duo_dir=str(d))]))
        return d

    def test_discovery_recovers_legacy_and_keeps_newest_first(self):
        session_index.discover(self.home)
        self.assertEqual([x['id'] for x in session.list_sessions()], ['orb', 'cognitive'])
        self.assertEqual([x['id'] for x in session.list_sessions(all_projects=False)], ['orb'])
        self.assertEqual({x['id'] for x in sessionlog.list_all(self.home)}, {'orb', 'cognitive'})

    def test_root_change_remembers_both_roots_without_moving_files(self):
        with mock.patch.dict(config.CFG, root=str(self.old)):
            config.save_global({'root': str(self.current)})
        self.assertEqual([x['id'] for x in session.list_sessions()], ['orb', 'cognitive'])
        self.assertTrue((self.old / 'project/.duo/cognitive/state.json').exists())

    def test_find_uses_original_folder(self):
        session_index.discover(self.home)
        with mock.patch.object(session, 'load', return_value='saved') as load:
            self.assertEqual(session.find('cognitive'), 'saved')
            self.assertEqual(session.find_or_explain('cognitive'), 'saved')
        self.assertEqual(load.call_args.args[0], str(self.old / 'project/.duo/cognitive'))

    def test_bad_index_and_duplicate_references_do_not_hide_history(self):
        session_index.discover(self.home)
        broken = self.base / 'broken'
        broken.mkdir()
        (broken / '.duo-index.json').write_text('{')
        session_index.remember(broken)
        p = self.current / '.duo-index.json'
        entries = json.loads(p.read_text())
        entries.extend(json.loads((self.old / '.duo-index.json').read_text()))
        entries.extend([None, {'id': 'invalid'}])
        p.write_text(json.dumps(entries))
        self.assertEqual([x['id'] for x in session.list_sessions()], ['orb', 'cognitive'])

    def test_delete_changes_only_selected_run_and_its_index(self):
        session_index.discover(self.home)
        before = (self.current / '.duo-index.json').read_bytes()
        session.delete_run('cognitive')
        self.assertEqual((self.current / '.duo-index.json').read_bytes(), before)
        self.assertTrue((self.old / 'project').exists())
        self.assertFalse((self.old / 'project/.duo/cognitive').exists())
        self.assertEqual(json.loads((self.old / '.duo-index.json').read_text()), [])

    def test_ambiguous_ids_are_not_opened_or_deleted(self):
        session_index.discover(self.home)
        self.add_run(self.old, 'orb', '2026-11-19T17:36:10')
        with self.assertRaisesRegex(ValueError, 'More than one'):
            session.find('orb')
        with self.assertRaisesRegex(ValueError, 'More than one'):
            session.delete_run('orb')

    def test_restore_does_not_adopt_workers_from_other_projects(self):
        session_index.discover(self.home)
        d = self.old / 'project/.duo/cognitive/state.json'
        state = json.loads(d.read_text())
        state['status'] = 'running'
        d.write_text(json.dumps(state))
        before = d.read_bytes()
        engine = Engine.__new__(Engine)
        with mock.patch.object(session, 'find') as find:
            engine._restore()
        find.assert_not_called()
        self.assertIsNone(engine.session)
        self.assertEqual(d.read_bytes(), before)

    def test_sidebar_api_and_saved_view_do_not_replace_current_run(self):
        session_index.discover(self.home)
        engine = Engine.__new__(Engine)
        current = engine.session = mock.Mock(st={'id':'selected','status':'stopped'})
        saved = mock.Mock()
        saved.view.return_value = {'id': 'cognitive', 'status': 'stopped'}
        srv = make_server(0, engine)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_address[1]
            code, data = call(port, '/api/sessions')
            self.assertEqual(code, 200)
            self.assertEqual([x['id'] for x in data], ['orb', 'cognitive'])
            with mock.patch.object(session, 'load', return_value=saved):
                code, data = call(port, '/api/session?id=cognitive')
            self.assertEqual((code, data['id']), (200, 'cognitive'))
            self.assertIs(engine.session, current)
            current.control.assert_not_called()
            saved.resume.assert_not_called()
        finally:
            srv.shutdown()
            srv.server_close()

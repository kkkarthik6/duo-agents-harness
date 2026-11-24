"""Project-local, verified context snapshots for subsequent runs."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import broker, project_handoff as handoff


class ProjectHandoffTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    def completed(self,name='old',created='2026-11-19T12:00:00',layout='.duo',report=True):
        folder=self.root/layout/name;folder.mkdir(parents=True)
        state=dict(id=name,workspace=str(self.root),task='Old request '+name,title=name,status='done',created=created,entries=[])
        (folder/'state.json').write_text(json.dumps(state))
        broker.ensure(folder,'C',name,state['task'])
        if report:
            summary=self.root/(name+'.md');summary.write_text('Historical work. '*1500+name+'_TAIL')
            broker.event(folder,'completion_summary',status='ready',path=str(summary),sha256=hashlib.sha256(summary.read_bytes()).hexdigest(),version=1)
        return folder,state

    def test_snapshot_survives_pointer_and_source_changes_without_truncation(self):
        folder,_=self.completed();state={'id':'new','task':'Current goal'}
        record=handoff.attach(self.root,self.root/'.duo/new',state)
        (self.root/'.duo/latest_context.json').write_text('{"run_id":"new"}')
        self.assertEqual(record['run_id'],'old');self.assertEqual(record['kind'],'detailed')
        original=Path(record['snapshot']).read_text();(self.root/'old.md').write_text('changed')
        prompt=handoff.prompt(state)
        self.assertIn('old_TAIL',prompt);self.assertIn('current user request controls scope',prompt)
        self.assertEqual(Path(record['snapshot']).read_text(),original)
        self.assertEqual(handoff.attach(self.root,self.root/'.duo/new',state),record)
        Path(record['snapshot']).write_text('tampered')
        with self.assertRaises(ValueError):handoff.prompt(state)

    def test_newest_completed_same_physical_project_and_both_layouts(self):
        self.completed('old');self.completed('newer','2026-11-19T13:00:00','duo/runs')
        wrong,state=self.completed('foreign','2026-11-19T15:00:00');state['workspace']=str(self.root/'different')
        (wrong/'state.json').write_text(json.dumps(state))
        active,state=self.completed('active','2026-11-19T16:00:00');state['status']='running'
        (active/'state.json').write_text(json.dumps(state))
        self.assertEqual(handoff.select(self.root)['run_id'],'newer')
        self.assertEqual(handoff.select(self.root,exclude='newer')['run_id'],'old')

    def test_missing_changed_or_invalid_summary_has_labelled_fallback(self):
        folder,_=self.completed('old')
        latest,_=self.completed('newer','2026-11-19T13:00:00',report=False)
        self.assertEqual(handoff.select(self.root)['kind'],'saved-state')
        record=handoff.attach(self.root,self.root/'.duo/new',{'id':'new'})
        self.assertEqual(record['run_id'],'newer')
        self.assertIn('saved-state handoff',Path(record['snapshot']).read_text())
        path=self.root/'newer.md';path.write_bytes(b'\xff')
        broker.event(latest,'completion_summary',status='ready',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(handoff.select(self.root)['kind'],'saved-state')
        path.write_text('unverified change')
        self.assertEqual(handoff.select(self.root)['kind'],'saved-state')

    def test_no_prior_run_does_not_create_context(self):
        target=self.root/'.duo/new'
        self.assertIsNone(handoff.attach(self.root,target,{'id':'new'}));self.assertFalse(target.exists())


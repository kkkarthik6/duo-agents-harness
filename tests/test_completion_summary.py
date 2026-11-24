import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from engine import broker, completion_summary as summary, config


class CompletionSummaryTests(unittest.TestCase):
    def setUp(self):
        auto=mock.patch.dict(config.CFG,auto_completion_summary=True); auto.start(); self.addCleanup(auto.stop)
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.run=self.root/'.duo/example'
        self.run.mkdir(parents=True)
        self.state={'id':'example','title':'Example report','task':'Produce the requested report.',
                    'status':'done','detail':'Reviewed output saved.','agents':['claude','codex'],'verify_passed':True}
        self.save_state()
        broker.ensure(self.run,'C','Report','Produce the requested report.')
        self.report='# Detailed handoff\n\n## User action items\n\n1. Review the saved report.\n\n## Suggested next-run prompt\n\nExtend the report from its saved evidence.\n'+'Full prose. '*1000+'TAIL_RETAINED'

    def test_the_summary_is_written_only_when_asked_by_default(self):
        with mock.patch.dict(config.CFG,auto_completion_summary=False), mock.patch.object(summary.helper,'run_cli') as writer:
            self.assertEqual(summary.request(self.run,self.root,self.state)['status'],'on_request')
            writer.assert_not_called()

    def save_state(self):
        (self.run/'state.json').write_text(json.dumps(self.state))

    def wait(self):
        for _ in range(300):
            if str(self.run.resolve()) not in summary.RUNNING: return
            time.sleep(.01)
        self.fail('Summary did not finish')

    def test_done_stage_medium_model_versions_full_text_and_next_run(self):
        with mock.patch.dict(config.CFG,completion_summary_provider='auto'), mock.patch.object(summary.helper,'run_cli',return_value=(True,self.report)) as writer:
            summary.request(self.run,self.root,self.state); self.wait()
            row=summary.latest(self.run)
            self.assertEqual(row['status'],'ready')
            self.assertEqual(writer.call_args.kwargs['model'],'claude-sonnet-5-5')
            self.assertEqual(Path(row['path']).read_text(),self.report)
            self.assertIn('TAIL_RETAINED',Path(row['path']).read_text())
            self.assertIn(row['path'],Path(row['next_run']).read_text())
            self.assertEqual(self.state['status'],'done')
            summary.request(self.run,self.root,self.state); self.wait()
            self.assertEqual(writer.call_count,1)
            broker.event(self.run,'progress','C',text='Later contextual note')
            summary.request(self.run,self.root,self.state); self.wait()
            self.assertEqual(writer.call_count,1)
            summary.request(self.run,self.root,self.state,force=True); self.wait()
            second=summary.latest(self.run)
            self.assertEqual(second['version'],2)
            self.assertTrue(Path(row['path']).is_file())
            self.assertEqual(broker.view(self.run)['completion_summary']['version'],2)
            self.assertIn('User action items',summary.prompt(Path(row['sources'])))

    def test_provider_fallback_failure_and_retry_preserve_done_and_previous(self):
        with mock.patch.dict(config.CFG,completion_summary_provider='auto'), mock.patch.object(summary.helper,'run_cli',side_effect=[(False,'RAW_PROVIDER_ERROR'),(True,self.report)]) as writer:
            summary.request(self.run,self.root,self.state);self.wait()
            self.assertEqual(summary.latest(self.run)['model'],'gpt-6.1-sol')
            self.assertEqual(writer.call_args.kwargs['model'],'gpt-6.1-sol')
        with mock.patch.object(summary.helper,'run_cli',return_value=(False,'RAW_FAILURE_TAIL')):
            summary.request(self.run,self.root,self.state,force=True);self.wait()
        row=summary.latest(self.run)
        self.assertEqual(row['status'],'unavailable');self.assertTrue(Path(row['previous']['path']).exists())
        self.assertEqual(self.state['status'],'done')
        self.assertTrue(any(r['message']=='RAW_FAILURE_TAIL' for r in broker.errors(self.run)))
        self.assertNotIn('RAW_FAILURE_TAIL',Path(row['previous']['path']).read_text())

    def test_not_done_coalesces_and_stale_result_is_not_published(self):
        self.state['status']='running'
        with mock.patch.object(summary.helper,'run_cli') as writer:
            self.assertEqual(summary.request(self.run,self.root,self.state)['status'],'not_done')
            writer.assert_not_called()
        self.state['status']='done';self.save_state()
        started,release=threading.Event(),threading.Event()
        def generate(*args,**kw):
            started.set();release.wait(2);return True,self.report
        with mock.patch.object(summary.helper,'run_cli',side_effect=generate) as writer:
            summary.request(self.run,self.root,self.state);self.assertTrue(started.wait(1))
            summary.request(self.run,self.root,self.state)
            self.state['status']='running';self.save_state();release.set();self.wait()
            self.assertEqual(writer.call_count,1)
        self.assertEqual(summary.latest(self.run)['status'],'superseded')

    def test_restart_exposes_retry_and_preserves_source_snapshot(self):
        broker.event(self.run,'completion_summary',status='writing',process_id=99999999,signature='old')
        with mock.patch.object(summary.os,'kill',side_effect=ProcessLookupError):
            self.assertEqual(summary.latest(self.run)['status'],'interrupted')

    def test_changed_output_invalidates_source_version(self):
        product=self.root/'report.md'; product.write_text('old')
        broker.event(self.run,'checkpoint','C',outputs=[broker.artifact(self.root,'report.md')])
        first=summary.signature(summary.capture(self.run,self.root,self.state))
        product.write_text('changed content')
        self.assertNotEqual(first,summary.signature(summary.capture(self.run,self.root,self.state)))


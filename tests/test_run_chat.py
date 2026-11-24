"""Run conversation must never mutate or resume execution state."""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine import broker, helper, run_chat
from engine.session import Session


class RunChatTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.run=self.root/'.duo/completed';self.run.mkdir(parents=True)
        self.state=dict(id='completed',task='Make a report',status='done',phase='done',agents=['claude','codex'],roles={'builder':'codex'},entries=[])
        self.state_file=self.run/'state.json';self.state_file.write_text(json.dumps(self.state));self.original=self.state_file.read_bytes()
        broker.ensure(self.run,'C','Report','Make a report')

    def wait(self):
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            with run_chat.GUARD:
                if str(self.run.resolve()) not in run_chat.RUNNING:return
            time.sleep(.005)
        self.fail('Conversation did not finish')

    def summary(self):
        path=self.root/'summary.md';path.write_text('Full summary. '*1500+'SUMMARY_TAIL')
        broker.event(self.run,'completion_summary',status='ready',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return path

    def test_saved_session_chat_preserves_done_and_full_history(self):
        self.summary()
        self.state.update(workspace=str(self.root),duo_dir=str(self.run))
        session=Session.__new__(Session);session.st=self.state
        with mock.patch.object(helper,'run_cli',side_effect=[(True,'First answer'),(True,'Second answer')]) as cli, mock.patch.object(Session,'resume') as resume:
            session.ask('What did you deliver?',request_id='one');self.wait()
            session.ask('Explain that answer.',request_id='two');self.wait()
        resume.assert_not_called()
        self.assertEqual(self.state_file.read_bytes(),self.original)
        self.assertEqual(self.state['status'],'done')
        self.assertEqual(len(run_chat.history(self.run)),2)
        prompt=cli.call_args.args[2]
        for text in ('SUMMARY_TAIL','First answer','What did you deliver?','Explain that answer.'):
            self.assertIn(text,prompt)
        self.assertEqual(cli.call_args.args[0],'codex')
        self.assertTrue(cli.call_args.kwargs['read_only'])
        self.assertEqual(session.chat()[-1]['a'],'Second answer')

    def test_concurrent_submissions_are_serial_and_idempotent(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def answer(agent,root,prompt,**kw):
            calls.append(prompt)
            if len(calls)==1:entered.set();release.wait(2)
            return True,'answer-'+str(len(calls))
        with mock.patch.object(helper,'run_cli',side_effect=answer):
            run_chat.request(self.run,self.root,self.state,'first',request_id='a');self.assertTrue(entered.wait(1))
            run_chat.request(self.run,self.root,self.state,'first',request_id='a')
            threads=[threading.Thread(target=run_chat.request,args=(self.run,self.root,self.state,'next-'+str(i)),kwargs={'request_id':'b'+str(i)}) for i in range(4)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(1)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            release.set();self.wait()
        rows=run_chat.history(self.run)
        self.assertEqual(len(rows),5);self.assertEqual(len(calls),5)
        self.assertTrue(all(row['status']=='answered' for row in rows))
        self.assertIn('answer-1',calls[1]);self.assertNotIn('next-',calls[0])
        self.assertEqual(self.state_file.read_bytes(),self.original)

    def test_failure_restart_and_retry_use_original_context(self):
        with mock.patch.object(helper,'run_cli',return_value=(False,'provider unavailable')):
            run_chat.request(self.run,self.root,self.state,'Explain',request_id='q');self.wait()
        row=run_chat.history(self.run)[0];self.assertEqual(row['status'],'failed')
        path=self.run/'conversation/messages.json';rows=json.loads(path.read_text())
        rows[0].update(status='working',owner='previous-process');path.write_text(json.dumps(rows))
        self.assertEqual(run_chat.history(self.run)[0]['status'],'interrupted')
        self.state.update(task='A different task',status='running')
        with mock.patch.object(helper,'run_cli',return_value=(True,'Recovered')) as cli:
            run_chat.request(self.run,self.root,self.state,retry_id='q');self.wait()
        self.assertIn('Make a report',cli.call_args.args[2]);self.assertNotIn('A different task',cli.call_args.args[2])
        self.assertEqual(run_chat.history(self.run)[0]['a'],'Recovered')
        self.assertEqual(self.state_file.read_bytes(),self.original)

    def test_question_context_is_captured_before_background_answer(self):
        with mock.patch.object(threading.Thread,'start'):
            row=run_chat.request(self.run,self.root,self.state,'Why?',request_id='snapshot')
        with run_chat.GUARD:run_chat.RUNNING.discard(str(self.run.resolve()))
        self.state['task']='changed'
        self.assertEqual(json.loads(Path(row['context_file']).read_text())['task'],'Make a report')
        self.assertEqual(self.state_file.read_bytes(),self.original)

    def test_native_read_only_flags_survive_command_guard(self):
        for provider in ('claude','codex'):
            with self.subTest(provider=provider),mock.patch.object(helper.shutil,'which',return_value='/bin/'+provider),mock.patch.object(helper.subprocess,'run',return_value=SimpleNamespace(stdout='ok',stderr='',returncode=0)) as child:
                ok,_=helper.run_cli(provider,str(self.root),'QUESTION',read_only=True)
                self.assertTrue(ok);argv=child.call_args.args[0]
                if provider=='codex':
                    self.assertEqual(argv[argv.index('--sandbox')+1],'read-only')
                    self.assertIn('--skip-git-repo-check',argv)
                    self.assertEqual(argv[argv.index('-c')+1],'history.persistence="none"')
                else:
                    self.assertEqual(argv[argv.index('--tools')+1],'Read,Glob,Grep')
                    self.assertEqual(argv[argv.index('--permission-mode')+1],'dontAsk')
                    self.assertEqual(argv[-2:],['--','QUESTION'])
                self.assertIn('QUESTION',argv)

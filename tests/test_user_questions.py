import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from engine import broker, server, session


class UserQuestionTests(unittest.TestCase):
    def test_stopped_run_can_record_a_task_answer_without_restarting(self):
        with tempfile.TemporaryDirectory() as folder:
            broker.ensure(folder,'C','Task','Finish it.')
            question=broker.ask(folder,'C','Which source?')
            target=SimpleNamespace(st={'id':'saved','status':'stopped'},dir=Path(folder),human_message=mock.Mock(),save=mock.Mock(),control=mock.Mock())
            engine=server.Engine.__new__(server.Engine);engine.session=None;engine.sessions={}
            with mock.patch.object(server,'ThreadingHTTPServer') as http:
                server.make_server(0,engine);cls=http.call_args.args[1]
            handler=object.__new__(cls);handler.path='/api/question-answer';handler.headers={'Content-Type':'application/json'}
            handler._trusted=lambda:True;handler._body=lambda:{'id':'saved','kind':'task','question_id':question['question_id'],'text':'Source B'};handler._send=mock.Mock()
            with mock.patch.object(server.session,'find',return_value=target):handler.do_POST()
            self.assertEqual(handler._send.call_args.args[0],200)
            self.assertEqual(broker.questions(folder)[0]['answer'],'Source B')
            target.control.assert_not_called()
            self.assertIsNone(engine.session)

    def test_saved_markdown_decisions_surface_once_with_recorded_recommendations(self):
        text='## User decisions (unresolved)\n\n- **D1.** Which paper?\n  - (a) Methods paper\n  - (b) Evaluation paper\n- **D2.** May training run? The default is no training.\n\n## Other work\nIgnore this.'
        with tempfile.TemporaryDirectory() as folder:
            broker.ensure(folder,'C','Task','Finish it.')
            self.assertEqual(broker.import_user_questions(folder,'C',text),2)
            broker.import_user_questions(folder,'C',text)
            questions=broker.questions(folder)
            self.assertEqual(len(questions),2)
            self.assertEqual(questions[0]['options'],['Methods paper','Evaluation paper'])
            self.assertEqual(questions[0]['scope'],'run')
            self.assertEqual(questions[1]['recommendation'],'The default is no training.')
            broker.question_context(folder,questions[0]['question_id'],recommendation='Evaluation first',reason='Existing evidence is reusable.')
            self.assertEqual(broker.questions(folder)[0]['recommendation'],'Evaluation first')

    def test_question_options_free_text_history_and_repeat_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            broker.ensure(folder,'C','Task','Complete the task.')
            question=broker.ask(folder,'C','Which data source should I use?', ['Source A','Source B'])
            self.assertEqual(question['status'],'open')
            self.assertEqual(broker.ask(folder,'C',question['text'],question['options'])['question_id'],question['question_id'])
            text='Use the local source and preserve these exact words. '+'x'*2000+'ANSWER_TAIL'
            answer=broker.answer_question(folder,question['question_id'],text)
            self.assertEqual(answer['answer'],text)
            self.assertTrue(broker.answer_question(folder,question['question_id'],text)['duplicate'])
            with self.assertRaises(ValueError): broker.answer_question(folder,question['question_id'],'Different answer')
            node=broker.node(folder,'C')
            self.assertEqual(node['questions'][0]['answer'],text)
            self.assertEqual(node['status'],'ready')

    def handler(self,state,body):
        state.setdefault('status','running')
        current=SimpleNamespace(st=state,save=mock.Mock(),answer_decision=mock.Mock(),answer_wait=mock.Mock(),panes_alive=mock.Mock(return_value=True))
        engine=server.Engine.__new__(server.Engine);engine.session=current;engine.sessions={}
        with mock.patch.object(server,'ThreadingHTTPServer') as http:
            server.make_server(0,engine)
            cls=http.call_args.args[1]
        handler=object.__new__(cls);handler.path='/api/question-answer'
        handler.headers={'Content-Type':'application/json'};handler._trusted=lambda:True
        handler._body=lambda:body;handler._send=mock.Mock()
        handler.do_POST()
        return current,handler._send

    def test_stale_decisions_and_permissions_do_not_change_current_request(self):
        state={'id':'run','decisions':[{'id':'D2'}], 'pending':[{'agent':'codex','waiting':{'kind':'approval','question':'Current native request'}}]}
        for body in [
            {'id':'run','kind':'decision','question_id':'D1','choice':'O1'},
            {'id':'run','kind':'approval','agent':'codex','question':'Old native request','approve':True},
            {'id':'other','kind':'approval','agent':'codex','question':'Current native request','approve':True}]:
            current,sent=self.handler(state,body)
            self.assertEqual(sent.call_args.args[0],400)
            current.answer_decision.assert_not_called();current.answer_wait.assert_not_called()
        current,sent=self.handler(state,{'id':'run','kind':'approval','agent':'codex','question':'Current native request','approve':False})
        current.answer_wait.assert_called_once_with('codex',False)

    def test_task_answer_targets_the_worker_that_asked(self):
        current=object.__new__(session.Session)
        current.st={'entries':[],'phase':'deep','status':'running','pending':[{'agent':'claude','task_id':'A'},{'agent':'codex','task_id':'C'}],
                    'last_seen':{'claude':10,'codex':10}}
        current.save=mock.Mock()
        current.add_entry=mock.Mock(side_effect=lambda *args,**kw: current.st['entries'].append({'author':'human','kind':'message','prose':args[3],'n':2}))
        with mock.patch.object(session.threading,'Thread') as thread:
            current.human_message('Use source B.',task_id='C')
            self.assertEqual(thread.call_args.kwargs['args'][0],'codex')

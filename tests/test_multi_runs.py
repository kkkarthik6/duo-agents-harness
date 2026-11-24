"""Two independent runs: admission, routing, recovery and real terminal isolation."""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import config, session, session_index, workers
from engine.agents import Agent
from engine.server import Engine, make_server
from engine.run_pool import overlap
from tests.test_server import call
from tests.helpers.session_base import SessionBase, fake


def stub(sid, folder, status='running'):
    run = mock.Mock()
    run.st = dict(id=sid, title=sid, workspace=str(folder), duo_dir=str(folder / '.duo' / sid),
                  status=status, paused=False, pending=[], panes={}, created='2026-11-19', project=folder.name)
    run.ws = folder
    run.dir = Path(run.st['duo_dir'])
    run.view.side_effect = lambda: dict(run.st)
    return run


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.enterContext(mock.patch.object(config, 'DUO_HOME', self.root / 'Duo'))
        self.enterContext(mock.patch.dict(config.CFG, root=str(self.root), max_active_runs=2))
        self.a = stub('a', self.root / 'Alpha')
        self.b = stub('b', self.root / 'Beta')
        self.engine = Engine.__new__(Engine)
        self.engine.session = self.a
        self.engine.sessions = {'a':self.a, 'b':self.b}
        self.engine.agents = []

    def test_two_runs_tick_and_one_failure_does_not_stop_other(self):
        self.a.tick.side_effect = ValueError('one broken run')
        self.engine.tick_runs()
        self.a.record_error.assert_called_once()
        self.b.tick.assert_called_once()
        self.b.record_error.assert_not_called()

    def test_successful_dispatch_archives_the_old_error(self):
        self.a.st['error']={'message':'File name too long','at':'17:36'}
        self.a.tick.side_effect=lambda:self.a.st['pending'].append({'agent':'claude'})
        self.engine.tick_runs()
        self.assertNotIn('error',self.a.st)
        self.assertEqual(self.a.st['recovered_errors'][0]['message'],'File name too long')
        self.a.save.assert_called_once()

    def test_selection_does_not_restart_or_stop_either_run(self):
        self.engine.select_run('b')
        self.assertIs(self.engine.session, self.b)
        for run in [self.a,self.b]:
            run.resume.assert_not_called()
            run.control.assert_not_called()
        self.engine.continue_run('a')
        self.a.resume.assert_not_called()

    def test_continue_resumes_attached_paused_or_failed_runs(self):
        for status, paused, stop in (('waiting', True, {'rule': 'Goal assessment invalid'}),
                                     ('paused', True, None), ('paused_decision', False, None),
                                     ('waiting', False, {'rule': 'Worker failed'})):
            self.a.st.update(status=status, paused=paused, stop=stop)
            self.a.resume.reset_mock()
            self.engine.continue_run('a')
            self.a.resume.assert_called_once_with()
            self.b.resume.assert_not_called()

    def test_continue_does_not_relaunch_an_attached_waiting_live_job(self):
        self.a.st.update(status='waiting', paused=False, pending=[{'agent': 'codex'}])
        self.engine.continue_run('a')
        self.a.resume.assert_not_called()

    def test_four_separate_projects_are_allowed_and_fifth_is_refused(self):
        with mock.patch.dict(config.CFG, max_active_runs=4):
            self.engine.ensure_room(self.root/'Gamma')
            self.engine.sessions['c'] = stub('c', self.root/'Gamma')
            self.engine.ensure_room(self.root/'Delta')
            self.engine.sessions['d'] = stub('d', self.root/'Delta')
            with self.assertRaisesRegex(ValueError, 'limit'):
                self.engine.ensure_room(self.root/'Epsilon')
            with self.assertRaisesRegex(ValueError, 'project'):
                self.engine.ensure_room(self.root/'Alpha')
        self.assertEqual(len(self.engine.sessions), 4)

    def test_third_run_and_force_cannot_replace_existing_runs(self):
        with self.assertRaisesRegex(ValueError, 'limit'):
            self.engine.start({'task':'third', 'workspace':str(self.root/'Gamma'), 'force':True})
        self.a.control.assert_not_called()
        self.b.control.assert_not_called()

    def test_same_project_nested_path_and_symlink_are_blocked(self):
        self.a.ws.mkdir()
        alias=self.root/'alias';alias.symlink_to(self.a.ws, target_is_directory=True)
        for path in [self.a.ws, self.a.ws/'nested', alias]:
            with self.assertRaisesRegex(ValueError, 'project'):
                self.engine.ensure_room(path)
        self.assertTrue(overlap(self.a.ws,alias))

    def test_two_subfolders_of_same_git_project_conflict(self):
        root=self.root/'repo';(root/'.git').mkdir(parents=True)
        self.assertTrue(overlap(root/'one',root/'two'))

    def test_missing_or_unknown_run_id_cannot_change_selected_run(self):
        with self.assertRaisesRegex(ValueError, 'Name the run'):
            self.engine.request_run()
        with self.assertRaisesRegex(ValueError, 'not attached'):
            self.engine.request_run('missing')
        self.assertIs(self.engine.request_run('b'),self.b)

    def test_single_run_requests_remain_compatible(self):
        self.engine.sessions.pop('b')
        self.assertIs(self.engine.request_run(),self.a)
        self.engine.ensure_room(self.b.ws)

    def test_limit_one_preserves_existing_runs_but_blocks_new_start(self):
        with mock.patch.dict(config.CFG,max_active_runs=1):
            with self.assertRaisesRegex(ValueError,'limit'):
                self.engine.ensure_room(self.root/'Gamma')
        self.assertEqual(self.a.st['status'],'running')
        self.assertEqual(self.b.st['status'],'running')

    def test_detached_job_holds_project_after_worker_stops(self):
        self.engine.sessions={'a':self.a};self.a.st['status']='stopped'
        jobs=self.a.dir/'jobs';jobs.mkdir(parents=True)
        (jobs/'task.json').write_text(json.dumps({'jobs':[{'pid':123,'identity':'123@start'}]}))
        with mock.patch('engine.jobs.process_table',return_value={123:{'identity':'123@start','state':'S'}}):
            with self.assertRaisesRegex(ValueError,'project'):
                self.engine.ensure_room(self.a.ws)
            self.engine.ensure_room(self.b.ws)

    def test_reused_job_pid_does_not_reserve_completed_work(self):
        self.a.st['status']='stopped';self.b.st['status']='stopped'
        jobs=self.a.dir/'jobs';jobs.mkdir(parents=True)
        (jobs/'task.json').write_text(json.dumps({'jobs':[{'pid':123,'identity':'123@old'}]}))
        with mock.patch('engine.jobs.process_table',return_value={123:{'identity':'123@new','state':'S'}}):
            self.engine.ensure_room(self.a.ws)


    def test_running_gate_keeps_stopped_project_reserved(self):
        self.a.st.update(status='stopped',gate={'running':True})
        with self.assertRaisesRegex(ValueError,'project'):
            self.engine.ensure_room(self.a.ws)

    def test_native_answer_requires_attached_workers(self):
        self.b.st['status']='stopped'
        srv=make_server(0,self.engine)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        try:
            code,_=call(srv.server_address[1],'/api/answer',{'run_id':'b','agent':'claude','approve':True})
            self.assertEqual(code,400)
            self.b.answer_wait.assert_not_called()
        finally:
            srv.shutdown();srv.server_close()

    def test_delete_preserves_history_while_detached_job_uses_it(self):
        self.a.st['status']='stopped'
        jobs=self.a.dir/'jobs';jobs.mkdir(parents=True)
        (jobs/'task.json').write_text(json.dumps({'jobs':[{'pid':123,'identity':'123@start'}]}))
        srv=make_server(0,self.engine)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        try:
            with mock.patch('engine.session.delete_run') as delete, mock.patch('engine.jobs.process_table',return_value={123:{'identity':'123@start','state':'S'}}):
                code,_=call(srv.server_address[1],'/api/delete',{'id':'a'})
            self.assertEqual(code,400)
            delete.assert_not_called()
        finally:
            srv.shutdown();srv.server_close()

    def test_run_owned_worker_check_rejects_other_run(self):
        self.a.st.update(worker_session='duo-run-a',panes={'claude':'%0'})
        with mock.patch('engine.workers.tmux',return_value='b\n') as tmux:
            self.assertFalse(workers.alive(self.a.st))
            workers.close(self.a.st)
        self.assertFalse(any(c.args[0]=='kill-session' for c in tmux.call_args_list))

    def test_late_delivery_cannot_reach_reused_pane(self):
        state=dict(self.a.st,worker_session='old',worker_generation='old',panes={'claude':'%0'})
        run=object.__new__(session.Session);run.st=state
        with mock.patch('engine.workers.owned',return_value=False), mock.patch('engine.session.send_text') as send:
            run._nudge('claude','late user message')
        send.assert_not_called()

    def test_api_targets_nonselected_run_and_rejects_ambiguous_requests(self):
        srv=make_server(0,self.engine)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        try:
            port=srv.server_address[1]
            self.assertEqual(call(port,'/api/control',{'action':'pause'})[0],400)
            self.assertEqual(call(port,'/api/control',{'run_id':'b','action':'pause'})[0],200)
            self.b.control.assert_called_once_with('pause')
            self.a.control.assert_not_called()
            self.assertEqual(call(port,'/api/message',{'run_id':'missing','text':'hello'})[0],400)
            self.a.human_message.assert_not_called()
            self.assertEqual(call(port,'/api/select',{'id':'b'})[0],200)
            self.assertIs(self.engine.session,self.b)
        finally:
            srv.shutdown();srv.server_close()

    def test_engine_reload_uses_clean_imports_and_keeps_port(self):
        from engine.server import replace_engine
        target=self.root/'new-core'
        with mock.patch('engine.coremgr.current_dir',return_value=target), mock.patch('os.chdir') as chdir, mock.patch('os.execve') as execute:
            replace_engine(9123,self.root/'Duo')
        chdir.assert_called_once_with(target)
        self.assertEqual(execute.call_args.args[1][1:],['-m','engine','serve','9123'])
        self.assertEqual(execute.call_args.args[2]['PYTHONPATH'],str(target))


@unittest.skipUnless(__import__('shutil').which('tmux'),'tmux required')
class TerminalIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.socket='duo-isolation-'+str(os.getpid())
        self.addCleanup(lambda:subprocess.run(['tmux','-L',self.socket,'kill-server'],capture_output=True))
        def tmux(*args,check=True):
            result=subprocess.run(['tmux','-L',self.socket,*args],capture_output=True,text=True,timeout=10)
            if check and result.returncode:
                raise RuntimeError(result.stderr)
            return result.stdout
        self.tmux=tmux
        for target in ['engine.tmux.tmux','engine.session.tmux','engine.workers.tmux']:
            self.enterContext(mock.patch(target,side_effect=tmux))
        self.enterContext(mock.patch.object(config,'DUO_HOME',self.root/'Duo'))
        self.enterContext(mock.patch.dict(config.CFG,root=str(self.root/'Projects'),max_active_runs=2,
                         reference_research=False,submit_delay=.02,summary_agent_enabled=False))
        self.enterContext(mock.patch('engine.agents.launch_command',side_effect=lambda a,c:c))
        self.enterContext(mock.patch('engine.agents.guarded_command',side_effect=lambda a,c,*args:c))
        self.enterContext(mock.patch('engine.trust.trust_folder'))
        self.enterContext(mock.patch('engine.sandbox.stop_all'))
        self.engine=Engine(agents=[Agent('claude','Claude','/bin/cat','/bin/cat','test'),
                                   Agent('codex','Codex','/bin/cat','/bin/cat','test')],tick=False)

    @mock.patch.dict(config.CFG, {"triage": "full"})   # this test needs both agents started
    def test_two_real_tmux_sessions_stop_restart_and_recover_independently(self):
        a=self.engine.start({'task':'First task','project_name':'Alpha'})
        b=self.engine.start({'task':'Second task','project_name':'Beta'})
        self.assertNotEqual(a.st['worker_session'],b.st['worker_session'])
        self.assertNotEqual(a.st['id'],b.st['id'])
        self.assertNotEqual(a.st['duo_dir'],b.st['duo_dir'])
        self.assertTrue(a.panes_alive());self.assertTrue(b.panes_alive())
        panes_before=dict(a.st['panes'])
        b.restart_agent('codex')
        self.assertEqual(a.st['panes'],panes_before)
        self.assertTrue(a.panes_alive())
        # A second broker reconnects both sessions without launching replacements.
        other=Engine(agents=[],tick=False)
        self.assertEqual(set(other.sessions),{a.st['id'],b.st['id']})
        self.assertEqual(other.sessions[a.st['id']].st['panes'],a.st['panes'])
        srv=make_server(0,self.engine)
        threading.Thread(target=srv.serve_forever,daemon=True).start()
        try:
            port=srv.server_address[1]
            self.assertEqual(call(port,'/api/keys',{'run_id':a.st['id'],'agent':'claude','text':'ONLY_ALPHA'})[0],200)
            time.sleep(.1)
            screen_a=self.tmux('capture-pane','-p','-t',a.st['panes']['claude'])
            screen_b=self.tmux('capture-pane','-p','-t',b.st['panes']['claude'])
            self.assertIn('ONLY_ALPHA',screen_a);self.assertNotIn('ONLY_ALPHA',screen_b)
            self.assertEqual(call(port,'/api/control',{'run_id':a.st['id'],'action':'stop'})[0],200)
            self.assertFalse(a.panes_alive());self.assertTrue(b.panes_alive())
            self.assertEqual(call(port,'/api/keys',{'run_id':a.st['id'],'agent':'claude','text':'LATE'})[0],400)
            self.assertTrue(b.panes_alive())
        finally:
            srv.shutdown();srv.server_close()
        self.engine.server=None
        self.engine.quit()
        self.assertFalse(b.panes_alive())

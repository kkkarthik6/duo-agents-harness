"""Receipts, idle recovery, sender leases, and complete-worker handoffs."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from tests.helpers.session_state import make_session
from engine import delivery, session, config
from engine import pane


class ReceiptTests(unittest.TestCase):
    def test_repair_keeps_task_id_but_cannot_reuse_the_original_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            p={'task_id':'T1'}; first=delivery.prepare(p,'acks/0003.txt',1)
            root=Path(directory);(root/'acks').mkdir();(root/first).write_text('T1')
            self.assertTrue(delivery.acknowledged(root,p))
            second=delivery.prepare(p,'acks/0003.txt',2)
            self.assertNotEqual(first,second);self.assertEqual(p['task_id'],'T1')
            self.assertFalse(delivery.acknowledged(root,p))
            self.assertTrue((root/first).exists())

    def test_saved_old_assignment_migrates_prompt_and_preserves_output(self):
        p={'task_id':'T1','ack':'acks/3.txt','prompt':'Write T1 to acks/3.txt',
           'wire_prompt':'Write T1 to acks/3.txt','resends':3,'acknowledged_at':1}
        self.assertTrue(delivery.migrate(p,100))
        self.assertIn(p['ack'],p['prompt']);self.assertIn(p['ack'],p['wire_prompt'])
        self.assertNotIn('acknowledged_at',p);self.assertEqual(p['resends'],0)
        self.assertFalse(delivery.migrate(p,101))

    def test_idle_success_without_reply_retries_then_stops(self):
        p={'issued':0,'sent_at':0}
        self.assertIsNone(delivery.recovery(p,100,True,True))
        self.assertEqual(delivery.recovery(p,146,True,True),'retry')
        delivery.retry(p,146)
        self.assertIsNone(delivery.recovery(p,150,True,True))
        p['resends']=3
        self.assertEqual(delivery.recovery(p,200,True,True),'exhausted')

    def test_busy_worker_and_live_sender_do_not_receive_duplicate_assignments(self):
        p={'issued':0,'idle_since':1}
        self.assertIsNone(delivery.recovery(p,1000,False,False));self.assertNotIn('idle_since',p)
        p.update(delivery_inflight_at=1000,delivery_owner=delivery.RUNTIME_ID)
        self.assertIsNone(delivery.recovery(p,1001,True,False))
        self.assertEqual(delivery.recovery(p,1091,True,False),'stuck_sender')

    def test_restart_discards_a_sender_lease_from_the_old_engine(self):
        p={'issued':0,'delivery_inflight_at':1,'delivery_owner':'old-engine','delivery_lease':'old'}
        self.assertEqual(delivery.recovery(p,100,True,False),'retry')
        self.assertNotIn('delivery_inflight_at',p)

    def test_old_approval_text_does_not_hide_completed_worker_prompt(self):
        screen='Duo agentic worker: ready\nDo you want to approve this?\nDUO_WORKER_RESULT:success\n❯ '
        self.assertTrue(pane.idle(screen,'claude'))
        busy=screen+'DUO assignment: ...\nWorking on assignment...'
        self.assertFalse(pane.idle(busy,'claude'))
        real='Do you want to approve this?\n> 1. Yes\n2. No'
        self.assertFalse(pane.idle(real,'claude'))




class EngineFailureTests(unittest.TestCase):
    def test_three_repeated_tick_errors_pause_once(self):
        s=session.Session.__new__(session.Session);s.st={}
        s.save=mock.Mock();s._pause=mock.Mock()
        with mock.patch('engine.session.notify.send') as notification:
            for _ in range(4):s.record_error(RuntimeError('broken state'))
        s._pause.assert_called_once();notification.assert_called_once()

    def test_different_error_resets_the_repeat_count(self):
        s=session.Session.__new__(session.Session);s.st={};s.save=mock.Mock();s._pause=mock.Mock()
        s.record_error(RuntimeError('first'));s.record_error(RuntimeError('second'))
        self.assertEqual(s.st['tick_failure']['count'],1);s._pause.assert_not_called()

class LegacySenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);d=root/'.duo/run';d.mkdir(parents=True)
        self.p={'n':3,'agent':'claude','kind':'diverge','task_id':'T1','prompt':'DUO turn #3. Repair this reply.',
                'ack':'.duo/run/acks/3.txt','issued_at':0,'file':'replies/fix.md'}
        self.s=session.Session({'workspace':str(root),'duo_dir':str(d),'pending':[self.p],
                                'panes':{'claude':'%1'},'model_now':{'claude':'selected'},'paused':False,'status':'running'})
        self.s.save=mock.Mock()
        # The sender only delivers to a worker the run owns; ownership comes from a tagged tmux session.
        owned=mock.patch('engine.workers.owned',return_value=True);owned.start();self.addCleanup(owned.stop)

    def test_busy_send_failure_is_recorded_and_lease_released(self):
        with mock.patch('engine.pane.wait_idle',return_value=(False,'busy')),mock.patch('engine.session.send_text') as send:
            self.s._nudge('claude',self.p['prompt'],'selected')
        send.assert_not_called();self.assertIn('input is not ready',self.p['delivery_error'])
        self.assertNotIn('delivery_inflight_at',self.p)
        self.assertNotIn('sent_at',self.p)

    def test_second_sender_cannot_overlap_an_existing_sender(self):
        self.p.update(delivery_owner=delivery.RUNTIME_ID,delivery_inflight_at=1)
        with mock.patch('engine.pane.wait_idle') as wait,mock.patch('engine.session.send_text') as send:
            self.s._nudge('claude',self.p['prompt'],'selected')
        wait.assert_not_called();send.assert_not_called()

    def test_superseded_pending_turn_cannot_send_to_the_worker(self):
        def replace(*args):
            self.s.st['pending']=[]
            return True,'❯'
        with mock.patch('engine.pane.wait_idle',side_effect=replace),mock.patch('engine.session.send_text') as send:
            self.s._nudge('claude',self.p['prompt'],'selected')
        send.assert_not_called()

    def test_success_records_delivery_and_writes_an_attempt_specific_assignment(self):
        with mock.patch('engine.pane.wait_idle',return_value=(True,'❯')),mock.patch('engine.session.send_text') as send, \
             mock.patch('engine.session.capture',return_value='❯'),mock.patch('engine.session.time.sleep'),mock.patch('engine.session.send_key'):
            self.s._nudge('claude',self.p['prompt'],'selected')
        path=self.s.dir/'assignments'/('0003-'+self.p['delivery_id']+'.md')
        self.assertTrue(path.is_file());self.assertIn(self.p['delivery_id'],send.call_args.args[1])
        self.assertIn('sent_at',self.p);self.assertNotIn('delivery_error',self.p)
        self.assertNotIn('delivery_inflight_at',self.p)

    def test_review_assignment_names_the_file_inside_the_allowed_packet(self):
        packet=self.s.dir/'research-reviews'/'3'
        self.p['review_root']=str(packet)
        with mock.patch('engine.pane.wait_idle',return_value=(True,'❯')),mock.patch('engine.session.send_text') as send, \
             mock.patch('engine.session.capture',return_value='❯'),mock.patch('engine.session.time.sleep'),mock.patch('engine.session.send_key'):
            self.s._nudge('claude',self.p['prompt'],'selected')
        path=packet/self.s.rel/'assignments'/('0003-'+self.p['delivery_id']+'.md')
        self.assertTrue(path.is_file())
        self.assertIn(str(path.resolve()),send.call_args.args[1])
        self.assertFalse((self.s.dir/'assignments'/path.name).exists())

class V3AssignmentPathTests(unittest.TestCase):
    def test_review_pointer_uses_the_packet_absolute_path(self):
        from engine.panes import TmuxDriver
        with tempfile.TemporaryDirectory() as directory:
            driver=TmuxDriver();driver.panes={'claude':'%2'}
            driver.folders={'claude':Path(directory)/'research-reviews'/'7'}
            with mock.patch('engine.pane.wait_idle',return_value=(True,'❯')), \
                 mock.patch('engine.panes.send_text') as send, \
                 mock.patch('engine.panes.time.sleep'),mock.patch('engine.panes.capture',return_value='working'):
                driver.send('claude','Review the saved research.')
            path=next((driver.folders['claude']/'duo/assignments').glob('*.md'))
            self.assertIn(str(path.resolve()),send.call_args.args[1])
            self.assertEqual(path.read_text(),'Review the saved research.')


class ResumeDeliveryTests(unittest.TestCase):
    def test_restart_clears_interrupted_and_keeps_the_assigned_model(self):
        p={'agent':'claude','prompt':'DUO turn #3. Continue.','ack':'acks/3.txt','task_id':'T1',
           'interrupted':True,'_assigned_model':'chosen-model','resends':3}
        s=make_session({'pending':[p],'renudge':['claude'],
                           'needs_context':['claude'],'protocol_sent':{'claude':False}})
        with mock.patch('engine.session.threading.Thread') as thread:s.renudge()
        self.assertNotIn('interrupted',p);self.assertEqual(p['resends'],0)
        self.assertEqual(thread.call_args.kwargs['args'][2],'chosen-model')
        self.assertNotEqual(p['ack'],'acks/3.txt')

class ExplicitPauseTests(unittest.TestCase):
    def test_user_pause_does_not_run_watchdog_or_restart_workers(self):
        s=make_session({'paused':True,'status':'paused','pending':[]})
        with mock.patch.object(s,'_tmux_alive') as alive,mock.patch.object(s,'_active_budget') as budget:
            s.tick()
        alive.assert_not_called();budget.assert_not_called()
        self.assertFalse(s._progress_watchdog())

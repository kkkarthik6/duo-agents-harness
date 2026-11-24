"""Closed-loop completion, evidence checks, and meaningful continuation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import goal_loop
from tests.goal_fixture import write_report


class GoalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_dir = self.root / '.duo/run'
        self.run_dir.mkdir(parents=True)
        (self.root / 'result.txt').write_text('Measured failure of method A.')
        self.state = dict(id='run', task='Test a method.', entries=[], goal_loop_version=1,
                          task_delivery={'outputs': [{'path': 'result.txt'}]})

    def report(self, status='continue', **changes):
        text = goal_loop.prepare(self.run_dir, self.root, self.state)
        data = write_report(self.root, text, status, ['result.txt'])
        data.update(changes)
        Path(self.state['goal']['report_file']).write_text(json.dumps(data))
        return goal_loop.consume(self.run_dir, self.root, self.state)

    def test_failed_experiment_can_supply_progress_and_a_next_action(self):
        self.assertEqual(self.report()[0], 'continue')
        self.assertEqual(self.state['goal']['status'], 'active')
        self.assertIn('selected alternative', goal_loop.continuation(self.state))

    def test_progress_continues_beyond_fixed_review_counts(self):
        for cycle in range(8):
            (self.root / 'result.txt').write_text('New discriminating experiment %d.' % cycle)
            self.assertEqual(self.report()[0], 'continue')
        self.assertEqual(len(self.state['goal']['cycles']), 8)
        self.assertEqual(self.report('achieved')[0], 'achieved')

    def test_three_unchanged_cycles_block_instead_of_claiming_success(self):
        self.assertEqual(self.report()[0], 'continue')
        for _ in range(2):
            self.assertEqual(self.report()[0], 'continue')
        self.assertEqual(self.report()[0], 'blocked')
        self.assertEqual(self.state['goal']['status'], 'blocked')

    def test_cycling_back_to_seen_results_is_not_new_progress(self):
        self.report()
        (self.root / 'result.txt').write_text('Second result')
        self.report()
        (self.root / 'result.txt').write_text('Measured failure of method A.')
        self.report()
        self.assertEqual(self.state['goal']['no_progress'], 1)

    def test_copying_old_evidence_does_not_reset_the_progress_counter(self):
        self.report()
        (self.root / 'copied-result.txt').write_text((self.root / 'result.txt').read_text())
        self.report(progress_evidence=['copied-result.txt'])
        self.assertEqual(self.state['goal']['no_progress'], 1)

    def test_achievement_expires_when_files_change_before_completion(self):
        self.assertEqual(self.report('achieved')[0], 'achieved')
        self.assertTrue(goal_loop.achievement_current(self.root, self.state))
        (self.root / 'result.txt').write_text('Changed after assessment.')
        self.assertFalse(goal_loop.achievement_current(self.root, self.state))

    def test_unmet_requirements_prevent_achievement(self):
        status, errors = self.report('achieved', criteria=[dict(requirement='Needed experiment', status='unmet')])
        self.assertIsNone(status)
        self.assertTrue(errors)

    def test_continuation_requires_questions_alternatives_and_a_next_action(self):
        for key, value in [('questions', []), ('alternatives', []), ('next_action', '')]:
            self.assertTrue(self.report(**{key: value})[1])

    def test_missing_external_and_operational_evidence_is_rejected(self):
        (self.root / 'summary.md').write_text('Only a summary.')
        for path in ('missing.txt', '../outside.txt', str(self.root / 'result.txt'), 'summary.md'):
            self.assertTrue(self.report(progress_evidence=[path])[1])

    def test_stale_or_wrong_signature_is_rejected(self):
        self.assertTrue(self.report(input_signature='old')[1])
        text = goal_loop.prepare(self.run_dir, self.root, self.state)
        write_report(self.root, text, 'achieved', ['result.txt'])
        (self.root / 'result.txt').write_text('Changed during goal review.')
        self.assertTrue(goal_loop.consume(self.run_dir, self.root, self.state)[1])

    def test_user_update_during_assessment_requires_another_check(self):
        text = goal_loop.prepare(self.run_dir, self.root, self.state)
        write_report(self.root, text, 'achieved', ['result.txt'])
        self.state['entries'].append(dict(author='human', prose='Also compare another baseline.'))
        self.assertTrue(goal_loop.consume(self.run_dir, self.root, self.state)[1])

    def test_met_criterion_allows_history_alongside_primary_evidence(self):
        reply = self.root / 'output/task-0001/step-0001/reply-0001.md'
        reply.parent.mkdir(parents=True)
        reply.write_text('Agent explanation, not acceptance evidence.')
        history = reply.relative_to(self.root).as_posix()
        criterion = dict(requirement='Test a method.', status='met', evidence=[history, 'result.txt'])
        self.assertEqual(self.report('achieved', criteria=[criterion])[0], 'achieved')
        criterion['evidence'] = [history]
        self.assertTrue(self.report('achieved', criteria=[criterion])[1])
        for extra in ('missing.txt', '../outside.txt', str(self.root / 'result.txt')):
            criterion['evidence'] = ['result.txt', extra]
            self.assertTrue(self.report('achieved', criteria=[criterion])[1])
        self.assertTrue(self.report('achieved', progress_evidence=[history])[1])

    def saved_achievement(self):
        text = goal_loop.prepare(self.run_dir, self.root, self.state)
        write_report(self.root, text, 'achieved', ['result.txt'])
        self.state.update(stop={'rule': 'Goal assessment invalid'}, pending=[])

    def test_saved_achievement_recovery_revalidates_product_and_request(self):
        self.saved_achievement()
        self.assertTrue(goal_loop.recover_saved_achievement(self.run_dir, self.root, self.state))
        self.assertTrue(goal_loop.achievement_current(self.root, self.state))
        self.saved_achievement()
        (self.root / 'result.txt').write_text('Changed product.')
        self.assertFalse(goal_loop.recover_saved_achievement(self.run_dir, self.root, self.state))
        self.saved_achievement()
        self.state['task'] = 'A different request.'
        self.assertFalse(goal_loop.recover_saved_achievement(self.run_dir, self.root, self.state))

    def test_saved_recovery_requires_no_pending_work_and_validation_stop(self):
        self.saved_achievement()
        self.state['pending'] = ['worker']
        self.assertFalse(goal_loop.recover_saved_achievement(self.run_dir, self.root, self.state))
        self.state.update(pending=[], stop={'rule': 'Review failed'})
        self.assertFalse(goal_loop.recover_saved_achievement(self.run_dir, self.root, self.state))


    def test_validation_pause_explains_only_known_validation_failures(self):
        stop = goal_loop.validation_pause('Goal assessment invalid', 'Criterion 1 needs a file.', 'Continue', 'error-1')
        self.assertIn('Criterion 1 needs a file.', stop['why'])
        self.assertEqual(stop['error_id'], 'error-1')
        self.assertIsNone(goal_loop.validation_pause('Worker failed', 'Private raw log', 'Continue', 'error-2'))

    def test_research_prompt_has_all_five_creativity_steps(self):
        self.state['task_type'] = 'research'
        text = goal_loop.prepare(self.run_dir, self.root, self.state)
        for word in ('CONTEXTUALIZE', 'DIVERGE', 'CROSS-POLLINATE', 'AMPLIFY', 'ANCHOR', 'A+D', 'B+E', 'C+D'):
            self.assertIn(word, text)

    def test_saved_runs_do_not_silently_change_their_completion_contract(self):
        self.assertFalse(goal_loop.enabled({'workflow': 'staged'}))




class GoalSessionTests(unittest.TestCase):
    def test_desktop_goal_continuation_returns_to_builder(self):
        from engine.session import Session
        session = object.__new__(Session)
        session.st = dict(goal_loop_version=1, goal=dict(reason='Missing baseline'),
                          round=2, max_rounds=12, roles=dict(builder='codex', critic='claude'), chosen='O1')
        p = dict(agent='claude', phase='deep', n=1, file='reply.md', task_id='goal')
        with mock.patch.object(Session, 'dir', new_callable=mock.PropertyMock, return_value=Path('/private/tmp')), \
             mock.patch.object(Session, 'ws', new_callable=mock.PropertyMock, return_value=Path('/private/tmp')), \
             mock.patch.object(Session, 'add_entry'), mock.patch.object(Session, 'save'), \
             mock.patch('engine.broker.record_stage_reply'), \
             mock.patch.object(Session, 'issue') as issue, \
             mock.patch('engine.goal_loop.consume', return_value=('continue', [])):
            session._accept_goal(p, mock.Mock(prose='The baseline is missing.'))
            self.assertEqual(issue.call_args.args[0].kind, 'revise')
            self.assertEqual(issue.call_args.args[0].agent, 'codex')
            self.assertFalse(session.st['verify_passed'])
            self.assertEqual(session.st['round'], 3)

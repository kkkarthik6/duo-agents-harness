"""Desktop transport tests use local fake agents, never paid providers."""
from unittest import mock
from tests.helpers.session_base import SessionBase, fake


class GenericSessionTests(SessionBase):
    def setUp(self):
        super().setUp()
        for target in ('engine.summary_agent.observe', 'engine.summary_agent.request', 'engine.completion_summary.request'):
            p = mock.patch(target)
            p.start()
            self.addCleanup(p.stop)

    def test_one_cli_finishes_with_four_independent_roles(self):
        run = self.session.Session.create('Build a tiny tool that prints hello', [fake('codex')], mode='auto')
        self.run_until(run, lambda s: s.st['status'] == 'done', timeout=75)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        self.assertEqual(kinds[:2], ['exchange', 'plan_review'])
        self.assertIn('build', kinds)
        self.assertIn('critique', kinds)
        self.assertTrue(run.st['verify_passed'])
        self.assertEqual({r['role'] for r in run.st['agent_instances']}, {'lead', 'challenger', 'builder', 'critic'})
        instances = run.st['agent_instances']
        self.assertEqual(len({r['instance_id'] for r in instances}), len(instances))

    def test_resume_preserves_planning_before_code(self):
        run = self.session.Session.create('Build a tiny tool that prints hello', [fake('codex')], mode='auto')
        self.run_until(run, lambda s: bool(s.st['pending']), timeout=20)
        self.assertEqual(run.st['pending'][0]['kind'], 'exchange')
        run.resume()
        self.assertEqual(run.st['pending'][0]['kind'], 'exchange')
        self.assertEqual(run.st['phase'], 'frame')
        self.run_until(run, lambda s: s.st['status'] == 'done', timeout=75)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        self.assertLess(kinds.index('plan_review'), kinds.index('build'))

    def test_further_goal_work_returns_through_plan_review(self):
        run = self.session.Session.create('Build a tiny tool that prints hello',
                                          [fake('codex', '--goal-continue')], mode='auto')
        self.run_until(run, lambda s: s.st['status'] == 'done', timeout=100)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        following = kinds[kinds.index('goal_check') + 1:]
        self.assertIn('goal_assess', following)
        self.assertIn('plan_review', following)
        self.assertLess(following.index('plan_review'), following.index('build'))

    def test_a_rejected_plan_returns_to_the_planner_before_any_code(self):
        run = self.session.Session.create('Build a tiny tool that prints hello',
                                          [fake('codex', '--reject-plan-once')], mode='auto')
        self.run_until(run, lambda s: s.st['status'] == 'done', timeout=100)
        entries = [e for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        kinds = [e['kind'] for e in entries]
        verdicts = [e.get('verdict') for e in entries if e['kind'] == 'plan_review']
        self.assertEqual(verdicts[:2], ['REVISE', 'APPROVED'])
        first_revise = kinds.index('plan_review')
        approved = [i for i, e in enumerate(entries) if e['kind'] == 'plan_review' and e.get('verdict') == 'APPROVED'][0]
        self.assertIn('exchange', kinds[first_revise + 1:approved], 'the planner must revise the plan after a rejection')
        self.assertLess(approved, kinds.index('build'), 'no code before the plan is approved')

    def test_approved_work_closes_without_the_goal_check_turns(self):
        from engine import config
        with mock.patch.dict(config.CFG, {"fast_close": True}):
            run = self.session.Session.create('Build a tiny tool that prints hello', [fake('codex')], mode='auto')
            self.run_until(run, lambda s: s.st['status'] == 'done', timeout=90)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        self.assertNotIn('cycle_summary', kinds)
        self.assertNotIn('goal_check', kinds)
        self.assertIn('critique', kinds)

    def test_a_small_task_is_one_agent_turn_with_no_plan_review_or_closing_turns(self):
        from engine import config
        with mock.patch.dict(config.CFG, {"triage": "auto"}):
            run = self.session.Session.create('Build a tiny tool that prints hello', [fake('claude'), fake('codex')], mode='auto')
            self.assertEqual(run.st['size'], 'trivial')
            self.assertEqual(len(run.st['agents']), 1)
            self.run_until(run, lambda s: s.st['status'] == 'done', timeout=60)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        self.assertEqual(kinds, ['build'])
        self.assertTrue(run.st['quick'])
        self.assertIn('quick task', run.st['entries'][-1]['summary'].lower())
        self.assertTrue((run.ws / 'work.txt').is_file())

    def test_a_larger_task_keeps_the_whole_pipeline_under_auto_triage(self):
        from engine import config
        with mock.patch.dict(config.CFG, {"triage": "auto"}):
            run = self.session.Session.create('Refactor the auth module to use JWT tokens', [fake('codex')], mode='auto')
            self.assertEqual(run.st['size'], 'standard')
            self.run_until(run, lambda s: s.st['status'] == 'done', timeout=90)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        self.assertEqual(kinds[:2], ['exchange', 'plan_review'])
        self.assertIn('critique', kinds)

    def test_a_quick_task_that_does_not_finish_becomes_a_full_run_with_both_agents(self):
        from engine import config
        flag = '--unfinished-until-planned'
        with mock.patch.dict(config.CFG, {"triage": "auto"}):
            run = self.session.Session.create('Build a tiny tool that prints hello', [fake('claude', flag), fake('codex', flag)], mode='auto')
            self.assertEqual((run.st['size'], len(run.st['agents'])), ('trivial', 1))
            self.run_until(run, lambda s: s.st['status'] == 'done', timeout=150)
        kinds = [e['kind'] for e in run.st['entries'] if e['author'] not in ('human', 'system')]
        self.assertEqual(kinds[:2], ['build', 'revise'])                    # one agent tried twice
        self.assertIn('exchange', kinds)                                   # then it was planned
        self.assertIn('plan_review', kinds)
        self.assertIn('critique', kinds)                                   # and independently reviewed
        self.assertTrue(run.st['escalated'])
        self.assertEqual(run.st['size'], 'standard')
        self.assertEqual(len(run.st['agents']), 2)
        self.assertTrue(any(e['kind'] == 'notice' and 'full process' in e['prose'] for e in run.st['entries']))


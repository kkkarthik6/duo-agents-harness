import json
import tempfile
import unittest
from pathlib import Path

from engine import broker, task_context


class TaskContextTests(unittest.TestCase):
    def test_focus_and_results_replace_conversation_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'.duo/run'
            state={'id':'run','title':'Prepare a methods paper','task':'Prepare a methods paper.\nFULL_PROMPT_MUST_NOT_BE_COPIED',
                   'status':'running','created':'2026-11-19T12:00:00','pending':[{'task_id':'B'}],
                   'entries':[{'prose':'FULL_CONVERSATION_MUST_NOT_BE_COPIED'}]}
            broker.ensure(run,'A',state['title'],state['task']);broker.ensure(run,'B',state['title'],state['task'])
            broker.event(run,'historical_role','A',role='assess');broker.event(run,'task_status','A',status='stage_completed')
            broker.event(run,'assigned','B',role='build',agent='claude',specification='FULL_ASSIGNMENT_MUST_NOT_BE_COPIED')
            docs=root/'node';docs.mkdir()
            summary=docs/'summary.md';summary.write_text('## Task focus\nBuild the evidence-attributable experiment runner\n\n## Goal\nMeasure attribution on real data.\n\n## Plan\nFreeze the evaluation protocol.\n\n## Finished\nThe baseline replay matches.\n\n## Doing\nImplementing the runner.\n\n## Results summary\nThe old scores are not auditable.\n\n## Next steps\nReview the new raw records.')
            broker.event(run,'task_documents','B',task_file=str(docs/'task.md'),summary_file=str(summary))
            result=task_context.update(run,root,state)
            current=json.loads((run/'current_task.json').read_text())
            pointer=json.loads((root/'.duo/latest_context.json').read_text())
            self.assertTrue(result['available'])
            self.assertEqual(current['task_summaries']['B'],'Build the evidence-attributable experiment runner')
            self.assertNotEqual(current['task_summaries']['A'],current['task_summaries']['B'])
            self.assertEqual(current['tasks']['B']['results_summary'],'The old scores are not auditable.')
            self.assertEqual(current['current_task_id'],'B')
            self.assertEqual(pointer['context_file'],str(run/'latest_context.json'))
            text=(run/'latest_context.json').read_text()+(run/'current_task.json').read_text()
            for marker in ('FULL_PROMPT','FULL_CONVERSATION','FULL_ASSIGNMENT'):self.assertNotIn(marker,text)
            self.assertIn('useful_previous',(run/'latest_context.json').read_text())
            broker.event(run,'task_context','B',context={'task focus':'Freeze the pre-registration and prepare the methods scaffold'},source='saved plan')
            newer=task_context.update(run,root,state)
            self.assertEqual(newer['tasks']['B']['summary'],'Freeze the pre-registration and prepare the methods scaffold')
            question=broker.ask(run,'B','Which budget?')
            broker.answer_question(run,question['question_id'],'Three hours per session, including setup.')
            task_context.update(run,root,state)
            latest=json.loads((run/'latest_context.json').read_text())
            self.assertEqual(latest['user_decisions'][0]['answer'],'Three hours per session, including setup.')

    def test_reading_old_run_does_not_rewind_newer_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,date in [('new','2026-11-19T12:00:00'),('old','2026-11-18T12:00:00')]:
                task_context.update(root/'.duo'/name,root,{'id':name,'created':date,'status':'done','task':'Goal','pending':[]})
            self.assertEqual(json.loads((root/'.duo/latest_context.json').read_text())['run_id'],'new')

"""Short output names preserve full identity in metadata and recover lost dispatches."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import task_output, phases
from tests.test_phases import st
from tests.helpers.session_base import SessionBase, fake


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_long_unicode_titles_and_ids_only_appear_in_metadata(self):
        title='研究と評価 ' * 300
        run='run-'+'x'*500
        identity='task-'+'y'*500
        folder=task_output.task_directory(self.root,title,run,identity)
        self.assertEqual(folder.relative_to(self.root).as_posix(),'output/task-0001/step-0001')
        name=task_output.filename(identity,1,'codex/'+'z'*300,'../fix/'+'u'*300)
        self.assertLess(len(name.encode()),100)
        self.assertNotIn('/',name)
        (folder/name).write_text('Saved safely')
        index=json.loads((self.root/'.duo/output-paths.json').read_text())
        self.assertEqual(next(iter(index['entries'].values()))['title'],title)
        steps=json.loads((folder.parent/'.steps.json').read_text())
        self.assertIn(identity,steps['entries'])

    def test_ids_increment_and_remain_stable_across_calls(self):
        one=task_output.task_directory(self.root,'First request','run-one','logical-A')
        two=task_output.task_directory(self.root,'First request','run-one','logical-B')
        three=task_output.task_directory(self.root,'Second request','run-two','logical-A')
        self.assertEqual(one.name,'step-0001');self.assertEqual(two.name,'step-0002')
        self.assertEqual(three.parent.name,'task-0002')
        self.assertEqual(task_output.task_directory(self.root,'First request','run-one','logical-A'),one)
        contents=(one.parent/'contents.md').read_text()
        self.assertIn('[step-0001](step-0001/)',contents)
        self.assertIn('[step-0002](step-0002/)',contents)

    def test_existing_numbered_user_folder_is_not_reused(self):
        original=self.root/'output/task-0001';original.mkdir(parents=True)
        (original/'user.txt').write_text('Keep')
        folder=task_output.directory(self.root,'New task','new-run')
        self.assertEqual(folder.name,'task-0002')
        self.assertEqual((original/'user.txt').read_text(),'Keep')

    def test_legacy_outputs_and_user_contents_stay_in_place(self):
        original=self.root/'output/Previous title/old-run/logical-task'
        original.mkdir(parents=True)
        (original/'old-reply.md').write_text('Existing work')
        contents=original.parent/'contents.md';contents.write_text('User index')
        folder=task_output.task_directory(self.root,'Previous title','old-run','logical-task')
        self.assertEqual(folder,original)
        self.assertEqual(contents.read_text(),'User index')
        self.assertEqual((folder/'old-reply.md').read_text(),'Existing work')

    def test_concurrent_allocations_never_share_a_number(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            folders=list(executor.map(lambda n:task_output.directory(self.root,'Task '+str(n),'run-'+str(n)),range(16)))
        self.assertEqual(len(set(folders)),16)
        self.assertEqual({p.name for p in folders},{'task-%04d'%n for n in range(1,17)})

    def test_damaged_index_fails_without_reusing_existing_files(self):
        folder=task_output.directory(self.root,'One','run')
        (folder/'keep.md').write_text('Keep')
        (self.root/'.duo/output-paths.json').write_text('{')
        with self.assertRaises(ValueError):task_output.directory(self.root,'Two','other')
        self.assertEqual((folder/'keep.md').read_text(),'Keep')


class AssessmentRecoveryTests(unittest.TestCase):
    def test_exchange_is_reissued_after_dispatch_failure(self):
        state=st(phase='frame',relay_step='exchange',chosen=None,pending=[])
        self.assertEqual([t.kind for t in phases.next_turns(state)],['exchange'])

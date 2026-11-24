"""Evidence paths, exact missing-record diagnostics, and research log compatibility."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import research_workflow as flow, bounds, session
from tests.research_fixture import make_report, make_review, write


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = make_report(self.root)

    def save(self):
        write(self.root, 'research/coverage.json', json.dumps(self.data, indent=2))

    def test_semicolon_references_are_local_file_lists_not_one_filename(self):
        self.data['sources'][0]['evidence'] = 'research/sources/S1-notes.md; research/literature-review.md'
        self.data['searches'][0]['evidence'] = 'research/searches/L1Q0.log; research/sources/S1-notes.md'
        self.save()
        self.assertEqual(flow.validate(self.root, 'research')[1], [])
        make_review(self.root)
        self.assertEqual(flow.validate(self.root, 'research', review=True)[1], [])
        self.assertIn('research/sources/S1-notes.md', flow.files(self.root, 'research'))
        self.assertFalse(any(';' in p for p in flow.files(self.root, 'research')))

    def test_arrays_of_evidence_files_bind_all_inputs(self):
        self.data['sources'][0]['evidence'] = ['research/sources/S1-notes.md', 'research/literature-review.md']
        self.save()
        self.assertEqual(flow.validate(self.root, 'research')[1], [])
        make_review(self.root)
        receipt = flow.receipt(self.root, 'research', 'question')
        self.assertTrue(flow.current(self.root, receipt, 'question'))
        write(self.root, 'research/sources/S1-notes.md', 'Changed source notes')
        self.assertFalse(flow.current(self.root, receipt, 'question'))

    def test_an_existing_filename_with_a_semicolon_is_not_split(self):
        rel = write(self.root, 'research/sources/notes;appendix.md', 'Real file')
        self.assertEqual(flow.evidence_paths(self.root, rel), [rel])

    def test_missing_member_has_its_title_url_and_exact_path(self):
        self.data['sources'][0]['evidence'] = ['research/sources/S1-notes.md', 'research/sources/missing.md']
        self.save()
        errors = flow.validate(self.root, 'research')[1]
        self.assertTrue(any('Local fixture paper 1' in x and 'missing.md' in x and 'https://example.org/fixture1' in x for x in errors))
        self.assertEqual(flow.validate(self.root, 'research', require_full=False)[1], [])
        gap = next(g for g in flow.evidence_gaps(self.root, 'research') if g['id'] == 'S1')
        self.assertEqual(gap['expected_paths'], ['research/sources/missing.md'])
        self.assertEqual(gap['owner'], 'Duo researcher')

    def test_partial_reading_is_explained_and_cannot_pass_final_review(self):
        self.data['sources'][0].update(read_scope='sections', full_text='')
        self.save()
        self.assertEqual(flow.validate(self.root, 'research', require_full=False)[1], [])
        self.assertTrue(flow.validate(self.root, 'research')[1])
        gaps = flow.write_evidence_needed(self.root, 'research')
        self.assertEqual(len(gaps), 1)
        text = (self.root / 'research/evidence-needed.md').read_text()
        for expected in ('Local fixture paper 1', 'https://example.org/fixture1', 'research/sources/S1-full.txt', 'Researcher action:'):
            self.assertIn(expected, text)

    def test_existing_full_text_is_reused_instead_of_requested_again(self):
        self.data['sources'][0]['read_scope'] = 'abstract'
        self.save()
        gap = flow.evidence_gaps(self.root, 'research')[0]
        self.assertIn('Read the saved full text', gap['action'])

    def test_search_gap_exposes_the_query_and_capture_path(self):
        query = self.data['searches'][0]
        query['evidence'] = 'research/searches/not-saved.log'
        self.save()
        gap = next(g for g in flow.evidence_gaps(self.root, 'research') if g['kind'] == 'search_record')
        self.assertEqual(gap['id'], query['id'])
        self.assertEqual(gap['title'], query['query'])
        self.assertEqual(gap['expected_paths'], ['research/searches/not-saved.log'])

    def test_capturing_missing_evidence_is_real_research_progress(self):
        self.data['sources'][0]['evidence'] = 'research/sources/pending-notes.md'
        self.save()
        before = flow.progress(self.root, 'research')
        write(self.root, 'research/sources/pending-notes.md', 'Source-bound observations')
        self.assertNotEqual(before, flow.progress(self.root, 'research'))

    def test_root_search_and_turn_access_logs_are_in_scope_but_app_code_is_not(self):
        for rel in ('research/search.log', 'research/access-log-0058.md', 'research/source-register.csv'):
            self.assertFalse(bounds._outside_scope(rel, 'scout', 'research'), rel)
        for rel in ('research/train.py', 'app.py', 'research/../app.py'):
            self.assertTrue(bounds._outside_scope(rel, 'scout', 'research'), rel)


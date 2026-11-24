"""Coverage, full-text reading, source-linked method design, and bounded novelty."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import research_workflow as flow, session
from tests.research_fixture import make_report, make_review, write


class ResearchValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = make_report(self.root)

    def save(self):
        write(self.root, "research/coverage.json", json.dumps(self.data, indent=2))

    def errors(self, review=False):
        return flow.validate(self.root, "research", review=review)[1]

    def test_full_paper_reading_and_method_proposal_pass(self):
        self.assertEqual(self.errors(), [])
        make_review(self.root)
        self.assertEqual(self.errors(review=True), [])

    def test_abstract_and_excerpt_are_not_completed_readings(self):
        for scope in ("abstract", "sections", "unavailable"):
            self.data["sources"][0]["read_scope"] = scope
            self.save()
            self.assertTrue(any("full-text reading" in e for e in self.errors()))

    def test_a_read_claim_without_full_text_or_section_notes_fails(self):
        self.data["sources"][0]["full_text"] = "missing.txt"
        self.data["sources"][0]["reading_record"].pop("experiments")
        self.save()
        errors = self.errors()
        self.assertTrue(any("full-text evidence" in x for x in errors))
        self.assertTrue(any("source-bound problem" in x for x in errors))

    def test_each_paper_requires_relevance_and_an_adaptation_test(self):
        self.data["sources"][0]["relevance_to_user_idea"].pop("validation")
        self.save()
        self.assertTrue(any("Relevance to user's idea" in x for x in self.errors()))

    def test_the_review_requires_the_named_relevance_section(self):
        write(self.root, "research/literature-review.md", "A generic summary with no relevance analysis")
        self.assertTrue(any("Relevance to user's idea section" in x for x in self.errors()))

    def test_every_included_paper_appears_in_the_rendered_relevance_and_matrix(self):
        write(self.root, "research/literature-review.md", "## Relevance to user's idea\nS1: useful fixture concept.\n")
        self.assertTrue(any("S2 is missing from the relevance" in x for x in self.errors()))

    def test_access_failure_cannot_be_used_as_a_silent_exclusion(self):
        source = self.data["sources"][1]
        source.update(inclusion="excluded", read_scope="unavailable", exclusion_reason="Could not access the paper")
        self.save()
        self.assertTrue(any("unavailable full text is not an exclusion" in x for x in self.errors()))

    def test_method_requires_a_source_bound_drawback_and_borrowed_concepts(self):
        self.data["method"].pop("drawback_evidence")
        self.data["method"]["borrowed_concepts"][0]["source"] = "unknown"
        self.save()
        errors = self.errors()
        self.assertTrue(any("target drawback" in x for x in errors))
        self.assertTrue(any("Borrowed method concepts" in x for x in errors))

    def test_method_requires_baselines_tests_tradeoffs_and_a_mechanism(self):
        self.data["method"].update(evaluation="", mechanism="", tradeoffs="")
        self.save()
        self.assertEqual(len([e for e in self.errors() if e.startswith("Method proposal needs")]), 3)

    def test_more_citations_do_not_replace_search_breadth(self):
        self.data["searches"] = [s for s in self.data["searches"] if s["id"] != "L1Q1"]
        self.save()
        self.assertTrue(any("multiple distinct search terms" in x for x in self.errors()))

    def test_closest_papers_require_backward_and_forward_searches(self):
        self.data["searches"] = [s for s in self.data["searches"] if s["kind"] != "forward"]
        self.save()
        self.assertTrue(any("forward citation search" in x for x in self.errors()))

    def test_universal_novelty_claim_is_rejected(self):
        self.data["novelty"]["statement"] = "No published work combines all of these components"
        self.save()
        self.assertTrue(any("unsupported universal" in x for x in self.errors()))

    def test_unknown_comparison_fields_are_allowed_but_omitted_fields_are_not(self):
        self.data["sources"][0]["model_size"] = "unknown: not stated in the paper"
        self.save()
        self.assertEqual(self.errors(), [])
        self.data["sources"][0].pop("training")
        self.save()
        self.assertTrue(any("needs training" in x for x in self.errors()))

    def test_missing_research_lines_cannot_be_approved(self):
        make_review(self.root, verdict="APPROVED", missing=["Documentation-grounded code generation"])
        self.assertTrue(any("unaddressed missing research lines" in x for x in self.errors(review=True)))

    def test_revise_is_a_valid_review_but_does_not_mean_approval(self):
        make_review(self.root, verdict="REVISE", missing=["An adjacent method"])
        review, errors = flow.validate(self.root, "research", review=True)
        self.assertEqual(errors, [])
        self.assertEqual(review["verdict"], "REVISE")

    def test_changed_report_or_source_invalidates_approval(self):
        make_review(self.root)
        receipt = flow.receipt(self.root, "research", "question")
        self.assertTrue(flow.current(self.root, receipt, "question"))
        write(self.root, self.data["sources"][0]["full_text"], "Changed full paper version")
        self.assertFalse(flow.current(self.root, receipt, "question"))
        self.assertTrue(any("source, reading or method inputs changed" in e for e in self.errors(review=True)))
        self.data["scope"] = "Changed scope"
        self.save()
        self.assertTrue(any("stale" in e for e in self.errors(review=True)))

    def test_malformed_primary_url_returns_a_validation_error(self):
        self.data["sources"][0]["url"] = "https://[invalid"
        self.save()
        self.assertTrue(any("primary HTTPS URL" in e for e in self.errors()))

    def test_malformed_review_evidence_does_not_crash_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            packet = flow.copy_review_packet(self.root, directory, "research")
            review = make_review(packet)
            review["evidence"] = None
            write(packet, "research/coverage-review.json", json.dumps(review))
            flow.collect_review(packet, self.root, "research")
        self.assertTrue(any("additional search" in e for e in self.errors(review=True)))

    def test_isolated_review_packet_contains_evidence_not_history_or_implementation(self):
        write(self.root, ".duo/history.md", "Private conversation")
        write(self.root, "src/app.py", "User implementation")
        with tempfile.TemporaryDirectory() as directory:
            packet = flow.copy_review_packet(self.root, directory, "research")
            self.assertTrue((packet / self.data["sources"][0]["full_text"]).is_file())
            self.assertFalse((packet / ".duo/history.md").exists())
            self.assertFalse((packet / "src/app.py").exists())
            make_review(packet)
            write(packet, "research/coverage.json", "Reviewer tampering")
            flow.collect_review(packet, self.root, "research")
        self.assertEqual(json.loads((self.root / "research/coverage.json").read_text()), self.data)
        self.assertEqual(self.errors(review=True), [])


class ResearchRunTests(unittest.TestCase):
    def create(self):
        folder = make_project()
        driver = FakeDriver(folder)
        original = driver.do_understand
        def understand(text):
            reply = original(text)
            p = folder / "duo/brief.md"
            p.write_text(p.read_text().replace("- programming", "- research"))
            return reply
        driver.behaviour["understand"] = understand
        run = runmod.Run.create(folder, "Research a method that improves a documented drawback", ["claude", "codex"], driver,
                                docker_check=lambda: True, clock=Clock())
        return run, driver






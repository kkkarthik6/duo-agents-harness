"""Explicit local fixtures, not real literature or research results."""
import hashlib
import json
from datetime import date
from pathlib import Path


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return rel


def make_report(root, directory="research"):
    from engine import research_workflow as flow
    sources, searches = [], []
    for number in (1, 2):
        sid, lid = "S%d" % number, "L%d" % number
        full_text = write(root, directory + "/sources/%s.txt" % sid,
                          "Local fixture paper. Problem: repeat lookups. Method: caching. Experiments: none. Limitation: stale entries.")
        notes = write(root, directory + "/sources/%s-notes.md" % sid, "Local fixture source notes with section locators; no real study result.")
        source = {"id": sid, "title": "Local fixture paper %d" % number, "year": 2026, "url": "https://example.org/fixture%d" % number,
                  "source_type": "primary", "version": "Local fixture", "read_scope": "full", "locator": "Fixture sections 1-4",
                  "support": "Fixture mechanism only; no scientific claim", "evidence": notes, "full_text": full_text,
                  "inclusion": "included", "exclusion_reason": "Relevant local fixture",
                  "reading_record": {key: "Fixture %s section inspected" % key for key in ("problem", "method", "experiments", "limitations")},
                  "relevance_to_user_idea": {"useful_concepts": "Cache repeated lookups", "drawback": "Entries can become stale",
                     "adaptation": "Add explicit cache versions", "distinction": "The proposed version check is an unmeasured addition", "validation": "Compare fresh and stale fixture inputs"}}
        source.update({key: "Unknown or not applicable to the local fixture" for key in flow.COMPARISON})
        sources.append(source)
        for index, kind in enumerate(("keyword", "keyword", "backward", "forward")):
            qid = "%sQ%d" % (lid, index)
            log = write(root, directory + "/searches/%s.log" % qid, "Local fixture search record: %s %d" % (kind, index))
            searches.append({"id": qid, "line": lid, "kind": kind, "query": "fixture %s term %d" % (sid, index),
                             "service": "Local fixture", "outcome": "found", "sources": [sid], "evidence": log})
    report = {"question": "Improve a local fixture mechanism", "scope": "Two local fixture lines only", "searched_on": date.today().isoformat(),
              "lines": [{"id": "L%d" % i, "name": "Fixture line %d" % i, "relevance": "Tests a coverage path",
                         "status": "covered", "limits": "No actual scientific search", "sources": ["S%d" % i]} for i in (1, 2)],
              "sources": sources, "searches": searches, "closest_work": ["S1", "S2"],
              "novelty": {"status": "not_established", "statement": "The fixture makes no scientific novelty claim", "limits": "Local test only"},
              "method": {"target_drawback": "Stale cached evidence", "closest_baseline": "S1",
                         "drawback_evidence": {"source": "S1", "locator": "Fixture limitation section", "observation": "Stale entries can be reused"},
                         "borrowed_concepts": [{"source": "S1", "concept": "Lookup cache", "adaptation": "Version entries"},
                                               {"source": "S2", "concept": "Check evidence age", "adaptation": "Reject stale entries"}],
                         "mechanism": "Look up, check version, refresh when stale", "expected_improvement": "Fewer stale answers; unmeasured",
                         "evaluation": "Compare the baseline and versioned cache; remove the version check as an ablation; reject improvement if stale errors persist",
                         "tradeoffs": "Extra checking cost", "novelty_limits": "No novelty claim; fixture only"}}
    write(root, flow.path(directory, "coverage.json"), json.dumps(report, indent=2))
    write(root, flow.path(directory, "literature-review.md"), "# Fixture review\n\n## Relevance to user's idea\n\nS1: cache lookups; stale entries are a drawback. S2: check evidence age. Proposed adaptation: versioned cache. Unmeasured.\n")
    write(root, flow.path(directory, "evidence-matrix.md"), "# Fixture comparison\nS1 and S2: local fixture methods, scope limits, unknown results; no real research.\n")
    write(root, flow.path(directory, "method-proposal.md"), "# Fixture method\nTarget stale cached inputs. Borrow caching and age checks. Version entries. Compare against the fixture baseline and remove version checks as an ablation. This is unmeasured.\n")
    return report


def make_review(root, directory="research", verdict="APPROVED", missing=None):
    from engine import research_workflow as flow
    evidence = write(root, directory + "/reviewer/search.log", "Separate local fixture forward/backward search and source check; no actual scientific search")
    review = {"report_sha256": hashlib.sha256((Path(root) / flow.path(directory, "coverage.json")).read_bytes()).hexdigest(),
              "input_hashes": flow.input_manifest(root, directory), "verdict": verdict, "missing_lines": missing or [], "closest_comparison": "Compared both local fixture methods and their scope",
              "counterevidence": "Refreshing a cache adds latency", "scope_limits": "Local fixture only", "reason": "Fixture coverage is complete" if verdict == "APPROVED" else "A relevant line remains missing",
              "method_assessment": "Versioned caching targets the documented stale-entry drawback; comparison and ablation are specified", "evidence": [evidence]}
    write(root, flow.path(directory, "coverage-review.json"), json.dumps(review, indent=2))
    write(root, flow.path(directory, "coverage-review.md"), "# Fixture coverage challenge\nCompared the two methods, checked reading notes, challenged missing lines, and assessed the targeted drawback and test. Local test only.\n")
    return review

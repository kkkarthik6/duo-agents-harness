import unittest

from engine.insights import flags, progress, stats


def e(n, author, phase, kind=None, **kw):
    d = {"n": n, "author": author, "phase": phase, "kind": kind or phase, "summary": "s", "prose": "",
         "options": [], "verdict": None, "ste": [], "bounds": [], "errors": [], "invalid": False, "duration": 10}
    d.update(kw)
    return d


def sc(*pairs):
    return [{"id": oid, "scores": {"fit": [v, "r"]}} for oid, v in pairs]


OPTS = [{"id": "O1", "proposer": "claude"}, {"id": "O2", "proposer": "codex"}]


def kinds(fl):
    return [f["kind"] for f in fl]


class InsightTests(unittest.TestCase):
    def test_no_ignored_when_all_scored(self):
        self.assertEqual(flags([e(5, "claude", "score", options=sc(("O1", 3), ("O2", 1)))], OPTS), [])

    def test_premature_termination(self):
        ents = [e(7, "codex", "deep", "critique", verdict="APPROVED", prose="Looks fine.")]
        self.assertEqual(kinds(flags(ents)), ["premature_termination"])
        ok = [e(7, "codex", "deep", "critique", verdict="APPROVED", prose="- Checked tests\n- Ran it")]
        self.assertEqual(flags(ok), [])

    def test_no_verification(self):
        self.assertEqual(kinds(flags([e(9, "system", "done", "done")])), ["no_verification"])
        self.assertEqual(flags([e(8, "codex", "verify"), e(9, "system", "done", "done")]), [])

    def test_derailment_and_ping_pong(self):
        ents = [e(3, "claude", "diverge", bounds=["handoff outside the ask", "ping-pong limit reached"])]
        self.assertEqual(kinds(flags(ents)), ["derailment", "ping_pong"])

    def test_stats_counts_current_cycle_only(self):
        ents = [e(2, "claude", "frame", cycle=1), e(3, "claude", "frame", cycle=2)]
        s = stats(ents, {"max_rounds": 4, "cycle": 2})
        self.assertEqual(s["per_phase"]["frame"]["turns"], 1)

    def test_stats_empty(self):
        s = stats([], {"max_rounds": 4})
        self.assertEqual((s["turns"], s["per_phase"]), (0, {}))


OPTS3 = [{"id": "O1"}, {"id": "O2"}, {"id": "O3"}]


def run_state(phase, entries, **kw):
    s = {"phase": phase, "status": "running", "cycle": 1, "max_rounds": 4, "agents": ["claude", "codex"],
         "options": OPTS3, "entries": entries}
    s.update(kw)
    return s


class ProgressTests(unittest.TestCase):
    def test_fresh_state(self):
        p = progress(run_state(None, []))
        self.assertEqual(p["done"], 0.0)
        self.assertIsNone(p["eta_s"])

    def test_mid_run_fraction(self):
        ents = [e(2, "claude", "frame"), e(3, "codex", "frame"), e(4, "claude", "deep")]
        p = progress(run_state("deep", ents))
        self.assertEqual(p["phase"], "deep")
        self.assertGreater(p["done"], 0.1)
        self.assertLess(p["done"], 0.9)

    def test_done_status(self):
        p = progress(run_state("done", [e(2, "claude", "frame")], status="done"))
        self.assertEqual(p["done"], 1.0)

    def test_eta_uses_run_durations_and_shrinks(self):
        before = progress(run_state("deep", [e(6, "claude", "deep")]))
        after = progress(run_state("deep", [e(6, "claude", "deep"), e(7, "codex", "deep")]))
        self.assertIsNotNone(before["eta_s"])
        self.assertLess(after["eta_s"][1], before["eta_s"][1])

    def test_eta_falls_back_to_history(self):
        p = progress(run_state("frame", [e(2, "claude", "frame")]), history_medians={"deep": 20})
        self.assertIsNotNone(p["eta_s"])


if __name__ == "__main__":
    unittest.main()

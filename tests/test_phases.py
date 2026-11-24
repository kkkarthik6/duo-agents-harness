import unittest

from engine.phases import next_turns

ROLES = {"lead": "claude", "challenger": "codex", "builder": "claude", "critic": "codex", "verifier": "codex", "scout": "agy"}


def st(**kw):
    s = {"phase": None, "task_type": "programming", "roles": dict(ROLES), "agents": ["claude", "codex", "agy"],
         "options": [], "round": 0, "max_rounds": 4, "verify_failures": 0, "ranking": [], "chosen": None,
         "tried": [], "last_verdict": None, "verify_passed": None, "stage_requests": {"verify": "check the output"}}
    s.update(kw)
    return s


def opts(n):
    return [{"id": "O%d" % i, "title": "t", "proposer": "claude", "probe": ""} for i in range(1, n + 1)]


class PhaseTests(unittest.TestCase):
    def test_plan_is_drafted_reviewed_then_approved(self):
        s = st()
        self.assertEqual([(x.agent, x.kind) for x in next_turns(s)], [("claude", "exchange")])
        self.assertEqual([(x.agent, x.kind) for x in next_turns(s)], [("codex", "plan_review")])
        s["plan_verdict"] = "APPROVED"
        self.assertEqual(next_turns(s), [])
        self.assertEqual(s["phase"], "decide")

    def test_a_rejected_plan_goes_back_to_the_planner(self):
        s = st(phase="frame", role_plan_step="review", plan_verdict="REVISE")
        self.assertEqual([(x.agent, x.kind) for x in next_turns(s)], [("claude", "exchange")])
        self.assertEqual(s["plan_review_rounds"], 1)

    def test_plan_rounds_run_out_and_stop(self):
        s = st(phase="frame", role_plan_step="review", plan_verdict="REVISE", max_rounds=1)
        self.assertEqual(next_turns(s), "stalled")

    def test_a_failed_verify_runs_out_of_rounds_and_stops(self):
        s = st(phase="verify", chosen="O1", verify_passed=False, round=4, max_rounds=4)
        self.assertEqual(next_turns(s), "stalled")

    def test_deep_alternates_until_approved(self):
        s = st(phase="decide", options=opts(2), ranking=["O2", "O1"], chosen="O2")
        self.assertEqual([(x.agent, x.kind) for x in next_turns(s)], [("claude", "build")])
        self.assertEqual([(x.agent, x.kind) for x in next_turns(s)], [("codex", "critique")])
        s["last_verdict"] = "REVISE"
        self.assertEqual([(x.agent, x.kind, s["round"]) for x in next_turns(s)], [("claude", "revise", 2)])
        next_turns(s)
        s["last_verdict"] = "APPROVED"
        self.assertEqual([(x.agent, x.phase) for x in next_turns(s)], [("codex", "verify")])
        s["verify_passed"] = True
        self.assertEqual(next_turns(s), "done")

    def test_budget_exhausted_stops_and_asks(self):
        s = st(phase="decide", options=opts(1), ranking=["O1"], chosen="O1", max_rounds=1)
        next_turns(s)
        next_turns(s)
        s["last_verdict"] = "REVISE"
        self.assertEqual(next_turns(s), "stalled")

    def test_single_agent_roles(self):
        one = {k: "codex" for k in ROLES}
        s = st(agents=["codex"], roles=one, phase="decide", options=opts(1), ranking=["O1"], chosen="O1")
        self.assertEqual(next_turns(s)[0].agent, "codex")
        self.assertEqual(next_turns(s)[0].kind, "critique")


if __name__ == "__main__":
    unittest.main()


class SecureTurnTests(unittest.TestCase):
    def state(self, **kw):
        s = {"phase": "verify", "roles": {"lead": "claude", "builder": "claude", "critic": "codex", "verifier": "codex",
             "security": "codex", "scout": "codex"}, "agents": ["claude", "codex"], "options": [{"id": "O1"}],
             "chosen": "O1", "round": 1, "max_rounds": 4, "verify_passed": True, "ranking": ["O1"], "tried": [], "stage_requests": {"verify": "x", "security": "x"}}
        s.update(kw)
        return s

    def test_verify_pass_with_code_goes_to_security_review(self):
        s = self.state(code_changed=True)
        t = next_turns(s)
        self.assertEqual((t[0].kind, t[0].agent), ("secure", "codex"))
        self.assertEqual(s["deep_step"], "secure")

    def test_verify_pass_without_code_finishes(self):
        self.assertEqual(next_turns(self.state(code_changed=False)), "done")

    def test_secure_approved_finishes(self):
        self.assertEqual(next_turns(self.state(code_changed=True, deep_step="secure", secure_ok=True)), "done")

    def test_secure_revise_goes_back_to_build(self):
        s = self.state(code_changed=True, deep_step="secure", secure_ok=False)
        t = next_turns(s)
        self.assertEqual((t[0].kind, s["round"]), ("revise", 2))


class SandboxFlowTests(unittest.TestCase):
    def state(self, **kw):
        s = {"phase": "decide", "roles": {"lead": "claude", "builder": "claude", "critic": "codex", "verifier": "codex", "security": "codex",
             "scout": "codex"}, "agents": ["claude", "codex"], "options": [{"id": "O1"}, {"id": "O2"}], "chosen": "O1", "round": 0,
             "max_rounds": 4, "ranking": ["O1", "O2"], "tried": [], "sandbox_on": True, "stage_requests": {"verify": "x"}}
        s.update(kw)
        return s

    def gate(self, s, ok, log=""):
        s["gate_result"] = {"ok": ok, "log": log}

    def test_setup_comes_after_decide_and_before_build(self):
        s = self.state()
        t = next_turns(s)
        self.assertEqual((t[0].agent, t[0].phase, t[0].kind), ("claude", "setup", "setup"))
        self.assertEqual(next_turns(s), "gate:image")

    def test_a_second_agent_checks_for_missing_prerequisites_then_the_image_is_rebuilt(self):
        s = self.state()
        next_turns(s); next_turns(s)
        self.gate(s, True)
        t = next_turns(s)
        self.assertEqual((t[0].agent, t[0].kind), ("codex", "setup_check"))
        self.assertEqual(next_turns(s), "gate:image")
        self.gate(s, True)
        t = next_turns(s)
        self.assertEqual((t[0].kind, s["phase"], s["setup_done"]), ("build", "deep", True))

    def test_single_agent_builds_right_after_the_first_image(self):
        s = self.state(agents=["claude"])
        next_turns(s); next_turns(s)
        self.gate(s, True)
        self.assertEqual(next_turns(s)[0].kind, "build")

    def test_failed_image_goes_back_to_the_builder_with_the_log(self):
        s = self.state()
        next_turns(s); next_turns(s)
        self.gate(s, False, "E: Unable to locate package nope")
        t = next_turns(s)
        self.assertEqual((t[0].agent, t[0].kind), ("claude", "setup_fix"))
        self.assertEqual(next_turns(s), "gate:image")                 # rebuilt after the fix

    def test_failure_after_the_check_returns_to_the_check_step(self):
        s = self.state()
        next_turns(s); next_turns(s)
        self.gate(s, True); next_turns(s); next_turns(s)
        self.gate(s, False, "boom")
        self.assertEqual(next_turns(s)[0].kind, "setup_fix")
        self.assertEqual(next_turns(s), "gate:image")
        self.gate(s, True)
        self.assertEqual(next_turns(s)[0].kind, "build")              # no second check turn

    def test_three_failed_builds_pause_the_run(self):
        s = self.state()
        next_turns(s); next_turns(s)
        for _ in range(3):
            self.gate(s, False, "x")
            self.assertEqual(next_turns(s)[0].kind, "setup_fix")
            next_turns(s)
        self.gate(s, False, "x")
        self.assertEqual(next_turns(s), "pause")

    def test_without_the_sandbox_nothing_changes(self):
        s = self.state(sandbox_on=False)
        t = next_turns(s)
        self.assertEqual((t[0].kind, s["phase"]), ("build", "deep"))

    def test_app_must_run_in_a_fresh_container_before_verify(self):
        s = self.state(phase="deep", deep_step="critique", last_verdict="APPROVED", setup_done=True, round=1)
        self.assertEqual(next_turns(s), "gate:smoke")
        self.gate(s, True)
        t = next_turns(s)
        self.assertEqual((t[0].kind, s["phase"], s["smoke_ok"]), ("verify", "verify", True))

    def test_failed_smoke_goes_back_to_revise_with_the_log(self):
        s = self.state(phase="deep", deep_step="critique", last_verdict="APPROVED", setup_done=True, round=1)
        next_turns(s)
        self.gate(s, False, "ModuleNotFoundError: flask")
        t = next_turns(s)
        self.assertEqual((t[0].kind, s["round"], s["smoke_log"]), ("revise", 2, "ModuleNotFoundError: flask"))
        self.assertIsNone(s["smoke_ok"])

    def test_failed_smoke_at_the_round_limit_stops_and_asks(self):
        s = self.state(phase="deep", deep_step="critique", last_verdict="APPROVED", setup_done=True, round=4)
        next_turns(s)
        self.gate(s, False, "x")
        t = next_turns(s)
        self.assertEqual(t, "stalled")




class LoopGuardTests(unittest.TestCase):
    def verify_fail(self, **kw):
        s = st(phase="verify", chosen="O1", verify_passed=False, roles=dict(ROLES), **kw)
        s["verify_failures"] = 0
        return s

    def test_an_open_security_block_stays_until_a_change_clears_it(self):
        # The denied text is still on disk when a revision changes nothing, so the block must hold.
        s = self.verify_fail(sec_blocked=True, last_changed=0, stall=0, round=0)
        t = next_turns(s)
        self.assertEqual(t[0].kind, "revise")
        self.assertTrue(s["sec_blocked"])

    def test_two_revisions_that_change_nothing_stop_the_run(self):
        s = self.verify_fail(last_changed=0, stall=1, round=0)
        self.assertEqual(next_turns(s), "stalled")

    def test_a_revision_that_changes_files_resets_the_stall_count(self):
        s = self.verify_fail(last_changed=3, stall=1, round=0)
        t = next_turns(s)
        self.assertEqual(t[0].kind, "revise")
        self.assertEqual(s["stall"], 0)

    def test_out_of_budget_stops_instead_of_switching_options(self):
        s = self.verify_fail(ranking=["O1", "O2", "O3"], tried=[], fallbacks=2, round=4, last_changed=2)
        s["verify_failures"] = 1
        self.assertEqual(next_turns(s), "stalled")

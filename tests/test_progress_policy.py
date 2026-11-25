"""Regression cases for useful build turns and bounded review work."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import phases, protocol, session
from tests.test_phases import st


class MilestoneTests(unittest.TestCase):
    def test_unfinished_build_continues_without_a_critic_then_requests_review(self):
        state = st(phase="deep", deep_step="build", chosen="O1", round=1,
                   ready_for_review=False, last_changed=2)
        self.assertEqual(phases.next_turns(state)[0].kind, "revise")
        self.assertEqual(state["round"], 2)
        state["ready_for_review"] = True
        self.assertEqual(phases.next_turns(state)[0].kind, "critique")

    def test_unfinished_build_still_respects_its_budget(self):
        state = st(phase="deep", deep_step="build", round=4, ready_for_review=False)
        self.assertEqual(phases.next_turns(state), "stalled")

    def test_check_logs_alone_do_not_extend_a_build_forever(self):
        state = st(phase="deep", deep_step="build", round=1, chosen="O1", ready_for_review=False, stall=1)
        state["last_changed"] = session.Session._guard_value(None, "progress", None, 0, True)
        self.assertEqual(phases.next_turns(state), "stalled")

    def test_review_readiness_requires_a_boolean(self):
        text = '```duo\n' + json.dumps({"summary": "Built the first part.", "phase": "deep", "ready_for_review": "false"}) + '\n```'
        self.assertIn("ready_for_review must be true or false", protocol.parse(text).errors)


class ContentProgressTests(unittest.TestCase):
    def test_gitless_edits_and_deletions_are_visible_without_harness_chatter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = root / "app.py"
            code.write_text("one")
            before = session._worktree_state(root)
            self.assertIn("app.py", before)
            code.write_text("two")
            self.assertNotEqual(before["app.py"], session._worktree_state(root)["app.py"])
            reply = root / ".duo/run/replies/turn.md"
            reply.parent.mkdir(parents=True)
            reply.write_text("Just a reply")
            self.assertNotIn(".duo/run/replies/turn.md", session._worktree_state(root))
            code.unlink()
            self.assertIn("app.py", set(before) - set(session._worktree_state(root)))

class ActiveBudgetTests(unittest.TestCase):
    def make_session(self, state):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state.update(duo_dir=folder.name, workspace=folder.name)
        return session.Session(state)

    def test_a_review_cannot_wait_indefinitely_for_a_reply(self):
        s = self.make_session({"created": None, "pending": [{"agent": "codex", "kind": "critique", "issued_at": 0}],
                             "panes": {"codex": "%1"}})
        # Unfinished-turn timeouts are opt-in: by default an assignment has no elapsed-time cutoff.
        with mock.patch.dict(session.config.CFG, unfinished_timeouts_enabled=True), \
                mock.patch("engine.session.time.time", return_value=601), \
                mock.patch("engine.session.send_key") as key, mock.patch.object(s, "_pause") as pause:
            self.assertTrue(s._active_budget())
        key.assert_called_once_with("%1", "C-c")
        self.assertEqual(pause.call_args.args[0], "Turn time limit")
        self.assertTrue(s.st["pending"][0]["interrupted"])

    def test_optional_active_limit_applies_to_a_turn_that_never_completes(self):
        s = self.make_session({"active_runtime_seconds":21601,"pending": [
            {"agent": "codex", "kind": "build", "issued_at": 21600}], "panes": {"codex": "%1"}})
        with mock.patch.dict(session.config.CFG, active_time_limit_enabled=True, active_time_limit_hours=6), \
                mock.patch("engine.session.time.time", return_value=21601), \
                mock.patch("engine.session.send_key"), mock.patch.object(s, "_pause") as pause:
            self.assertTrue(s._active_budget())
        self.assertEqual(pause.call_args.args[0], "Active task time limit")

    def test_stop_card_does_not_resume_itself_through_boot_detection(self):
        s = self.make_session({"status": "waiting", "paused": True, "stop": {"rule": "Turn time limit"}})
        with mock.patch.object(s, "_tmux_alive") as alive:
            s.tick()
        alive.assert_not_called()

    def test_passing_smoke_fingerprint_changes_only_with_product_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("first")
            before = session._source_fingerprint(root)
            log = root / ".duo/logs/check.log"
            log.parent.mkdir(parents=True)
            log.write_text("check passed")
            self.assertEqual(before, session._source_fingerprint(root))
            source.write_text("second")
            self.assertNotEqual(before, session._source_fingerprint(root))

    def test_unscanned_data_disables_smoke_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            self.assertIsNone(session._source_fingerprint(root))


class ContributionTests(unittest.TestCase):
    def test_display_notes_keep_outcomes_separate_and_allow_old_reviews(self):
        from engine import task_output
        review = "REVISE\n\nOutcome Save: Passed retry test.\n- Outcome Load: Still fails.\nNext: Fix ordering."
        notes = task_output.review_notes(review)
        self.assertEqual(notes["review_outcomes"], {"Save": "Passed retry test.", "Load": "Still fails."})
        self.assertEqual(notes["review_next"], "Fix ordering.")
        self.assertEqual(task_output.review_notes("REVISE\nOrdering is incorrect."), {})

    def test_one_blocker_leaves_independent_work_ready(self):
        from engine import build_workflow
        packages = [dict(id=name, depends=deps, requires=[], status=status)
                    for name, deps, status in (("1", [], "done"), ("2", [], "blocked"),
                                               ("3", [], "pending"), ("4", ["2"], "pending"))]
        workflow = {"packages": packages, "checks": []}
        self.assertEqual(build_workflow.ready(workflow), [packages[2]])
        packages[2]["status"] = "done"
        self.assertEqual(build_workflow.ready(workflow), [])
        self.assertFalse(build_workflow.complete(workflow))
        packages[1]["status"] = "done"
        self.assertEqual(build_workflow.ready(workflow), [packages[3]])

    def test_blocked_reply_reaches_package_scheduler(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / ".duo" / "test"
            run.mkdir(parents=True)
            packages = [dict(id=key, status="pending", requires=[], depends=deps,
                             deliverables=[key + ".txt"], kind="document")
                        for key, deps in (("2", []), ("3", []), ("4", ["2"]))]
            workflow = dict(checks=[], packages=packages, receipts={})
            state = st(id="test", workspace=str(root), duo_dir=str(run), task="Build items",
                       phase="deep", deep_step="build", build_workflow=workflow, entries=[],
                       pending=[], handoff_log=[], status="running", paused=False)
            engine = session.Session(state)
            assignment = dict(kind="build", phase="deep", agent="codex", task_id="T1", n=2,
                              file="replies/build.md", issued_at=1, base={}, build_package=packages[0])
            with mock.patch.object(engine, "save"):
                engine.accept(assignment, "Blocked: Owner must supply input for item 2.")
            self.assertFalse(state["paused"])
            self.assertEqual(packages[0]["status"], "blocked")
            self.assertEqual(engine._build_package()["id"], "3")

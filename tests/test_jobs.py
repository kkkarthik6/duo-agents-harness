import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import jobs, session


def process(pid, parent=1, identity=None, infrastructure=False, state="S"):
    return {"pid": pid, "parent": parent, "identity": identity or str(pid) + "@start",
            "infrastructure": infrastructure, "state": state, "cpu": "0:00"}


class JobMonitorTests(unittest.TestCase):
    def setUp(self):
        # Retain explicit-timeout behavior checks as an opt-in compatibility mode.
        self.enterContext(mock.patch.dict(jobs.config.CFG, unfinished_timeouts_enabled=True, long_job_timeout_seconds=21600))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "jobs/T1.json"
        self.p = {"task_id": "T1", "issued": 0, "progress_at": 0}
        with mock.patch.object(jobs, "pane_pid", return_value=10), \
                mock.patch.object(jobs, "process_table", return_value={10: process(10, infrastructure=True)}):
            jobs.begin(self.p, "%1", self.path)

    def test_silent_sleeping_training_process_survives_an_hour_without_files_or_cpu_change(self):
        table = {10: process(10, infrastructure=True), 11: process(11, 10)}
        with mock.patch.object(jobs, "process_table", return_value=table):
            self.assertEqual(jobs.poll(self.p, 15), "waiting")
            self.assertEqual(jobs.poll(self.p, 3615), "waiting")
        self.assertEqual(self.p["progress_at"], 0)
        self.assertEqual(jobs.load(self.path, "T1")[0]["pid"], 11)

    def test_twelve_hour_estimate_gets_eighteen_hours_and_survives_reload(self):
        self.p.update(estimated_runtime_seconds=12 * 3600, estimate_basis="1000 steps at measured throughput")
        with mock.patch.object(jobs, "process_table", return_value={11: process(11, 10)}):
            self.assertEqual(jobs.poll(self.p, 15), "waiting")
            self.assertEqual(jobs.poll(self.p, 12 * 3600 + 15), "waiting")
            self.assertEqual(self.p["job_deadline_at"], 18 * 3600 + 15)
        resumed = {"task_id": "T1", "job_registry": str(self.path)}
        with mock.patch.object(jobs, "process_table", return_value={11: process(11)}):
            self.assertEqual(jobs.poll(resumed, 17 * 3600), "waiting")
            self.assertEqual(jobs.poll(resumed, 18 * 3600 + 16), "expired")

    def test_deadline_does_not_slide_when_estimate_or_multiplier_changes(self):
        self.p["estimated_runtime_seconds"] = 100
        with mock.patch.object(jobs, "process_table", return_value={11: process(11, 10)}):
            jobs.poll(self.p, 15)
            self.p["estimated_runtime_seconds"] = 10000
            with mock.patch.dict(jobs.config.CFG, {"job_runtime_multiplier": 10}):
                self.assertEqual(jobs.poll(self.p, 166), "expired")
        self.assertEqual(self.p["job_deadline_at"], 165)

    def test_user_resume_extends_the_same_job_without_resetting_its_identity(self):
        self.p["estimated_runtime_seconds"] = 100
        with mock.patch.object(jobs, "process_table", return_value={11: process(11, 10)}):
            jobs.poll(self.p, 15)
            jobs.poll(self.p, 166)
            jobs.extend_deadlines([self.p], 200)
            self.assertEqual(jobs.poll(self.p, 201), "waiting")
            self.assertEqual(self.p["job_deadline_at"], 350)
        self.assertEqual(jobs.load(self.path, "T1")[0]["identity"], "11@start")

    def test_invalid_estimates_are_rejected(self):
        for number in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                jobs.positive(number)

    def test_parallel_waits_receive_one_run_budget_credit(self):
        from engine import harness
        pending = [dict(stage="build", task_id="T1", job_deadline_at=18 * 3600),
                   dict(stage="build", task_id="T2", job_deadline_at=20 * 3600)]
        state = {}
        with mock.patch.object(jobs, "poll", return_value="waiting"):
            jobs.budget_extension(state, pending, 0)
            extra = jobs.budget_extension(state, pending, 10 * 3600)
        self.assertEqual(extra, 10)
        self.assertEqual(harness.budget({"created":"1970-01-01T00:00:00+00:00"})[0], "ok")
        with mock.patch.object(jobs, "poll", return_value="expired"):
            extra = jobs.budget_extension(state, pending, 21 * 3600)
        self.assertEqual(extra, 20)

    def test_waiting_for_user_does_not_consume_the_resumed_run_budget(self):
        state = {"job_runtime_paused_at": 100, "job_budget_waiting": True, "job_budget_checked_at": 100}
        jobs.resume_budget(state, 7300)
        self.assertEqual(state["budget_extension"], 2)
        self.assertEqual(state["job_budget_checked_at"], 7300)
        self.assertFalse(state["job_budget_waiting"])

    def test_first_registered_estimate_can_replace_fallback_but_not_slide_later(self):
        jobs.save(self.path, "T1", [{"container": "job", "identity": "id@start", "observed_at": 0,
                                  "deadline_at": 21600, "runtime_limit_seconds": 21600}])
        args = ["jobs.py", "register", "--workspace", self.tmp.name, "--registry", str(self.path),
                "--task-id", "T1", "--container", "job", "--estimate-seconds", "43200", "--estimate-basis", "pilot"]
        with mock.patch.object(jobs, "inspect_container", return_value={"identity": "id@start", "alive": True, "task_id": "T1"}), \
                mock.patch.object(jobs.time, "time", return_value=100), mock.patch("sys.argv", args), mock.patch("builtins.print"):
            jobs.main()
        saved = jobs.load(self.path, "T1")[0]
        self.assertEqual(saved["deadline_at"], 64800)
        self.assertEqual(saved["estimated_runtime_seconds"], 43200)
        args[args.index("43200")] = "86400"
        with mock.patch.object(jobs, "inspect_container", return_value={"identity": "id@start", "alive": True, "task_id": "T1"}), \
                mock.patch("sys.argv", args), mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                jobs.main()
        self.assertEqual(jobs.load(self.path, "T1")[0]["deadline_at"], 64800)

    def test_provider_and_unrelated_processes_do_not_hide_a_stall(self):
        table = {10: process(10, infrastructure=True), 11: process(11, 10, infrastructure=True), 99: process(99)}
        with mock.patch.object(jobs, "process_table", return_value=table):
            self.assertEqual(jobs.poll(self.p, 601), "idle")

    def test_reparented_job_stays_tracked_then_exit_restores_agent_grace_period(self):
        with mock.patch.object(jobs, "process_table", return_value={11: process(11, 10)}):
            jobs.poll(self.p, 15)
        with mock.patch.object(jobs, "process_table", return_value={11: process(11, 1)}):
            self.assertEqual(jobs.poll(self.p, 2000), "waiting")
        with mock.patch.object(jobs, "process_table", return_value={}):
            self.assertEqual(jobs.poll(self.p, 3600), "idle")
        self.assertEqual(self.p["progress_at"], 3600)
        self.assertEqual(self.p["resumed_at"], 3600)

    def test_pid_reuse_and_zombies_cannot_keep_a_task_alive(self):
        jobs.save(self.path, "T1", [{"pid": 11, "identity": "old-start"}])
        for row in (process(11, identity="new-start"), process(11, identity="old-start", state="Z")):
            self.p.pop("jobs_sampled_at", None)
            with mock.patch.object(jobs, "process_table", return_value={11: row}):
                self.assertEqual(jobs.poll(self.p, 601), "idle")

    def test_inspection_failure_protects_existing_work_and_wait_has_a_limit(self):
        jobs.save(self.path, "T1", [{"pid": 11, "identity": "11@start"}])
        with mock.patch.object(jobs, "process_table", side_effect=OSError("unavailable")):
            self.assertEqual(jobs.poll(self.p, 15), "waiting")
            self.assertEqual(jobs.poll(self.p, 21616), "expired")
        self.assertTrue(self.p["job_inspection_unavailable"])

    def test_detached_container_requires_matching_task_and_start_identity(self):
        jobs.save(self.path, "T1", [{"container": "job-container", "identity": "id@start"}])
        with mock.patch.object(jobs, "process_table", return_value={}), \
                mock.patch.object(jobs, "inspect_container", return_value={"alive": True, "identity": "id@start", "task_id": "T1"}):
            self.assertEqual(jobs.poll(self.p, 15), "waiting")
        with mock.patch.object(jobs, "process_table", return_value={}), \
                mock.patch.object(jobs, "inspect_container", return_value={"alive": True, "identity": "id@restart", "task_id": "T1"}):
            self.assertEqual(jobs.poll(self.p, 30), "idle")

    def test_removed_container_is_finished_but_daemon_failure_is_unknown(self):
        with mock.patch.object(jobs.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="Error: No such container: test")):
            self.assertFalse(jobs.inspect_container("test")["alive"])
        with mock.patch.object(jobs.subprocess, "run", return_value=mock.Mock(returncode=1, stderr="Cannot connect to Docker daemon")):
            with self.assertRaises(OSError):
                jobs.inspect_container("test")

    def test_new_registration_bypasses_cached_idle_decision(self):
        with mock.patch.object(jobs, "process_table", return_value={}):
            self.assertEqual(jobs.poll(self.p, 15), "idle")
        jobs.save(self.path, "T1", [{"pid": 11, "identity": "11@start"}])
        with mock.patch.object(jobs, "process_table", return_value={11: process(11)}):
            self.assertEqual(jobs.poll(self.p, 16), "waiting")



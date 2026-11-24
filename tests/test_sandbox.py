import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, sandbox

FAKE = [sys.executable, str(Path(__file__).parent / "fakedocker.py")]


def spec_dir(root, dockerfile="FROM python:3.12-slim\nRUN pip install flask\n", **over):
    d = Path(root) / "sandbox"
    d.mkdir(parents=True, exist_ok=True)
    if dockerfile is not None:
        (d / "Dockerfile").write_text(dockerfile)
    js = {"ports": [8000], "tools": ["python3 --version", "pip --version"], "run": "python app.py", "smoke": "python -c 'print(1)'"}
    js.update(over)
    (d / "sandbox.json").write_text(json.dumps(js))
    (d / "prereqs.md").write_text("- python3: runs the app\n- flask: web server\n- pip: installs packages\n")
    return d


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / "docker.log"
        env = {"FAKE_DOCKER_LOG": str(self.log), "FAKE_DOCKER_FAIL": ""}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.p = mock.patch.object(sandbox, "DOCKER", FAKE)
        self.p.start()
        self.ws = self.tmp / "proj"
        (self.ws / ".duo" / "r1").mkdir(parents=True)

    def tearDown(self):
        self.p.stop()
        self.env.stop()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    # ---- availability
    def test_available_when_docker_answers(self):
        self.assertEqual(sandbox.available(), (True, ""))

    def test_unavailable_explains_how_to_fix(self):
        os.environ["FAKE_DOCKER_FAIL"] = "info"
        ok, why = sandbox.available()
        self.assertFalse(ok)
        self.assertIn("Docker Desktop", why)

    def test_unavailable_when_docker_is_not_installed(self):
        with mock.patch.object(sandbox, "DOCKER", ["/no/such/docker"]):
            ok, why = sandbox.available()
        self.assertFalse(ok)
        self.assertIn("not installed", why)

    # ---- spec checks
    def test_good_spec_has_no_problems(self):
        self.assertEqual(sandbox.check_spec(spec_dir(self.tmp)), [])

    def test_spec_problems_are_named(self):
        d = Path(tempfile.mkdtemp())
        self.assertTrue(any("Dockerfile" in p for p in sandbox.check_spec(d)))
        for bad, needle in (({"tools": []}, "tools"), ({"run": ""}, "run"), ({"smoke": ""}, "smoke"), ({"ports": [80]}, "ports"),
                            ({"ports": ["x"]}, "ports")):
            self.assertTrue(any(needle in p for p in sandbox.check_spec(spec_dir(Path(tempfile.mkdtemp()), **bad))), bad)
        self.assertTrue(any("FROM" in p for p in sandbox.check_spec(spec_dir(Path(tempfile.mkdtemp()), dockerfile="RUN ls\n"))))

    def test_review_file_required_for_the_check_turn(self):
        d = spec_dir(self.tmp)
        self.assertTrue(any("review.md" in p for p in sandbox.check_spec(d, review=True)))
        (d / "review.md").write_text("Missing found\n- none\n")
        self.assertEqual(sandbox.check_spec(d, review=True), [])

    def test_invalid_json_is_reported_not_raised(self):
        d = spec_dir(self.tmp)
        (d / "sandbox.json").write_text("{nope")
        self.assertTrue(any("sandbox.json" in p for p in sandbox.check_spec(d)))

    # ---- build, run, tools, smoke
    def test_build_image_uses_the_sandbox_folder_only(self):
        d = spec_dir(self.ws / ".duo" / "r1")
        ok, log = sandbox.build_image(d, "r1")
        self.assertTrue(ok)
        build = next(c for c in self.calls() if c[0] == "build")
        self.assertEqual(build[build.index("-t") + 1], "duo-r1")
        self.assertEqual(build[-1], str(d))                     # context is the sandbox folder, not the project

    def test_build_failure_returns_the_log_tail(self):
        d = spec_dir(self.ws / ".duo" / "r1", dockerfile="FROM x\nRUN BROKEN\n")
        ok, log = sandbox.build_image(d, "r1")
        self.assertFalse(ok)
        self.assertIn("Unable to locate package", log)

    def test_container_is_limited_and_only_mounts_the_project(self):
        ok, _ = sandbox.start_container(self.ws, "r1", [8000])
        self.assertTrue(ok)
        run = next(c for c in self.calls() if c[0] == "run")
        flat = " ".join(run)
        self.assertIn("--security-opt no-new-privileges", flat)
        self.assertIn("-v %s:/work" % self.ws, flat)
        self.assertIn("127.0.0.1:8000:8000", flat)
        self.assertIn("--label duo=1", flat)
        for banned in ("--privileged", "--network host", "docker.sock", "-v /:"):
            self.assertNotIn(banned, flat)
        self.assertEqual(flat.count("-v "), 1)

    def test_old_container_is_removed_before_a_new_one(self):
        sandbox.start_container(self.ws, "r1", [])
        cmds = [c[0] + (" " + c[1] if c[0] == "rm" else "") for c in self.calls()]
        self.assertLess(cmds.index("rm -f"), cmds.index("run"))

    def test_wrapper_runs_commands_in_the_container(self):
        sandbox.write_wrapper(self.ws, "r1")
        w = self.ws / ".duo" / "sbx"
        self.assertTrue(w.stat().st_mode & stat.S_IXUSR)
        text = w.read_text()
        self.assertIn("docker exec", text)
        self.assertIn("duo-r1", text)
        self.assertIn('"$*"', text)

    def test_tools_are_checked_one_by_one(self):
        self.assertEqual(sandbox.check_tools("r1", ["python3 --version", "pip --version"]), (True, ""))
        os.environ["FAKE_DOCKER_FAIL"] = "tool:pip"
        ok, log = sandbox.check_tools("r1", ["python3 --version", "pip --version"])
        self.assertFalse(ok)
        self.assertIn("pip --version", log)

    def test_smoke_runs_in_a_fresh_container(self):
        ok, _ = sandbox.smoke(self.ws, "r1", {"smoke": "echo ok"})
        self.assertTrue(ok)
        run = next(c for c in self.calls() if c[0] == "run")
        self.assertIn("--rm", run)
        os.environ["FAKE_DOCKER_FAIL"] = "smoke"
        ok, log = sandbox.smoke(self.ws, "r1", {"smoke": "echo ok"})
        self.assertFalse(ok)
        self.assertIn("did not start", log)

    def test_stop_all_removes_every_duo_container(self):
        sandbox.stop_all()
        calls = self.calls()
        self.assertEqual(calls[0][:4], ["ps", "-aq", "--filter", "label=duo=1"])
        self.assertTrue(any(c[:2] == ["rm", "-f"] and "abc123" in c and "def456" in c for c in calls))

    def test_dockerfile_hash_changes_with_the_file(self):
        d = spec_dir(self.tmp)
        a = sandbox.dockerfile_hash(d)
        (d / "Dockerfile").write_text("FROM node:22\n")
        self.assertNotEqual(a, sandbox.dockerfile_hash(d))


if __name__ == "__main__":
    unittest.main()

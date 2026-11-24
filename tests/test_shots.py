import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from engine import sandbox, shots


class FakeRunner:
    def __init__(self, out_dir, returncode=0, write_png=True, raises=None):
        self.out_dir = out_dir
        self.returncode = returncode
        self.write_png = write_png
        self.raises = raises
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if self.raises is not None:
            raise self.raises
        if self.write_png:
            (self.out_dir / cmd[-1].split("/out/", 1)[1]).write_bytes(b"\x89PNG fake")
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr="")


class ShotsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "ws"
        self.run_dir = self.root / "run"
        self.ws.mkdir()
        self.run_dir.mkdir()
        self.shots_dir = self.run_dir / "shots"

    def tearDown(self):
        self._tmp.cleanup()

    def runner(self, returncode=0, write_png=True, raises=None):
        return FakeRunner(self.shots_dir, returncode, write_png, raises)

    def test_no_ports_writes_smoke_text(self):
        res = shots.capture(self.ws, "abc", self.run_dir, 1, None, "smoke ok", runner=self.runner())
        self.assertEqual(res["kind"], "text")
        self.assertEqual(res["file"], "round-1.txt")
        self.assertIsNone(res["warning"])
        self.assertEqual((self.shots_dir / "round-1.txt").read_text(), "smoke ok")

    def test_empty_ports_list_writes_smoke_text(self):
        res = shots.capture(self.ws, "abc", self.run_dir, 1, [], "smoke ok", runner=self.runner())
        self.assertEqual(res["kind"], "text")
        self.assertEqual(res["file"], "round-1.txt")

    def test_success_returns_image(self):
        r = self.runner(returncode=0, write_png=True)
        res = shots.capture(self.ws, "abc", self.run_dir, 2, [8000], "smoke", runner=r)
        self.assertEqual(res["kind"], "image")
        self.assertEqual(res["file"], "round-2.png")
        self.assertIsNone(res["warning"])
        self.assertTrue((self.shots_dir / "round-2.png").exists())

    def test_command_uses_docker_and_add_host_and_port(self):
        r = self.runner(returncode=0, write_png=True)
        shots.capture(self.ws, "abc", self.run_dir, 2, [8000], "smoke", runner=r)
        cmd = r.calls[0][0]
        self.assertEqual(cmd[0], sandbox.DOCKER[0])
        self.assertIn("--add-host=host.docker.internal:host-gateway", cmd)
        self.assertIn("8000", cmd)

    def test_nonzero_returncode_falls_back_to_text(self):
        r = self.runner(returncode=1, write_png=False)
        res = shots.capture(self.ws, "abc", self.run_dir, 3, [8000], "smoke", runner=r)
        self.assertEqual(res["kind"], "text")
        self.assertEqual(res["file"], "round-3.txt")
        self.assertTrue(res["warning"].startswith("Screenshot failed"))
        self.assertEqual((self.shots_dir / "round-3.txt").read_text(), "smoke")

    def test_timeout_is_caught(self):
        r = self.runner(raises=subprocess.TimeoutExpired(cmd="docker", timeout=90))
        res = shots.capture(self.ws, "abc", self.run_dir, 4, [8000], "smoke", runner=r)
        self.assertEqual(res["kind"], "text")
        self.assertTrue(res["warning"])
        self.assertTrue(res["warning"].startswith("Screenshot failed"))
        self.assertEqual((self.shots_dir / "round-4.txt").read_text(), "smoke")

    def test_zero_returncode_without_png_is_failure(self):
        r = self.runner(returncode=0, write_png=False)
        res = shots.capture(self.ws, "abc", self.run_dir, 5, [8000], "smoke", runner=r)
        self.assertEqual(res["kind"], "text")
        self.assertEqual(res["file"], "round-5.txt")
        self.assertTrue(res["warning"])


if __name__ == "__main__":
    unittest.main()

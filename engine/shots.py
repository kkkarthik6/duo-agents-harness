"""Screenshots of a running app. A sidecar container with Playwright loads the app's port and saves a PNG.

Any failure falls back to the smoke test text, so a round never stops because a screenshot could not be taken.
"""
import subprocess
from pathlib import Path

from . import sandbox

SIDECAR_IMAGE = "mcr.microsoft.com/playwright/python:v1.47.0-jammy"
SHOT_SCRIPT = """import sys
from playwright.sync_api import sync_playwright
port, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto("http://host.docker.internal:" + port, timeout=30000)
    page.wait_for_timeout(2000)
    page.screenshot(path=out)
    browser.close()
"""


def sidecar_cmd(sid, port, out_dir, out_name):
    return list(sandbox.DOCKER) + ["run", "--rm", "--add-host=host.docker.internal:host-gateway", "-v",
                                   str(out_dir) + ":/out", SIDECAR_IMAGE, "python3", "-c", SHOT_SCRIPT,
                                   str(port), "/out/" + out_name]


def capture(ws, sid, run_dir, round_no, ports, smoke_out, runner=None, timeout=90):
    shots_dir = Path(run_dir) / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    txt = "round-%d.txt" % round_no

    def text(warning):
        (shots_dir / txt).write_text(smoke_out or "")
        return {"kind": "text", "file": txt, "warning": warning}

    if not ports:
        return text(None)
    out_name = "round-%d.png" % round_no
    run = runner or subprocess.run
    try:
        r = run(sidecar_cmd(sid, ports[0], shots_dir, out_name), capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return text("Screenshot failed: %s" % type(e).__name__)
    if r.returncode != 0:
        return text("Screenshot failed: exit code %s" % r.returncode)
    if not (shots_dir / out_name).exists():
        return text("Screenshot failed: no image was written")
    return {"kind": "image", "file": out_name, "warning": None}

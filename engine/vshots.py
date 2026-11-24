"""Screenshots of the screens a feedback version changes (core 1.4.0).

Duo starts a private engine on a free local port with the version copy's features, opens each changed
screen in headless Chrome, and saves PNG files next to the copy (versions/<id>.shots/). Pages are opened
at #/f/<feature>/<page>; panels in the home or header slot are seen on #/home. Without Chrome, Duo takes
no screenshots and says so.
"""
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import features

MAC_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Chromium.app/Contents/MacOS/Chromium")
NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
SIZE = "1280,860"


def find_chrome():
    env = os.environ.get("DUO_CHROME")
    if env and Path(env).exists():
        return env
    for n in NAMES:
        p = shutil.which(n)
        if p:
            return p
    return next((p for p in MAC_PATHS if Path(p).exists()), None)


def screens(components, names):
    """Addresses to photograph for the changed features: their pages, then Home if they show panels there."""
    out = []
    for c in components:
        if c["feature"] in names and c["kind"] == "page":
            _, f, pid = c["key"].split(":", 2)
            out.append("#/f/%s/%s" % (f, pid))
    if any(c["feature"] in names and c["kind"] == "panel" and c.get("slot") in ("home", "header") for c in components):
        out.append("#/home")
    return list(dict.fromkeys(out))


def _shoot(chrome, url, out, timeout=60):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check",
           "--window-size=" + SIZE, "--virtual-time-budget=8000", "--screenshot=" + str(out), url]
    if os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return Path(out).is_file() and Path(out).stat().st_size > 0


def take(clone, changed, base=None):
    """Screenshots for the changed features of the copy. Returns ([png paths], note)."""
    chrome = find_chrome()
    if not chrome:
        return [], "No Chrome found, so Duo took no screenshots. Install Google Chrome to get them."
    clone = Path(clone)
    names = {p.split("/")[1] for p in changed if p.startswith("features/") and p.count("/") >= 2}
    reg = features.load(clone)
    todo = screens(reg.components, names)
    if not todo:
        return [], "The change has no page or Home panel to photograph."
    from .server import Engine, make_server
    prev = features.active()
    srv = make_server(0, Engine(agents=[], tick=False, home=base, user_dir=clone))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    out_dir = clone.parent / (clone.name + ".shots")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    pics = []
    try:
        for i, h in enumerate(todo, 1):
            out = out_dir / ("%d.png" % i)
            if _shoot(chrome, "http://127.0.0.1:%d/?snapshot=1%s" % (port, h), out):
                pics.append(str(out))
    finally:
        srv.shutdown()
        features.activate(prev)
    note = "" if len(pics) == len(todo) else "Chrome took %d of %d screenshots." % (len(pics), len(todo))
    return pics, note

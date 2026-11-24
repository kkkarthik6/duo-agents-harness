"""Build a Session from partial state the way production does: with a workspace and a run folder."""
import atexit
import shutil
import tempfile
from pathlib import Path

from engine import session

_ROOTS = []


def _scratch():
    root = tempfile.mkdtemp(prefix="duo-test-session-")
    _ROOTS.append(root)
    return Path(root)


def _cleanup():
    for root in _ROOTS:
        shutil.rmtree(root, ignore_errors=True)


atexit.register(_cleanup)


def make_session(state=None):
    """Session(state) with workspace, duo_dir and id filled in when the test did not set them."""
    full = dict(state or {})
    workspace = Path(full["workspace"]) if full.get("workspace") else _scratch()
    full.setdefault("workspace", str(workspace))
    run_dir = Path(full["duo_dir"]) if full.get("duo_dir") else workspace / ".duo" / "test-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    full["duo_dir"] = str(run_dir)
    full.setdefault("id", "test-run")
    return session.Session(full)

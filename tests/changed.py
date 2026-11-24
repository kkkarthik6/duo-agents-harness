"""Run only the test modules that a change can affect.

  python3 -m tests.changed            # test what differs from HEAD (working tree and staged)
  python3 -m tests.changed --all      # the full suite, the release gate
  python3 -m tests.changed engine/models.py   # test as if these files changed

A file that is not in the map runs the full suite, so nothing is skipped by accident.
"""
import fnmatch
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Source or test file pattern -> test modules it can affect.
MAP = [
    ("engine/phases.py",     ["test_phases", "test_session", "test_steering", "test_integration"]),
    ("engine/session.py",    ["test_session", "test_session_sandbox", "test_server", "test_delete_run",
                              "test_phases", "test_steering", "test_helper", "test_integration"]),
    ("engine/server.py",     ["test_server", "test_delete_run", "test_helper", "test_settings"]),
    ("engine/models.py",     ["test_models", "test_session"]),
    ("engine/agents.py",     ["test_agents", "test_cli", "test_session"]),
    ("engine/sandbox.py",    ["test_sandbox", "test_session_sandbox", "test_shots"]),
    ("engine/shots.py",      ["test_shots", "test_server"]),
    ("engine/security.py",   ["test_security", "test_session"]),
    ("engine/insights.py",   ["test_insights", "test_server"]),
    ("engine/decide.py",     ["test_decide", "test_session"]),
    ("engine/references.py", ["test_references", "test_session"]),
    ("engine/rubrics.py",    ["test_rubrics", "test_routing"]),
    ("engine/routing.py",    ["test_routing", "test_session"]),
    ("engine/personal.py",   ["test_personal", "test_settings"]),
    ("engine/settings.py",   ["test_settings", "test_server"]),
    ("engine/history.py",    ["test_history", "test_insights"]),
    ("engine/memory.py",     ["test_memory", "test_session"]),
    ("engine/ste.py",        ["test_ste", "test_session"]),
    ("engine/protocol.py",   ["test_protocol", "test_session"]),
    ("engine/bounds.py",     ["test_bounds", "test_session"]),
    ("engine/readiness.py",  ["test_readiness", "test_server"]),
    ("web/index.html",       ["test_server", "test_delete_run"]),
    ("engine/helper.py",     ["test_helper", "test_server"]),
    ("engine/tmux.py",       ["test_session", "test_server"]),
    ("engine/trust.py",      ["test_trust", "test_server"]),
    ("engine/notify.py",     ["test_session"]),
    ("engine/selfdev.py",    ["test_selfdev", "test_selfdev_flow"]),
    ("bin/duo",              ["test_cli", "test_server"]),
    ("tests/changed.py",     ["test_cli"]),
    ("tests/test_*.py",      []),   # a changed test file runs itself, added below
]
IGNORE = ("__pycache__/*", "*.pyc", "docs/*", "*.md")


def changed_files():
    out = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=all"],
                         capture_output=True, text=True, check=True).stdout
    files = [line[3:].split(" -> ")[-1] for line in out.splitlines() if line.strip()]
    return [f for f in files if not any(fnmatch.fnmatch(f, pat) for pat in IGNORE)]


def modules_for(files):
    """Return (modules, reason). reason is 'all' when a file is not mapped."""
    mods = set()
    for f in files:
        if fnmatch.fnmatch(f, "tests/test_*.py"):
            mods.add(Path(f).stem)
            continue
        hit = [m for pat, m in MAP if fnmatch.fnmatch(f, pat)]
        if not hit:
            return None, f
        for group in hit:
            mods.update(group)
    return sorted(mods), None


def main(argv):
    if argv == ["--all"]:
        files, mods = None, None
    elif argv:
        files = argv
    else:
        files = changed_files()
        if not files:
            print("Nothing changed that affects the tests.")
            return 0
        print("Changed: " + ", ".join(files))
    if files is not None:
        mods, unmapped = modules_for(files)
        if mods is None:
            print("No map entry for %s, so running the full suite." % unmapped)
    names = ["tests." + m for m in mods] if mods else None
    suite = (unittest.defaultTestLoader.loadTestsFromNames(names) if names
             else unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)))
    print("Running: " + (", ".join(mods) if names else "the full suite"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

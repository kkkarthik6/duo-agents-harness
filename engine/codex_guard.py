"""Duo calls Codex only through ephemeral exec, which saves no session and writes nothing to the user's Codex history.
This looks for the opposite: a saved Codex session whose first lines are a Duo assignment, created while a run was active.
It never deletes or edits anything. It only reports, so a regression is noticed instead of quietly filling the user's list."""
import datetime
import os
from pathlib import Path

MARKERS = ("DUO assignment:", "DUO turn #", "Duo starts a fresh ephemeral call")
HEAD_BYTES = 200_000


def sessions_dir():
    from . import agents
    return Path(agents.CODEX_HOME) / "sessions"


def _day_folders(root, since, now):
    day = datetime.date.fromtimestamp(since)
    end = datetime.date.fromtimestamp(now)
    while day <= end:
        yield Path(root) / ("%04d" % day.year) / ("%02d" % day.month) / ("%02d" % day.day)
        day += datetime.timedelta(days=1)


def _is_duo(path):
    try:
        with open(path, "r", errors="replace") as handle:
            head = handle.read(HEAD_BYTES)
    except OSError:
        return False
    return any(marker in head for marker in MARKERS)


def leaked(since, root=None, now=None):
    """Saved Codex sessions created since `since` (epoch seconds) that begin with a Duo prompt."""
    root = Path(root) if root else sessions_dir()
    now = now or datetime.datetime.now().timestamp()
    found = []
    for folder in _day_folders(root, since, now):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("rollout-*.jsonl")):
            try:
                created = path.stat().st_mtime
            except OSError:
                continue
            if created >= since - 1 and _is_duo(path):
                found.append(str(path))
    return found

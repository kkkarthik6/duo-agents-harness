"""Run-owned terminal sessions. A saved pane number alone is never ownership."""
import hashlib
import time
from pathlib import Path

from . import config
from .tmux import tmux, pane_dead


def name(state):
    return state.get('worker_session') or config.CFG['tmux_session']


def new_name(state):
    identity = str(Path(state['workspace']).resolve()) + '\0' + state['id']
    return 'duo-run-' + hashlib.sha256(identity.encode()).hexdigest()[:20]


def owned(state):
    target = name(state)
    owner = tmux('display-message', '-p', '-t', target, '#{@duo_run_id}', check=False).strip()
    if owner:
        return owner == state['id']
    if state.get('worker_session') or not state.get('panes') or not state.get('workspace'):
        return False
    # Legacy workers have no tag. Verify their session and working directory.
    root = Path(state['workspace']).resolve()
    for pane in state['panes'].values():
        fields = tmux('display-message', '-p', '-t', pane,
                      '#{session_name}\t#{pane_current_path}', check=False).strip().split('\t')
        if len(fields) != 2 or fields[0] != target or Path(fields[1]).resolve() != root:
            return False
    return True


def attached(state):
    if not state.get('panes') or not owned(state):
        return False
    target = name(state)
    return all(tmux('display-message', '-p', '-t', p,
               '#{session_name}', check=False).strip() == target for p in state['panes'].values())


def alive(state):
    return attached(state) and all(not pane_dead(p) for p in state['panes'].values())


def close(state):
    if owned(state):
        tmux('kill-session', '-t', name(state), check=False)


PREFIX = 'duo-run-'
GRACE = 600          # a session younger than this is never swept: its run may still be starting


def sweep(keep_ids, now=None):
    """Close Duo worker sessions that no active run owns, so they do not pile up (each holds panes and memory).
    A session stays when its run is active, someone has it open in a terminal, or it is brand new."""
    now = time.time() if now is None else now
    out = tmux('list-sessions', '-F', '#{session_name}\t#{@duo_run_id}\t#{session_attached}\t#{session_created}', check=False)
    closed = []
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) != 4 or not parts[0].startswith(PREFIX):
            continue
        session_name, run_id, attached, created = parts
        if run_id in keep_ids or attached not in ('', '0'):
            continue
        try:
            if now - float(created) < GRACE:
                continue
        except ValueError:
            continue
        tmux('kill-session', '-t', session_name, check=False)
        closed.append(session_name)
    return closed

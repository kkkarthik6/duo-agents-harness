"""Run-owned terminal sessions. A saved pane number alone is never ownership."""
import hashlib
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

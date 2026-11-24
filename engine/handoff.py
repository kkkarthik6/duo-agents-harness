"""The brief every agent gets at the start of a turn, and the run's memory file.

Generated from the run's saved state, never written by a model, so it cannot drift from what happened.
It always carries what the next agent must not lose (the task, constraints, the plan, open defects,
files changed, the last handoff) and compresses what it can recover from files (older turns).
Nothing the next agent needs is cut: the ask, constraints, messages, open defects, files and the last
handoff go in full. Only turns older than the last few collapse to counts, since their files stay on disk."""
import os

from . import roleflow

RECENT_TURNS = 3

NAMES = {'lead': 'Planner', 'challenger': 'Plan reviewer', 'builder': 'Coder', 'critic': 'Code reviewer'}
TIER_RULE = {
    'large': 'You carry the judgment: resolve ambiguity, set the steps, and say what to check.',
    'medium': 'You check and decide; do not rebuild the work yourself.',
    'small': 'Execute the plan literally, step by step. Do not redesign it. If a step is unclear or impossible, stop and report the blocker instead of guessing.',
}
TASK = {
    'exchange': 'Write the implementation plan: steps a small coder can follow literally, each with an output path and an observable check.',
    'plan_review': 'Review the plan. Quote what you object to. List every required correction now.',
    'build': 'Implement the accepted plan. Finish the whole output, run the relevant checks and report their real results.',
    'revise': 'Fix every required defect below, together, then re-run the affected checks.',
    'critique': 'Review the build against the plan, the requirements and the saved check evidence. List every defect at once.',
    'verify': 'Verify the requested output against every requirement.',
    'secure': 'Review the changes for security defects and report each with a fix.',
}
READER = {'exchange': 'challenger', 'plan_review': 'builder', 'build': 'critic', 'revise': 'critic',
          'critique': 'builder', 'verify': 'critic', 'secure': 'builder'}


def _clip(text, limit=None):
    text = ' '.join(str(text or '').split())
    return text if limit is None or len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _block(text, limit=None):
    """Like _clip but keeps one item per line, so a numbered list of defects stays a list."""
    lines = [' '.join(l.split()) for l in str(text or '').splitlines() if l.strip()]
    out = '\n'.join(lines)
    return out if limit is None or len(out) <= limit else out[:limit - 1].rstrip() + '…'


def _agent_turns(state):
    return [e for e in state.get('entries', []) if e.get('author') not in ('human', 'system')
            and e.get('kind') not in ('warning', 'error', 'recovery', 'restart', 'review_note') and not e.get('invalid')]


def _role_line(state, role):
    spec = (state.get('_role_models') or {}).get(role) or {}
    tier = spec.get('size') or ''
    model = spec.get('model') or ''
    return NAMES.get(role, role), tier, model


def open_defects(state):
    """The latest review that asked for changes, unless a later build already answered it."""
    last_review = None
    for e in state.get('entries', []):
        if e.get('kind') in ('critique', 'plan_review', 'verify', 'secure') and e.get('verdict') == 'REVISE':
            last_review = e
        elif e.get('kind') in ('build', 'revise', 'exchange') and last_review and e.get('n', 0) > last_review.get('n', 0):
            last_review = None
    return last_review


def files_changed(state):
    seen = []
    for e in state.get('entries', []):
        for f in e.get('changed') or []:
            if not f.startswith(('.duo/', 'output/')) and f not in seen:
                seen.append(f)
    return seen


def _progress(state, rel):
    turns = _agent_turns(state)
    old, recent = turns[:-RECENT_TURNS], turns[-RECENT_TURNS:]
    lines = []
    if old:
        counts = {}
        for e in old:
            counts[e['kind']] = counts.get(e['kind'], 0) + 1
        lines.append('- earlier: ' + ', '.join('%d %s' % (n, k) for k, n in counts.items()))
    for e in recent:
        verdict = ' [%s]' % e['verdict'] if e.get('verdict') else ''
        where = ' (%s)' % os.path.normpath(rel + '/' + e['file']) if e.get('file') else ''
        lines.append('- #%s %s%s: %s%s' % (e['n'], e['kind'], verdict, _clip(e.get('summary') or e.get('prose')), where))
    return lines


def quick_brief(state):
    """A quick task is one turn by one agent: the ask, the user's constraints, and how to do it, nothing else."""
    asks = [e for e in state.get('entries', []) if e.get('kind') in ('task', 'followup')]
    out = ['# Quick task']
    if asks:
        out.append('Ask: ' + _block(asks[0].get('prose')))
        out += ['Follow-up: ' + _block(e.get('prose')) for e in asks[1:]]
    notes = [_block(e.get('prose')) for e in state.get('entries', []) if e.get('author') == 'human' and e.get('kind') == 'message']
    notes = list(state.get('constraints') or []) + notes
    if notes:
        out.append('User constraints and messages (all still apply):')
        out += ['- ' + n for n in notes]
    out.append('Do it in one pass, as you would alone: write the code, run its tests once with the project\'s Python, and reply with the files '
               'and the real check results. Do not repeat checks across Python versions, and do not add files, docs or notes nobody asked for.')
    return '\n'.join(out) + '\n'


def brief(state, kind=None, rel='.duo', patch=None):
    """The brief for one turn of `kind`, or the run memory when kind is None."""
    role = roleflow.role_for(kind) if kind else None
    out = []
    if role and kind == 'build' and state.get('size') == 'trivial' and not state.get('quick_review'):
        return quick_brief(state)
    asks = [e for e in state.get('entries', []) if e.get('kind') in ('task', 'followup')]
    if role:
        name, tier, model = _role_line(state, role)
        out.append('# Handoff brief')
        out.append('You are the %s (%s model%s). %s' % (name, tier or 'unrated', ', ' + model if model else '',
                                                        TIER_RULE.get(tier, '')))
        quick = state.get('size') == 'trivial'
        reader = READER.get(kind) if not (quick and kind == 'build' and not state.get('quick_review')) else None
        if reader:
            rname, rtier, _ = _role_line(state, reader)
            out.append('Your handoff is read next by the %s (%s model, fresh session): it sees this brief, your reply and the files you name. '
                       'State what you did, the files, the checks with real results, and what is left.' % (rname, rtier or 'unrated'))
        task_now = ('A quick one-agent attempt did not finish. Plan the remaining work from what exists (the files and the last handoff below), '
                    'in steps a small coder can follow literally. Do not redo what already works.'
                    ) if kind == 'exchange' and state.get('escalated') else ('Do the task directly, as you would alone: make the change, run it or its tests to check it, and report the files and the real check results. '
                    'There is no separate plan or reviewer.') if quick and kind == 'build' and not state.get('quick_review') else TASK.get(kind, 'Do the assigned work.')
        out.append('Your task now: ' + task_now)
    else:
        out.append('# Run memory: %s' % _clip(state.get('title'), 120))
        out.append('Status %s, phase %s, round %s of %s, cycle %s.' % (state.get('status'), state.get('phase') or 'start',
                                                                       state.get('round', 0), state.get('max_rounds'), state.get('cycle', 1)))
    if asks:
        first, later = asks[0], asks[1:]
        out.append('Original ask: ' + _block(first.get('prose')))
        for e in later:
            out.append('Follow-up %s: %s' % (e.get('cycle', ''), _block(e.get('prose'))))
    notes = [e for e in state.get('entries', []) if e.get('author') == 'human' and e.get('kind') == 'message']
    notes = [_block(e.get('prose')) for e in notes]
    if state.get('constraints') or notes:
        out.append('User constraints and messages (all still apply):')
        out += ['- ' + c for c in (state.get('constraints') or []) if c not in notes] + ['- ' + n for n in notes]
    if state.get('size') != 'trivial':
        plan_state = 'approved' if state.get('plan_verdict') == 'APPROVED' else 'not yet approved'
        out.append('Plan: %s (%s). Extra stages requested: %s.' % (os.path.normpath(rel + '/plan.md'), plan_state,
                                                                    ', '.join(state.get('stage_requests') or {}) or 'none'))
    progress = _progress(state, rel)
    if progress:
        out.append('Progress so far:')
        out += progress
    changed = files_changed(state)
    if changed:
        out.append('Files changed in this run: ' + ', '.join(changed))
    last_build = next((e for e in reversed(state.get('entries', [])) if e.get('kind') in ('build', 'revise')), None)
    if last_build and kind != 'build':
        out.append('Last coder handoff (%s): %s' % (os.path.normpath(rel + '/' + last_build['file']) if last_build.get('file') else 'saved reply',
                                                    _block(last_build.get('prose'))))
    if patch:
        out.append('Diff of the latest build: ' + patch)
    review = open_defects(state)
    if review:
        out.append('OPEN DEFECTS from the %s (#%s), unresolved until a later build answers them:' % (review['kind'], review['n']))
        out.append(_block(review.get('prose')))
    return '\n'.join(out) + '\n'

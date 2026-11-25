"""Four independent role instances; provider and model identity are not authorship."""
import hashlib
import json
import secrets
from pathlib import Path
from . import config, model_catalog, cli_adapters, agent_failures

ROLES = ('lead', 'challenger', 'builder', 'critic')
ALIASES = {'architect': 'lead', 'analyst': 'lead', 'helper': 'lead', 'scout': 'lead', 'researcher': 'lead',
           'reviewer': 'critic', 'verifier': 'critic', 'security': 'critic', 'checker': 'critic'}
KINDS = {'exchange': 'lead', 'plan': 'lead', 'goal_assign': 'lead', 'goal_assess': 'lead',
         'assess': 'lead', 'research': 'lead', 'research_expand': 'lead', 'scout': 'lead', 'refs': 'lead', 'equip': 'lead', 'plan_review': 'challenger', 'plan_challenge': 'challenger',
         'build': 'builder', 'revise': 'builder', 'setup': 'builder', 'setup_fix': 'builder'}


def rubric(role):
    return json.loads((config.APP_DIR / 'rubrics/agent-roles.json').read_text())[role]


NEAREST = {'large': ('medium', 'small'), 'medium': ('large', 'small'), 'small': ('medium', 'large')}


def _cli_defaults(state, now=None):
    """One row per usable CLI that lets the CLI choose its own model. This is what a user whose models Duo has
    never rated gets, so a run can always start."""
    rows = []
    for cli in state.get('agents', []):
        try:
            adapter = cli_adapters.get(cli)
        except ValueError:
            continue
        if not {'tools', 'fresh'} <= set(adapter.capabilities) or not agent_failures.available(state, cli, None, now):
            continue
        if state.get('dynamic_models'):
            from . import model_discovery
            snapshot = model_discovery.refresh(cwd=state.get('workspace') or state.get('folder'))
            if not any(c['id'] == cli and c['installed'] and c['status'] == 'ready' for c in snapshot['clis']):
                continue
        rows.append({'cli': cli, 'id': None, 'provider': adapter.provider, 'default_model': True})
    return rows


def _rows_for(state, tier, now=None):
    """The models for a role's tier; else the nearest tier that has any; else each CLI's own default model.
    Returns (rows, how) where how is None for an exact match."""
    rows = model_catalog.candidates(state, tier, now)
    if rows:
        return rows, None
    for other in NEAREST[tier]:
        rows = model_catalog.candidates(state, other, now)
        if rows:
            return rows, 'nearest tier: ' + other
    rows = _cli_defaults(state, now)
    return rows, ('CLI default model' if rows else None)


def ensure(state, now=None):
    profile = {}
    state['model_fallbacks'] = []
    for role in ROLES:
        rule = rubric(role)
        rows, how = _rows_for(state, rule['tier'], now)
        override = config.CFG.get('role_models', {}).get(role, {})
        if override:
            override = dict(override)
            if 'provider' in override:
                override['provider'] = {'claude': 'anthropic', 'codex': 'openai'}.get(override['provider'], override['provider'])
            rows = [r for r in rows if r['id'] == override.get('model', r['id']) and
                    r['provider'] == override.get('provider', r['provider']) and
                    r['cli'] == override.get('cli', r['cli'])]
        if not rows:
            raise ValueError('No eligible ' + rule['tier'] + ' model in catalog for role ' + role +
                             '. No installed CLI is ready for it: install and sign in to Claude, Codex or Antigravity (`duo doctor` shows what Duo can find).')
        # Prefer a different provider for review when possible, without requiring two CLIs.
        author = profile.get('lead' if role == 'challenger' else 'builder')
        if author:
            rows.sort(key=lambda r: r['provider'] == author['provider'])
        row = rows[0]
        profile[role] = {'agent': row['cli'], 'provider': row['provider'], 'model': row['id'], 'size': rule['tier']}
        if how:
            profile[role]['fallback'] = how
            state['model_fallbacks'].append({'role': role, 'wanted': rule['tier'], 'how': how,
                                             'agent': row['cli'], 'model': row['id']})
    state['_role_models'] = profile
    state['review_independence'] = independence(profile)
    state.setdefault('roles', {}).update({r: p['agent'] for r, p in profile.items()})
    state['roles'].update({a: profile[r]['agent'] for a, r in ALIASES.items()})
    return profile


REVIEWS = (('plan', 'lead', 'challenger'), ('code', 'builder', 'critic'))


def independence(profile):
    """Reviews whose reviewer runs on the same provider as its author. Separate sessions still apply,
    but a second provider is what catches an author's blind spots, so the user is told when it is missing."""
    return [{'review': name, 'provider': profile[reviewer]['provider'], 'same_provider': True}
            for name, author, reviewer in REVIEWS
            if profile[author]['provider'] == profile[reviewer]['provider']]


def role_for(kind):
    return KINDS.get(kind, 'critic')


def assignment(state, kind, task_id):
    role = role_for(kind)
    spec = dict(ensure(state)[role], role=role, instance_id=secrets.token_hex(12), task_id=task_id)
    key = 'plan' if role in ('lead', 'challenger') else str(task_id)
    if role in ('lead', 'builder'):
        state.setdefault('role_authors', {})[key] = spec['instance_id']
    else:
        spec['author_instance'] = state.get('role_authors', {}).get(key)
        if spec['author_instance'] == spec['instance_id']:
            raise ValueError('An agent cannot review its own work')
    state.setdefault('_model_assignments', {})[role] = spec
    state.setdefault('agent_instances', []).append(dict(spec, kind=kind))
    return spec


def instructions(role):
    rule = rubric(role)
    return ('\n# Role: ' + rule['name'] + '\n' + rule['instructions'] + '\nAcceptance checks:\n' +
            '\n'.join('- ' + c for c in rule['checks']))


def plan_signature(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and p.stat().st_size else None


def plan_prompt(shared, feedback=''):
    return ('Write a complete implementation plan. Inspect the relevant source and available skills. '
            'Give each required outcome a short stable label, dependencies, outputs, and an observable success check. '
            'For an open UI decision, compare two interactions. Reuse the nearest design reference or find one relevant example. '
            'Generate one mockup only if it settles that decision and the tool is available. Give the builder its path and a short description. '
            'For open-ended research, use the closest prior work to identify one credible alternative and the cheapest test that could '
            'distinguish it. Reuse existing evidence first. Preserve fixed protocols. '
            'Do not implement product changes during planning. Resolve required review findings.\n' +
            instructions('lead') + '\n' + shared + '\n# Previous review\n' + feedback)


def review_prompt(shared, plan_path):
    return ('Review this plan in a fresh session; you did not write it. Inspect source when needed; do not edit the plan or product. '
            'Start with APPROVED or REVISE on its own line. For REVISE, list EVERY required correction now, in this one review, '
            'one per line as `plan step: problem -> fix`, quoting the plan text you object to; hold nothing back for later rounds. '
            'Tie required corrections to the user goal and current constraints. Keep optional ideas outside the acceptance checks.\n' +
            instructions('challenger') + '\n' + shared + '\n# Plan\nRead the plan at `' + plan_path + '`.')

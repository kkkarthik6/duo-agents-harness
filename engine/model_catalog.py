"""Explicit operator-maintained model capabilities. Names never determine tier.

Built-in entries are routing defaults, not a claim of current account access.
The CLI validates access at invocation; failures update run availability.
"""
from . import config, cli_adapters, agent_failures
from .antigravity_models import MODELS as ANTIGRAVITY_MODELS

DEFAULTS = [
    {'cli': cli, 'id': model, 'tier': tier, 'capabilities': ['tools'], 'available': True,
     'availability_source': 'configured; access checked on invocation'}
    for cli, model, tier in (
        ('claude', 'claude-opus-5-5', 'large'), ('claude', 'claude-sonnet-5-5', 'medium'),
        ('claude', 'claude-haiku-5-5', 'small'), ('codex', 'gpt-6-astra', 'large'),
        ('codex', 'gpt-6.1-sol', 'medium'), ('codex', 'gpt-6-luna', 'small'))
] + ANTIGRAVITY_MODELS


def entries():
    rows = config.CFG.get('model_catalog', DEFAULTS)
    if not isinstance(rows, list):
        raise ValueError('model_catalog must be a list')
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not row.get('id') or not row.get('cli') or row.get('tier') not in ('large', 'medium', 'small'):
            raise ValueError('Each model catalog entry needs cli, id, and explicit tier')
        if not isinstance(row.get('capabilities', []), list):
            raise ValueError('Model capabilities must be a list')
        key = row['cli'], row['id']
        if key in seen:
            raise ValueError('Duplicate model catalog entry: ' + str(key))
        seen.add(key)
        result.append(dict(row))
    tiers = config.CFG.get('model_tiers', {})
    if not isinstance(tiers, dict):
        raise ValueError('model_tiers must map CLI IDs to model tiers')
    indexed = {(r['cli'], r['id']): r for r in result}
    for cli, models in tiers.items():
        if not isinstance(models, dict):
            raise ValueError('Each CLI in model_tiers needs a model-to-tier mapping')
        for identifier, tier in models.items():
            if not isinstance(identifier, str) or not identifier or tier not in ('large', 'medium', 'small'):
                raise ValueError('Model tiers must be large, medium or small')
            row = indexed.get((cli, identifier))
            if row is None:
                row = {'cli': cli, 'id': identifier, 'capabilities': ['tools'], 'available': True,
                       'availability_source': 'explicit model_tiers policy; discovery determines current listing'}
                result.append(row)
                indexed[(cli, identifier)] = row
            row['tier'] = tier
    return result


def candidates(state, tier, now=None):
    result = []
    if state.get('dynamic_models'):
        from . import model_discovery
        snapshot = model_discovery.refresh(cwd=state.get('workspace') or state.get('folder'))
        state['model_discovery'] = {'checked_at': snapshot['checked_at'], 'expires_at': snapshot['expires_at'],
            'clis': [{k: c[k] for k in ('id', 'installed', 'status', 'checked_at', 'source', 'error') if k in c} for c in snapshot['clis']]}
        rows = model_discovery.catalog(snapshot, entries())
    else:
        rows = entries()
    for row in rows:
        if row['cli'] not in state['agents'] or row['tier'] != tier or row.get('available', False) is not True:
            continue
        adapter = cli_adapters.get(row['cli'])
        if not adapter.model_flag or not {'tools', 'fresh'} <= set(adapter.capabilities) or 'tools' not in row.get('capabilities', []):
            continue
        if agent_failures.available(state, row['cli'], row['id'], now):
            result.append(dict(row, provider=adapter.provider))
    return result


def live_entries(force=False, cwd=None):
    from . import model_discovery
    snapshot = model_discovery.refresh(force=force, cwd=cwd)
    return model_discovery.catalog(snapshot, entries())

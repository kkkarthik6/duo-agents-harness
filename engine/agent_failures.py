"""Structured failures and bounded availability exclusions, shared by both engines."""
import json
import time
from . import cli_adapters

AUTH_MEMORY = 900     # seconds a provider's expired login is remembered by later runs, so they do not retry it first

MARKER = 'DUO_WORKER_FAILURE:'


def classify(event):
    text = json.dumps(event).lower()
    status = event.get('api_error_status', event.get('status_code', event.get('status')))
    if any(x in text for x in ('monthly spend limit', 'spend_limit', 'spending limit', 'credit balance', 'out of credits', 'insufficient_quota')):
        kind = 'spend_limit'
    elif any(x in text for x in ('token expired', 'token has expired', 'oauth', 'authentication_error', 'authentication failed', 'authentication required', 'unauthorized', 'not logged in', 'login required', 'please log in')) or status == 401:
        kind = 'auth_expired'
    elif any(x in text for x in ('model_not_found', 'model not found', 'unsupported model', 'different model than the assigned', 'model does not exist', 'not recognized as a known model')):
        kind = 'model_unavailable'
    elif status == 429 or any(x in text for x in ('rate_limit', 'usage_limit_reached', 'rate limit', 'usage limit', 'too many requests', 'quota exceeded')):
        kind = 'rate_limit'
    elif any(x in text for x in ('permission_denied', 'permission refusal', 'permission denied')):
        kind = 'permission_denied'
    else:
        kind = 'process_failure'
    return {'kind': kind, 'retry_after': event.get('retry_after', 60), 'message': kind.replace('_', ' ')}


def from_screen(screen):
    tail = screen.rsplit('Working on assignment...', 1)[-1]
    for line in reversed(tail.splitlines()):
        if MARKER in line:
            try:
                value = json.loads(line.split(MARKER, 1)[1])
                if isinstance(value, dict) and value.get('kind'):
                    return value
            except ValueError:
                pass
    return classify({'message': tail})


def _memory_file():
    from . import config
    return config.DUO_HOME / 'availability.json'


def _remember(provider, now):
    path = _memory_file()
    try:
        known = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        known = {}
    known[provider] = {'kind': 'auth_expired', 'recorded_at': now}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(known))
    except OSError:
        pass


def seed(state, now=None):
    """Start a run knowing which logins just expired, as long as another CLI can do the work.
    Returns the providers it left out."""
    now = time.time() if now is None else now
    try:
        known = json.loads(_memory_file().read_text())
    except (OSError, ValueError):
        return []
    providers = {}
    for cli in state.get('agents', []):
        try:
            providers.setdefault(cli_adapters.get(cli).provider, []).append(cli)
        except ValueError:
            providers.setdefault(cli, []).append(cli)
    skipped = []
    for provider, row in known.items():
        fresh = isinstance(row, dict) and now - row.get('recorded_at', 0) < AUTH_MEMORY
        if fresh and provider in providers and len(providers) > 1:
            state.setdefault('agent_availability', {})[provider] = {
                'kind': 'auth_expired', 'message': 'auth expired (remembered from an earlier run)', 'until': None,
                'attempts': 1, 'recorded_at': row['recorded_at'], 'remembered': True}
            skipped.append(provider)
    return skipped


def record(state, cli, model, failure, now=None):
    now = time.time() if now is None else now
    try:
        provider = cli_adapters.get(cli).provider
    except ValueError:
        provider = cli
    kind = failure['kind']
    # Spending and login failures affect the account, across all its models and CLIs.
    scope = provider if kind in ('spend_limit', 'auth_expired', 'rate_limit') else cli + ':' + str(model)
    previous = state.setdefault('agent_availability', {}).get(scope, {})
    attempts = previous.get('attempts', 0) + 1
    delay = failure.get('retry_after', 60)
    try:
        delay = max(1, min(float(delay), 86400))
    except (TypeError, ValueError):
        delay = 60
    until = now + delay if kind == 'rate_limit' else None
    state['agent_availability'][scope] = dict(failure, until=until, attempts=attempts, recorded_at=now)
    if kind == 'auth_expired':
        _remember(provider, now)
    if state.get('dynamic_models') and kind in ('auth_expired', 'model_unavailable'):
        from . import model_discovery
        model_discovery.invalidate(state.get('workspace') or state.get('folder'))
    return scope


def available(state, cli, model, now=None):
    now = time.time() if now is None else now
    adapter = cli_adapters.get(cli)
    for scope in (adapter.provider, cli + ':' + str(model)):
        row = state.get('agent_availability', {}).get(scope)
        if row and (row.get('until') is None or now < row['until']):
            return False
    return True


def replacement(state, cli, model, kind):
    """Select the same role tier, never silently promote a model after a quota error."""
    from . import roleflow
    return roleflow.ensure(state)[roleflow.role_for(kind)]


def schedule_retry(state, scope):
    from . import config
    row = state['agent_availability'][scope]
    if row['kind'] != 'rate_limit' or row['attempts'] >= config.CFG.get('task_recovery_limit', 3):
        return False
    state['availability_retry_at'] = row['until']
    return True

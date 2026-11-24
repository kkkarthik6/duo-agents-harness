"""Per-attempt receipts and bounded recovery for idle assignment workers."""
import secrets
from pathlib import Path

VERSION = 1
RUNTIME_ID = secrets.token_hex(12)


def prepare(p, ack, now):
    """Keep task identity stable; give each issue/repair a distinct receipt path."""
    token = secrets.token_hex(6)
    path = Path(p.setdefault("ack_base", ack))
    p.update(delivery_version=VERSION, delivery_id=token,
             ack=str(path.with_name(path.stem + '-' + token + path.suffix)),
             delivery_created_at=now)
    return p['ack']


def migrate(p, now):
    """Invalidate receipts saved by older engines without deleting their files."""
    if p.get('delivery_version') == VERSION:
        return False
    old = p['ack']
    prepare(p, old, now)
    for key in ('prompt', 'wire_prompt'):
        if p.get(key):
            p[key] = p[key].replace(old, p['ack'])
    for key in ('acknowledged_at', 'idle_since', 'delivery_inflight_at'):
        p.pop(key, None)
    p['resends'] = 0
    p['delivery_error'] = 'Recovered an assignment saved without an attempt-specific receipt'
    return True


def acknowledged(root, p):
    try:
        path = Path(root) / p['ack']
        return (path.is_file() and path.stat().st_mtime >= p.get('delivery_created_at', 0)
                and path.read_text().strip() == p['task_id'])
    except (OSError, UnicodeError):
        return False


def recovery(p, now, idle, ack, interval=45, limit=3):
    """Only retry idle workers. Busy workers and approval prompts keep their turn budget."""
    if p.get('delivery_owner') not in (None, RUNTIME_ID):
        for key in ('delivery_inflight_at', 'delivery_lease', 'delivery_owner'):
            p.pop(key, None)
    if p.get('delivery_inflight_at') is not None:
        return 'stuck_sender' if now - p['delivery_inflight_at'] > 90 else None
    if not idle:
        p.pop('idle_since', None)
        return None
    if ack:
        p.setdefault('idle_since', now)
        if not p.get('delivery_error') and now - p['idle_since'] < interval:
            return None
    issued = p.get('issued_at', p.get('issued', now))
    if now - p.get('last_delivery_attempt_at', p.get('sent_at', issued)) < interval:
        return None
    if p.get('resends', 0) >= limit:
        return 'exhausted'
    return 'retry'


def retry(p, now):
    p.update(resends=p.get('resends', 0) + 1, last_delivery_attempt_at=now)
    p.pop('idle_since', None)

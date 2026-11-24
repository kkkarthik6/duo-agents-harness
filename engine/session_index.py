"""Find saved runs across remembered project roots without moving their files."""
import fcntl
import json
import os
from pathlib import Path

from . import config


def _read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def remember(*roots, home=None):
    """Keep old roots when the project setting changes. Serialize atomic writes."""
    path = Path(home or config.DUO_HOME) / "session-roots.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        known = [x for x in _read(path) if isinstance(x, str)]
        updated = list(dict.fromkeys(known + [str(Path(x).expanduser().resolve()) for x in roots]))
        if updated != known or not path.exists():
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, indent=2) + '\n')
            os.replace(tmp, path)


def discover(home):
    """Recover legacy indexes in the default root and its direct children only."""
    legacy = Path(home).parent / 'Duo Workouts'
    roots = [config.root()]
    if legacy.is_dir():
        roots.append(legacy)
        roots.extend(p.parent for p in legacy.glob('*/.duo-index.json'))
    remember(*roots, home=home)


def entries(all_projects=True, home=None):
    roots = [config.root()]
    if all_projects:
        roots.extend(Path(x) for x in _read(Path(home or config.DUO_HOME) / 'session-roots.json')
                     if isinstance(x, str))
    seen = set()
    for root in dict.fromkeys(p.expanduser().resolve() for p in roots):
        index = root / '.duo-index.json'
        for item in _read(index):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not isinstance(item.get('duo_dir'), str):
                continue
            key = (item['id'], str(Path(item['duo_dir']).expanduser().resolve()))
            if key not in seen:
                seen.add(key)
                yield index, item


def locate(sid, all_projects=True):
    found = [(index, item) for index, item in entries(all_projects=all_projects) if item['id'] == sid]
    if len(found) > 1:
        raise ValueError('More than one saved run has this ID. No run was selected.')
    return found[0] if found else (None, None)


def remove(sid):
    """Remove duplicate index references as well as the owning index entry."""
    # Include all indexes because entries() deduplicates references.
    roots = [config.root()] + [Path(x) for x in _read(config.DUO_HOME / 'session-roots.json') if isinstance(x, str)]
    paths = {root / '.duo-index.json' for root in roots}
    for path in paths:
        items = _read(path)
        updated = [i for i in items if not isinstance(i, dict) or i.get('id') != sid]
        if updated != items:
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(updated, indent=1))
            os.replace(tmp, path)

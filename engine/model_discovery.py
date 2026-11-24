"""Read-only CLI model discovery. Reported choices are not quota guarantees.

No prompts or inference turns are submitted. Snapshots stay in memory and are
invalidated by executable/auth-file metadata changes, TTL, or explicit refresh.
"""
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from . import cli_adapters, config

LOCK = threading.RLock()
CACHE = {}


def clear():
    with LOCK:
        CACHE.clear()


def invalidate(cwd=None):
    with LOCK:
        CACHE.pop(str(Path(cwd or config.APP_DIR).resolve()), None)


def normalize(rows):
    if not isinstance(rows, list):
        raise ValueError('Model discovery must return a list')
    out = {}
    for raw in rows:
        if isinstance(raw, str):
            raw = {'id': raw}
        if not isinstance(raw, dict) or raw.get('hidden') or raw.get('visibility') == 'hide' or raw.get('available') is False:
            continue
        value = raw.get('value')
        identifier = raw.get('model') or raw.get('slug') or raw.get('id') or raw.get('resolvedModel') or value
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError('Model discovery returned a model without an identifier')
        identifier = identifier.strip()
        if len(identifier) > 512 or identifier.startswith('-') or any(ord(c) < 32 for c in identifier):
            raise ValueError('Model discovery returned an unsafe identifier')
        aliases = [v for v in (value, raw.get('resolvedModel'), raw.get('resolved_model')) if isinstance(v, str)]
        aliases += [v for v in raw.get('aliases', []) if isinstance(v, str)]
        row = out.setdefault(identifier, {'id': identifier, 'display_name': str(raw.get('display_name') or raw.get('displayName') or identifier),
                                          'aliases': [], 'reasoning_levels': raw.get('reasoning_levels', raw.get('supportedReasoningEfforts', raw.get('supportedEffortLevels', [])))})
        row['aliases'] = list(dict.fromkeys(row['aliases'] + aliases))
        if raw.get('resolvedModel'):
            row['resolved_model'] = raw['resolvedModel']
    return list(out.values())


def parse_models(output, format):
    if format == 'json':
        value = json.loads(output)
        rows = value if isinstance(value, list) else value.get('models', value.get('data')) if isinstance(value, dict) else None
        return normalize(rows)
    if format != 'text':
        raise ValueError('Unknown model-list format')
    rows = []
    output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
    for line in output.splitlines():
        match = re.match(r'^\s*([A-Za-z0-9][A-Za-z0-9._/\[\]:-]*)\s{2,}(\S.*)$', line)
        if match and match[1].lower() not in ('model', 'models', 'id', 'slug', 'name', 'available'):
            rows.append({'id': match[1], 'display_name': match[2].strip()})
    if not rows and output.strip():
        raise ValueError('Unrecognized model-list text; configure a JSON listing command')
    return normalize(rows)


class MetadataProcess:
    """Bounded stdio control channel, with no task or inference requests."""
    def __init__(self, argv, cwd, timeout):
        self.argv, self.cwd, self.timeout = argv, cwd, timeout
        self.messages = queue.Queue()

    def __enter__(self):
        self.process = subprocess.Popen(self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True)
        def read():
            try:
                for line in self.process.stdout:
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    self.messages.put(value)
            finally:
                self.messages.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.deadline = time.monotonic() + self.timeout
        return self

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def response(self, match):
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('CLI model discovery timed out')
            try:
                value = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError('CLI model discovery timed out') from None
            if value is None:
                raise RuntimeError('CLI closed before returning its model catalog')
            if isinstance(value, dict) and match(value):
                return value

    def __exit__(self, *args):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except PermissionError:
                self.process.terminate()
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except PermissionError:
                self.process.kill()
            except ProcessLookupError:
                pass
            self.process.wait(timeout=2)
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        self.reader.join(timeout=2)
        self.process.stdout.close()


def probe_cli(adapter, path, cwd):
    timeout = config.CFG.get('model_discovery_timeout_seconds', 15)
    if adapter.model_list_args:
        result = subprocess.run([path] + list(adapter.model_list_args), cwd=cwd, capture_output=True, text=True,
                                timeout=timeout, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError('CLI model listing failed')
        return {'models': parse_models(result.stdout, adapter.model_list_format), 'source': 'cli_models_command'}
    if adapter.protocol == 'claude':
        argv = [path, '--print', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
                '--no-session-persistence', '--safe-mode', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
        with MetadataProcess(argv, cwd, timeout) as channel:
            channel.send({'type': 'control_request', 'request_id': 'duo-model-discovery', 'request': {'subtype': 'initialize', 'hooks': None}})
            message = channel.response(lambda r: r.get('type') == 'control_response' and r.get('response', {}).get('request_id') == 'duo-model-discovery')
            response = message.get('response', {})
            if response.get('subtype') != 'success':
                raise RuntimeError('Claude model discovery failed')
            return {'models': normalize(response.get('response', {}).get('models')), 'source': 'claude_control_initialize'}
    if adapter.protocol == 'codex':
        with MetadataProcess([path, 'app-server', '--listen', 'stdio://'], cwd, timeout) as channel:
            channel.send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'duo-catalog', 'version': '1.18.0'}}})
            initialized = channel.response(lambda r: r.get('id') == 1)
            if initialized.get('error'):
                raise RuntimeError('Codex model discovery initialization failed')
            channel.send({'method': 'initialized'})
            rows, cursor = [], None
            for page in range(20):
                request_id = page + 2
                params = {'limit': 100, 'includeHidden': False}
                if cursor:
                    params['cursor'] = cursor
                channel.send({'id': request_id, 'method': 'model/list', 'params': params})
                message = channel.response(lambda r: r.get('id') == request_id)
                if message.get('error') or not isinstance(message.get('result', {}).get('data'), list):
                    raise RuntimeError('Codex model listing failed')
                result = message['result']
                rows.extend(result['data'])
                cursor = result.get('nextCursor')
                if not cursor:
                    return {'models': normalize(rows), 'source': 'codex_app_server_catalog', 'note': 'CLI catalog can include bundled or cached models; access is checked on invocation.'}
            raise RuntimeError('CLI model listing exceeded its page limit')
    return {'models': [], 'source': 'unsupported', 'status': 'unsupported'}


def _stamp(path):
    try:
        p = Path(path)
        stat = p.stat()
        return [str(p.resolve()), stat.st_mtime_ns, stat.st_size]
    except OSError:
        return None


def is_fresh(snapshot, now=None):
    return (time.time() if now is None else now) < snapshot.get('expires_at', 0)


def refresh(force=False, now=None, cwd=None, which=None, probe=None):
    which, probe = which or shutil.which, probe or probe_cli
    cwd = str(Path(cwd or config.APP_DIR).resolve())
    adapters = cli_adapters.registry()
    paths = {key: which(a.executable) for key, a in adapters.items()}
    auth_paths = [Path.home() / p for p in ('.codex/auth.json', '.codex/config.toml', '.claude/.credentials.json',
                  '.claude.json', '.gemini/antigravity-cli/settings.json')]
    signature = hashlib.sha256(json.dumps([paths, [_stamp(p) for p in paths.values() if p], [_stamp(p) for p in auth_paths],
                              [asdict(a) for a in adapters.values()]], sort_keys=True).encode()).hexdigest()
    timestamp = time.time() if now is None else now
    with LOCK:
        old = CACHE.get(cwd)
        if not force and old and old['signature'] == signature and is_fresh(old, timestamp):
            return json.loads(json.dumps(old))
        def discover(item):
            key, adapter = item
            path = paths[key]
            row = {'id': key, 'installed': bool(path), 'checked_at': timestamp, 'models': [], 'status': 'not_installed'}
            if not path:
                return row
            try:
                result = probe(adapter, path, cwd)
                row.update(models=normalize(result['models']), status=result.get('status', 'ready'), source=result.get('source', 'cli'))
                if result.get('note'):
                    row['note'] = result['note']
            except Exception as error:
                row.update(status='error', error='Model discovery did not complete (' + type(error).__name__ + ').')
            return row
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(discover, adapters.items()))
        completed_at = time.time() if now is None else timestamp
        snapshot = {'checked_at': completed_at, 'expires_at': completed_at + config.CFG.get('model_discovery_ttl_seconds', 60),
                    'signature': signature, 'clis': rows}
        CACHE[cwd] = snapshot
        return json.loads(json.dumps(snapshot))


def catalog(snapshot, policies):
    rules = {(r['cli'], r['id']): r for r in policies}
    for rule in policies:
        for alias in rule.get('discovery_aliases', []):
            rules.setdefault((rule['cli'], alias), rule)
    result = []
    for cli in snapshot['clis']:
        if not cli['installed'] or cli['status'] != 'ready':
            continue
        for reported in cli['models']:
            rule = rules.get((cli['id'], reported['id']))
            if rule is None:
                rule = next((rules[(cli['id'], alias)] for alias in reported.get('aliases', []) if (cli['id'], alias) in rules), None)
            row = dict(rule or {}, **reported, cli=cli['id'], tier=(rule or {}).get('tier'),
                       listed=True, checked_at=cli['checked_at'], source=cli.get('source'), account_access='not_tested',
                       availability_source='current CLI-reported catalog; quota and inference access not tested')
            row['available'] = bool(rule and rule.get('available') is True)
            row['status'] = 'listed' if row['available'] else 'disabled' if rule else 'needs_tier'
            result.append(row)
    return result

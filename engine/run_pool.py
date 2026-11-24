"""Admission and routing for independent runs. Native workers do the work concurrently."""
import json
import subprocess
import time
from pathlib import Path

from . import config, jobs, session, session_index, workers


def workspace_key(folder):
    root = Path(folder).expanduser().resolve()
    for parent in (root, *root.parents):
        if (parent / '.git').exists():
            return parent
    return root


def overlap(a, b):
    a, b = workspace_key(a), workspace_key(b)
    return a == b or a in b.parents or b in a.parents


def background_jobs(state, table):
    """Keep detached work reserved, including work whose terminal was closed."""
    folder = Path(state.get('duo_dir', '/nonexistent')) / 'jobs'
    for path in folder.glob('*.json'):
        try:
            records = json.loads(path.read_text()).get('jobs', [])
        except (OSError, ValueError, AttributeError):
            return True
        if not isinstance(records, list):
            return True
        for record in records:
            if not isinstance(record, dict):
                return True
            if record.get('container'):
                try:
                    row = jobs.inspect_container(record['container'])
                    if row['alive'] and row['identity'] == record.get('identity'):
                        return True
                except (OSError, ValueError, KeyError, TimeoutError, subprocess.TimeoutExpired):
                    return True
            elif record.get('pid'):
                if table is None:
                    return True  # Inspection failure cannot permit a duplicate workload.
                row = table.get(record['pid'], {})
                if row.get('identity') == record.get('identity') and not any(c in row.get('state', '') for c in 'ZX'):
                    return True
    return False


class RunPool:
    def managed(self):
        if not hasattr(self, 'sessions'):
            self.sessions = {}
        if self.session:
            self.sessions[self.session.st['id']] = self.session
        return self.sessions

    def has_active_runs(self):
        return any(s.st['status'] in session.ACTIVE for s in self.managed().values())

    def get_session(self, sid=None, saved=True):
        if not sid:
            return self.session
        current = self.managed().get(sid)
        return current if current else session.find(sid) if saved else None

    def request_run(self, sid=None, saved=False):
        if not sid and sum(s.st['status'] in session.ACTIVE for s in self.managed().values()) > 1:
            raise ValueError('Name the run for this request. More than one run is active.')
        target = self.get_session(sid, saved=saved)
        if not target:
            raise ValueError('This run is not attached. Use Continue to start its workers.')
        return target

    def select_run(self, sid):
        target = self.get_session(sid, saved=False)
        if not target:
            raise ValueError('This is a saved run. Opening its history does not start workers.')
        self.session = target
        session.bump()
        return target

    def reservations(self):
        states = {sid: s.st for sid, s in self.managed().items()}
        for _, entry in session_index.entries():
            if entry['id'] in states:
                continue
            try:
                state = json.loads((Path(entry['duo_dir']) / 'state.json').read_text())
                state.setdefault('duo_dir', entry['duo_dir'])
                states[entry['id']] = state
            except (OSError, ValueError):
                continue
        need_table = any((Path(s.get('duo_dir', '/nonexistent')) / 'jobs').is_dir() for s in states.values())
        table = {}
        if need_table:
            try:
                table = jobs.process_table()
            except (OSError, TimeoutError, subprocess.TimeoutExpired):
                table = None
        held = []
        for sid, state in states.items():
            active = sid in self.managed() and state['status'] in session.ACTIVE
            active |= sid in self.managed() and bool((state.get('gate') or {}).get('running'))
            active |= bool(state.get('worker_session') and state.get('status') in session.ACTIVE and workers.alive(state))
            if state.get('workspace') and (active or background_jobs(state, table)):
                held.append((sid, state['workspace']))
        return held

    def ensure_room(self, workspace, sid=None):
        held = [(key, folder) for key, folder in self.reservations() if key != sid]
        if any(overlap(workspace, folder) for _, folder in held):
            raise ValueError('Another run or background job uses this project. Finish or stop that work first.')
        if len(held) >= config.CFG.get('max_active_runs', 4):
            raise ValueError('The active-run limit is reached. Finish or stop a run before starting another.')

    def tick_runs(self):
        for current in list(self.managed().values()):
            if current.st['status'] not in session.ACTIVE:
                continue
            if getattr(self, 'discovery_enabled', False) and current.st.get('workflow') == 'roles':
                current.st['dynamic_models'] = True
            try:
                prior_error = current.st.get('error')
                previous_pending = len(current.st.get('pending', []))
                current.tick()
                current.st.pop('tick_failure', None)
                if prior_error and current.st.get('error') == prior_error and len(current.st.get('pending', [])) > previous_pending:
                    current.st.setdefault('recovered_errors', []).append(dict(prior_error, recovered_at=time.time(),
                        recovery='Task assignments were issued successfully.'))
                    current.st.pop('error', None)
                    current.save()
                if current.st['status'] == 'done':
                    workers.close(current.st)
            except Exception as error:
                try:
                    current.record_error(error)
                except Exception:
                    # An error while recording one failure cannot starve other runs.
                    import traceback
                    traceback.print_exc()

    def run_list(self):
        return [{key: s.st.get(key) for key in ('id', 'title', 'workspace', 'project', 'status', 'paused')}
                for s in self.managed().values()]

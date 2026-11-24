"""Local HTTP API + the single-page UI, on 127.0.0.1."""
import json
import os
import mimetypes
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import clone_agent, coremgr, extensions, features, feedback as feedback_mod, sandbox, agents as agents_mod, config, model_policy, notify, personal, security, selfdev, session, settings, trust, userspace, broker, summary_agent
from .tmux import capture, send_key, send_text, tmux
from .run_pool import RunPool
from . import workers, session_index
from . import project_handoff

WEB = config.APP_DIR / "web" / "index.html"
code_id = config.code_id
CODE = code_id()


def app_version():
    p = config.APP_DIR / "VERSION"
    return p.read_text().strip() if p.exists() else "unknown"


class Engine(RunPool):
    def __init__(self, agents=None, tick=True, home=None, user_dir=None):
        self.discovery_enabled = agents is None
        self.agents_checked_at = time.time()
        self.agents = agents if agents is not None else agents_mod.detect()
        self.other_agents = agents_mod.detect_other() if agents is None else []
        # ~/Duo, where the user layer and the sealed core live. Tests pass their own folder or none.
        self.home = Path(home) if home else (config.DUO_HOME if agents is None else None)
        self.user_dir = Path(user_dir) if user_dir else None      # load features from here instead (screenshots)
        self.session = None
        self.sessions = {}
        self.load_features()
        self.applying = False
        self.new_changes = []
        personal.apply_settings()
        if self.discovery_enabled:
            self.model_inventory(force=True)
        if self.home:
            from . import session_index
            session_index.discover(self.home)
        self._restore()
        self.swept_at = 0
        try:
            self.sweep_workers()
        except Exception as ex:
            print("sweep error: %r" % ex, file=sys.stderr)
        if tick:
            threading.Thread(target=self.loop, daemon=True).start()

    def refresh_agents(self, force=False):
        if getattr(self, 'discovery_enabled', False) and (force or time.time() - self.agents_checked_at >= config.CFG.get('model_discovery_ttl_seconds', 60)):
            self.agents = agents_mod.detect()
            self.other_agents = agents_mod.detect_other()
            self.agents_checked_at = time.time()

    def model_inventory(self, force=False):
        from . import model_discovery
        if not self.discovery_enabled:
            return {'checked_at': time.time(), 'expires_at': time.time(), 'clis': []}
        self.refresh_agents(force=force)
        return model_discovery.refresh(force=force)

    def apply_feedback(self, runner=None):
        """Send pending feedback: each item becomes a version clone that an agent edits in the background.
        Given a runner, interpret the feedback in place instead: settings layers are applied, code changes queued."""
        if runner is not None:
            return self._interpret_feedback(runner)
        with session.LOCK:
            pending = [f for f in personal.list_feedback() if f["status"] == "pending" and not f.get("reaction")]
            if not pending:
                return None
            rec = feedback_mod.submit("\n".join(f["text"] for f in pending), self.duo_home(), kind="minor")
            personal._mark({f["id"] for f in pending}, "cloned", "Saved as %s. Duo is editing a copy." % rec["number"])
            session.bump()
        return rec

    # ------------------------------------------------------------------ user layer and core
    def duo_home(self):
        return self.home or config.DUO_HOME

    def load_features(self, emit_started=True):
        """Load the user layer's features into this engine. Bad features are skipped and reported."""
        off = set(coremgr.disabled(self.home) if self.home else ())
        off.update(name for name, control in config.CFG.get("feature_controls", {}).items() if control.get("enabled") is False)
        if self.user_dir is not None:
            reg = features.load(self.user_dir, off)
        elif self.home is None:
            reg = features.Registry()
        else:
            userspace.migrate_legacy(self.home)
            reg = features.load(userspace.active_dir(self.home), off)
        extensions.apply_layout(reg)
        reg.home = self.duo_home()
        reg.settings_fn = lambda f: (config.CFG.get("features") or {}).get(f, {})
        features.activate(reg)
        if emit_started:
            reg.emit("app.started", {})
        return reg

    def versions_view(self):
        h = self.duo_home()
        s = userspace.summary(h)
        reqs = []
        d = h / "core-requests"
        if d.is_dir():
            for f in sorted(d.glob("*.json")):
                try:
                    reqs.append(json.loads(f.read_text()))
                except ValueError:
                    pass
        core = coremgr.info(h)
        return {"number": s["number"], "next": s["next"], "rolled": s.get("rolled", []), "trying": s.get("trying"),
                "versions": list(reversed(s["versions"])), "core": core,
                "sealed": not coremgr.verify(h) if core.get("dir") else None,
                "disabled": coremgr.disabled_info(h), "core_requests": reqs,
                "can_go_back": bool(s.get("rolled"))}

    def _interpret_feedback(self, runner):
        if self.applying:
            return None
        self.applying = True

        def work():
            try:
                out = personal.process(runner)
                with session.LOCK:
                    self.new_changes = self.new_changes + out["applied"]
                    session.bump()
            finally:
                self.applying = False

        threading.Thread(target=work, daemon=True).start()
        return None

    def add_feedback(self, b):
        """Save feedback in the feedback log. It never goes into a running task: feedback changes Duo itself,
        through a version copy that its own agent edits. To steer a run, the user writes in the run's message box."""
        ctx = dict(b.get("context") or {})
        with session.LOCK:
            f = personal.add_feedback(str(b.get("text", "")), ctx, b.get("reaction") or None)
            session.bump()
        return f

    def _restore(self):
        """Reconnect live native workers. Starting the broker must not kill their work."""
        self.session = None
        self.sessions = {}
        local = {x['id'] for x in session.list_sessions(all_projects=False)}
        for item in session.list_sessions():
            if item["status"] not in session.ACTIVE:
                continue
            _, entry = session_index.locate(item['id'])
            raw = json.loads((Path(entry['duo_dir']) / 'state.json').read_text()) if entry else {}
            if item['id'] not in local and not raw.get('worker_session'):
                continue  # Historical legacy pane numbers may belong to another project now.
            if not workers.alive(raw):
                continue  # Preserve orphan job records; admission still reserves live jobs.
            candidate = session.find(item['id'])
            if candidate:
                self.sessions[item['id']] = candidate
                if self.session is None:
                    self.session = candidate

    def sweep_workers(self):
        """Close worker terminals that belong to no active run."""
        keep = {x['id'] for x in session.list_sessions() if x['status'] in session.ACTIVE} | set(self.sessions)
        closed = workers.sweep(keep)
        if closed:
            print("Closed %d unused worker session(s)." % len(closed), file=sys.stderr)
        return closed

    def quit(self):
        with session.LOCK:
            for s in list(self.managed().values()):
                workers.close(s.st)
                if s.st['status'] in session.ACTIVE:
                    s.st['status'], s.st['detail'] = 'stopped', 'Duo was quit.'
                    s.save()
            try:
                sandbox.stop_all()
            except Exception:
                pass
        srv = getattr(self, "server", None)
        if srv:
            threading.Thread(target=srv.shutdown, daemon=True).start()

    def _open(self, sid, force):
        s = self.get_session(sid, saved=False) or session.find_or_explain(sid)
        if getattr(self, 'discovery_enabled', False) and s.st.get('workflow') == 'roles':
            s.st['dynamic_models'] = True
        if sid in self.managed() and s.st['status'] in session.ACTIVE:
            return s
        self.ensure_room(s.ws, sid)
        return s

    def continue_run(self, sid, force=False, keep_workers=False):
        """Continue any run: start its agents again and finish its open turns."""
        with session.LOCK:
            s = self._open(sid, force)
            if (sid in self.managed() and s.st['status'] in session.ACTIVE
                    and not s.st.get('paused') and not s.st.get('stop')
                    and s.st['status'] != 'paused'):
                self.session = s
                return  # Selecting an attached live run must not relaunch its workers.
            if keep_workers:
                if s.st.get("pending") and not s.panes_alive():
                    raise ValueError("Saved native workers are unavailable; use ordinary resume.")
                self.session = s
                self.sessions[sid] = s
                return
            s.st["paused"] = False                # pressing Continue means carry on
            s.resume()
            self.session = s
            self.sessions[sid] = s

    resume = continue_run

    def follow_up(self, sid, text, force=False):
        """Add to a run: a new cycle built on its earlier decisions."""
        with session.LOCK:
            s = self._open(sid, force)
            if s.st["status"] in session.ACTIVE:
                raise ValueError("Stop the current run first.")
            s.follow_up(text)
            self.session = s
            self.sessions[sid] = s

    def loop(self):
        while True:
            try:
                with session.LOCK:
                    self.tick_runs()
                    selfdev.tick(self)
                    if time.time() - self.swept_at > 300:
                        self.swept_at = time.time()
                        self.sweep_workers()
            except Exception as ex:
                print("tick error: %r" % ex, file=sys.stderr)
                with session.LOCK:
                    print('broker error: %r' % ex, file=sys.stderr)
            time.sleep(1)

    def start(self, b):
        self.refresh_agents(force=True)
        with session.LOCK:
            if b.get('project'):
                workspace = config.root() / b['project']
            elif b.get('workspace'):
                workspace = Path(b['workspace']).expanduser().resolve()
            else:
                workspace = trust.unique_project_dir(b.get('project_name') or session.slugify(b.get('task', ''), 40))
            self.ensure_room(workspace)
            chosen = [a for a in self.agents if not b.get("agents") or a.id in b["agents"]]
            mode = b.get("sandbox") or config.CFG.get("sandbox", "auto")
            box = False
            if mode == "on":
                ok, msg = sandbox.available()
                if ok:
                    box = True
                elif mode == "on":
                    raise ValueError(msg)
            self.session = session.Session.create(
                str(b.get("task", "")).strip() or _fail("Write a task first."), chosen,
                project=b.get("project") or None, project_name=b.get("project_name") or None,
                workspace=b.get("workspace") or None, mode=b.get("mode") or None,
                task_type=b.get("task_type") or None, max_rounds=b.get("max_rounds") or None,
                approvals=b.get("approvals") or "auto", sandbox_on=box)
            self.sessions[self.session.st['id']] = self.session
            return self.session

    def state(self):
        with session.LOCK:
            s = self.session
            cur = s.st["id"] if s else None
            unfinished = next(({"id": x["id"], "title": x["title"], "project": x.get("project")}
                               for x in session.list_sessions() if x["status"] == "stopped" and x["id"] != cur), None)
            return {"version": session.VERSION[0], "duo_version": userspace.load(self.duo_home())["number"],
                    "core_version": coremgr.info(self.duo_home()).get("version"),
                    "session": s.view() if s else None, "last_unfinished": unfinished,
                    'runs': self.run_list(), 'max_active_runs': config.CFG.get('max_active_runs', 4),
                    "files": s.changed_files() if s else [],
                    "agents": [a.to_dict() for a in self.agents],
                    "other_agents": self.other_agents,
                    "personal": {"ui": personal.ui_prefs(), "layers": personal.layers(), "feedback": personal.list_feedback()[:50],
                                 "proposals": personal.proposals(), "applying": self.applying, "new_changes": self.new_changes,
                                 "security_count": len(security.read_log(200))},
                    "tmux": "tmux attach -t %s" % (workers.name(s.st) if s else config.CFG['tmux_session']),
                    "defaults": {"mode": config.CFG["decision_mode"], "max_rounds": config.CFG["max_rounds"],
                                 "root": config.CFG["root"]}}


def _fail(msg):
    raise ValueError(msg)


def make_server(port, engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _trusted(self):
            """Only this app's own pages may call the API (blocks other websites and DNS rebinding)."""
            port = self.server.server_address[1]
            hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
            origins = {"http://" + h for h in hosts} | {"tauri://localhost", "http://tauri.localhost"}
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in origins

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._trusted():
                return self._send(403, {"error": "forbidden"})
            try:
                if u.path in ("/", "/index.html"):
                    self._send(200, WEB.read_bytes(), "text/html; charset=utf-8")
                elif u.path == "/features.js":
                    self._send(200, (config.APP_DIR / "web" / "features.js").read_bytes(), "text/javascript; charset=utf-8")
                elif u.path == "/research-evidence.js":
                    self._send(200, (config.APP_DIR / "web" / "research-evidence.js").read_bytes(), "text/javascript; charset=utf-8")
                elif u.path in ("/task-map.js", "/task-map.css"):
                    name = u.path.lstrip("/")
                    mime = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
                    self._send(200, (config.APP_DIR / "web" / name).read_bytes(), mime)
                elif u.path == "/notifications.js":
                    self._send(200, (config.APP_DIR / "web" / "notifications.js").read_bytes(), "text/javascript; charset=utf-8")
                elif u.path == "/api/ready":
                    import shutil
                    from . import agents as agent_specs, readiness, sandbox
                    found = {aid: {"found": shutil.which(aid) is not None, "signed_in": None} for aid in agent_specs.AGENT_SPECS}
                    self._send(200, {"checks": readiness.checks(found, sandbox.available(), shutil.which("tmux") is not None)})
                elif u.path == "/api/health":
                    h = engine.duo_home()
                    cur = coremgr.current_dir(h)
                    us = userspace.summary(h)
                    waiting = sum(1 for x in us["versions"] if x.get("status") == "waiting_for_core")
                    self._send(200, {"ok": True, "version": "2", "number": us["number"],
                                     "trying": (us.get("trying") or {}).get("number"), "core_requests": waiting,
                                     "core": coremgr.info(h).get("version"), "app": app_version(), "code": CODE,
                                     "code_now": config.code_id(), "running": str(config.APP_DIR),
                                     "current": str(cur) if cur else None, "feedback": feedback_mod.notice(h)})
                elif u.path == "/api/versions":
                    self._send(200, engine.versions_view())
                elif u.path == "/api/versions/shot":
                    h = engine.duo_home()
                    v = userspace.entry(h, q.get("id", [""])[0])
                    pics = v.get("shots") or []
                    n = int(q.get("n", ["0"])[0] or 0)
                    p = Path(pics[n]).resolve() if 0 <= n < len(pics) else None
                    root = userspace.versions_dir(h).resolve()
                    if not p or root not in p.parents or not p.is_file():
                        return self._send(404, {"error": "not found"})
                    self._send(200, p.read_bytes(), "image/png")
                elif u.path == "/api/features":
                    v = features.active().view()
                    v["disabled"] = coremgr.disabled_info(engine.duo_home())
                    v["start_page"] = config.CFG.get("start_page", "home")
                    self._send(200, v)
                elif u.path == "/api/extensions":
                    v = extensions.view(features.active(), coremgr.disabled_info(engine.duo_home()))
                    v["core"] = coremgr.info(engine.duo_home())
                    self._send(200, v)
                elif u.path == "/api/models/roles":
                    state = {"agents": [a.id for a in engine.agents], "role_model_routing": True,
                             "workflow": config.CFG.get('workflow', 'roles'), 'dynamic_models': engine.discovery_enabled}
                    self._send(200, {"roles": model_policy.human_view(state), "mode": "agentic"})
                elif u.path == "/api/models/catalog":
                    from . import model_catalog, cli_adapters, model_discovery
                    snapshot = engine.model_inventory(force=q.get('refresh', [''])[0] in ('1', 'true'))
                    policies = model_catalog.entries()
                    self._send(200, {"models": model_discovery.catalog(snapshot, policies), 'configured_models': policies,
                                     'discovery': snapshot,
                                     "adapters": [{"id": a.id, "executable": a.executable, "protocol": a.protocol,
                                                   "capabilities": a.capabilities, "provider": a.provider}
                                                  for a in cli_adapters.registry().values()]})
                elif u.path.startswith("/api/f/"):
                    feat, _, rest = u.path[len("/api/f/"):].partition("/")
                    code, payload = features.active().call_route(feat, "GET", "/" + rest, {}, {k: v[0] for k, v in q.items()})
                    self._send(code, payload)
                elif u.path.startswith("/f/"):
                    feat, _, rel = u.path[len("/f/"):].partition("/")
                    reg = features.active()
                    p = (reg.user_dir / "features" / feat / rel) if (reg.serves(feat, rel) and reg.user_dir) else None
                    if not p or not p.is_file():
                        return self._send(404, {"error": "not found"})
                    self._send(200, p.read_bytes(), "text/javascript; charset=utf-8")
                elif u.path == "/api/state":
                    self._send(200, engine.state())
                elif u.path == "/api/agents":
                    engine.refresh_agents()
                    self._send(200, [a.to_dict() for a in engine.agents])
                elif u.path == "/api/projects":
                    self._send(200, {"root": config.CFG["root"], "projects": trust.list_projects()})
                elif u.path == '/api/project-context':
                    folder=Path(q.get('workspace',[''])[0]).expanduser()
                    if not folder.is_absolute() or not folder.is_dir():
                        raise ValueError('Choose an existing project folder.')
                    self._send(200,project_handoff.public(project_handoff.select(folder)))
                elif u.path == "/api/sessions":
                    self._send(200, session.list_sessions())
                elif u.path in ("/api/task-errors", "/api/task-file"):
                    sid = q.get("id", [""])[0]
                    target = engine.get_session(sid)
                    if not target:
                        return self._send(404, {"error": "Task session not found."})
                    if u.path == "/api/task-errors":
                        self._send(200, {"errors": broker.errors(target.dir, q.get("task", [None])[0])})
                    else:
                        try:
                            self._send(200, broker.read_output(target.ws, q.get("path", [""])[0]))
                        except ValueError as error:
                            self._send(404, {"error": str(error)})
                elif u.path == "/api/session":
                    sid = q.get('id', [''])[0]
                    s = engine.get_session(sid)
                    self._send(200 if s else 404, dict(s.view(), managed=sid in engine.managed()) if s else {"error": "not found"})
                elif u.path == "/api/pane":
                    with session.LOCK:
                        s = engine.request_run(q.get('run_id', [None])[0])
                        pane = s.st["panes"].get(q.get("agent", [""])[0]) if s and s.panes_alive() else None
                    self._send(200, {"text": capture(pane, 300) if pane else ""})
                elif u.path == "/api/shot":
                    s = engine.get_session(q.get("id", [""])[0])
                    name = os.path.basename(q.get("f", [""])[0])
                    f = (s.dir / "shots" / name) if s else None
                    if not f or f.suffix.lower() not in (".png", ".txt") or not f.is_file():
                        return self._send(404, {"error": "not found"})
                    self._send(200, f.read_bytes(), "image/png" if f.suffix == ".png" else "text/plain; charset=utf-8")
                elif u.path == "/api/proposal":
                    self._send(200, {"diff": selfdev.diff_text(q.get("id", [""])[0])})
                elif u.path == "/api/security":
                    self._send(200, {"log": security.read_log()})
                elif u.path == "/api/chat":
                    with session.LOCK:
                        s = engine.request_run(q.get('run_id', [None])[0], saved=True)
                        self._send(200, s.chat() if s else [])
                elif u.path == "/api/config":
                    self._send(200, {"config": {k: config.CFG[k] for k in settings.GLOBAL}})
                elif u.path == "/api/events":
                    self._events(q)
                else:
                    self._send(404, {"error": "not found"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (ValueError, KeyError, RuntimeError, TypeError) as error:
                self._send(400, {'error': str(error)})

        def _events(self, q):
            """Server-sent events. `since=N` sends only entries after entry N, as named "entries" events.
            Full state goes out as a plain data event on connect and every 5 seconds."""
            since = int((q.get("since") or ["-1"])[0] or -1)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last, last_sent, cursor, selected_id = -1, 0, since, None
            try:
                while True:
                    now = time.time()
                    with session.LOCK:
                        s = engine.session
                        if s and s.st['id'] != selected_id:
                            if selected_id is not None:
                                cursor = -1
                            selected_id = s.st['id']
                        fresh = s.entries_after(cursor) if s else []
                    if fresh:
                        cursor = fresh[-1]["n"]
                        self.wfile.write(("event: entries\ndata: %s\n\n" % json.dumps({"run_id": selected_id, "entries": fresh, "cursor": cursor}, default=str)).encode())
                        self.wfile.flush()
                    if session.VERSION[0] != last or now - last_sent > 5:
                        last, last_sent = session.VERSION[0], now
                        self.wfile.write(("data: %s\n\n" % json.dumps(engine.state(), default=str)).encode())
                        self.wfile.flush()
                    time.sleep(0.3)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            u = urlparse(self.path)
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if not self._trusted() or ctype != "application/json":
                return self._send(403, {"error": "forbidden"})
            try:
                b = self._body()
                if u.path == '/api/ui-diagnostic':
                    target=engine.session.dir if engine.session else engine.duo_home()/'diagnostics/ui'
                    broker.error(target,'ui',str(b.get('message','UI error')),raw=b,operation='ui_render',source='browser')
                    return self._send(200,{'ok':True})
                if u.path == "/api/config":
                    clean = settings.check_global(b)
                    self._send(200, {"config": {k: v for k, v in config.save_global(clean).items() if k in settings.GLOBAL}})
                    return
                if u.path == "/api/extensions/configure":
                    lifecycle = "enabled" in b or "layout" in b
                    with session.LOCK:
                        active = engine.has_active_runs()
                        if lifecycle and active:
                            raise ValueError("Stop active runs before changing feature availability or placement")
                        clean = extensions.configure(features.active(), b, coremgr.disabled(engine.duo_home()))
                        config.save_global(clean)
                        if lifecycle:
                            engine.load_features(emit_started=False)
                        session.bump()
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/start":
                    result = engine.start(b)
                    self._send(200, {"ok": True, 'id': result.st['id']})
                    return
                if u.path == '/api/select':
                    with session.LOCK:
                        engine.select_run(str(b.get('id') or ''))
                    self._send(200, {'ok': True})
                    return
                if u.path == "/api/quit":               # the app calls this when you quit Duo
                    self._send(200, {"ok": True})
                    engine.quit()
                    return
                if u.path == "/api/shutdown":           # used by `duo` to replace an outdated engine
                    RESTART["on"] = bool(b.get("restart"))
                    self._send(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if u.path == "/api/delete":
                    with session.LOCK:
                        if any(sid == b.get('id') for sid, _ in engine.reservations()):
                            raise ValueError('This run still has workers or background jobs. Finish that work before deleting its history.')
                        live_id = engine.session.st["id"] if engine.session else None
                        session.delete_run(b.get("id") or "", live_id)
                    self._send(200, {"ok": True})
                    return
                if u.path in ("/api/resume", "/api/continue"):
                    sid = b.get('id') or engine.request_run(b.get('run_id')).st['id']
                    engine.continue_run(sid, bool(b.get("force")), bool(b.get("keep_workers")))
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/followup":
                    engine.follow_up(b.get('id') or engine.request_run(b.get('run_id')).st['id'],
                                     b.get("text", ""), bool(b.get("force")))
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/feedback":
                    kind = str(b.get("kind") or "minor")
                    if not b.get("reaction") and kind not in userspace.KINDS:
                        raise ValueError("Choose minor or major.")
                    f = engine.add_feedback(b)
                    rec = None
                    if not b.get("reaction"):
                        rec = feedback_mod.submit(f["text"], engine.duo_home(), kind=kind)
                        personal._mark({f["id"]}, "cloned", "Saved as %s. Duo is editing a copy in the background." % rec["number"])
                        session.bump()
                    self._send(200, {"ok": True, "feedback": f, "reference": rec})
                    return
                if u.path == "/api/proposal":
                    act, pid = b.get("action"), str(b.get("id", ""))
                    fn = {"approve": selfdev.approve, "decline": selfdev.decline, "rollback": selfdev.rollback, "retry": selfdev.retry}.get(act)
                    if not fn:
                        raise ValueError("Unknown action.")
                    fn(pid)
                    session.bump()
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/versions/repair":
                    feedback_mod.repair(str(b.get("id") or ""), engine.duo_home())
                    self._send(200, {"ok": True})
                    return
                if u.path in ("/api/versions/rollup", "/api/versions/accept"):
                    num = clone_agent.accept(str(b.get("id") or ""), engine.duo_home())
                    engine.load_features()
                    session.bump()
                    self._send(200, {"ok": True, "number": num, "reload": True})
                    return
                if u.path in ("/api/versions/use", "/api/versions/stop"):
                    if u.path.endswith("use"):
                        num = userspace.try_version(engine.duo_home(), str(b.get("id") or ""))
                    else:
                        num = None
                        userspace.stop_trying(engine.duo_home())
                    engine.load_features()
                    session.bump()
                    self._send(200, {"ok": True, "trying": num, "reload": True})
                    return
                if u.path == "/api/versions/reject":
                    clone_agent.reject(str(b.get("id") or ""), engine.duo_home())
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/versions/goback":
                    num = userspace.go_back(engine.duo_home())
                    engine.load_features()
                    session.bump()
                    self._send(200, {"ok": True, "number": num, "reload": True})
                    return
                if u.path == "/api/core/goback":
                    with session.LOCK:
                        if engine.has_active_runs():
                            raise ValueError("A run is open. Stop it before you go back to the earlier core.")
                    ver = coremgr.go_back(engine.duo_home())
                    self._send(200, {"ok": True, "core": ver, "restart": True})
                    return
                if u.path.startswith("/api/f/"):
                    feat, _, rest = u.path[len("/api/f/"):].partition("/")
                    code, payload = features.active().call_route(feat, "POST", "/" + rest, b, {})
                    return self._send(code, payload)
                if u.path == "/api/undo":
                    personal.undo(str(b.get("id", "")))
                    engine.new_changes = [c for c in engine.new_changes if c.get("id") != b.get("id")]
                    session.bump()
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/changes/seen":
                    engine.new_changes = []
                    session.bump()
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/terminal":
                    s = engine.request_run(b.get('run_id'))
                    target = workers.name(s.st)
                    if not shutil.which("osascript"):
                        raise ValueError("Opening Terminal works on macOS only. Run: tmux attach -t %s" % target)
                    # Worker session names are generated locally, never supplied as shell text.
                    import shlex
                    cmd = 'tmux attach -t ' + shlex.quote(target)
                    subprocess.run(["osascript", "-e", 'tell application "Terminal" to do script "%s"' % cmd,
                                    "-e", 'tell application "Terminal" to activate'], check=False)
                    self._send(200, {"ok": True})
                    return
                if u.path == "/api/rescan":
                    engine.agents = agents_mod.detect()
                    engine.other_agents = agents_mod.detect_other()
                    engine.agents_checked_at = time.time()
                    engine.model_inventory(force=True)
                    self._send(200, [a.to_dict() for a in engine.agents])
                    return
                with session.LOCK:
                    historical = u.path in ('/api/task-summary', '/api/completion-summary', '/api/question-answer', '/api/ask')
                    sid = b.get('run_id') or (b.get('id') if historical else None)
                    s = engine.request_run(sid, saved=historical)
                    if not s:
                        raise ValueError("No session yet.")
                    if u.path == "/api/question-answer":
                        if str(b.get("id")) != s.st["id"]:
                            raise ValueError("Open this run as the active run before answering.")
                        kind = b.get("kind")
                        if kind == 'approval' and s.st['id'] not in engine.managed():
                            raise ValueError("This request belongs to an inactive run. Open its active run first.")
                        if kind == "approval":
                            if s.st.get('status') not in session.ACTIVE or not s.panes_alive():
                                raise ValueError('The native workers for this request are no longer attached.')
                            pending = next((p for p in s.st.get("pending",[]) if p.get("agent") == b.get("agent") and
                                            (p.get("waiting") or {}).get("question") == b.get("question")),None)
                            if not pending or (pending.get("waiting") or {}).get("kind") != "approval":
                                raise ValueError("This native permission request is no longer current.")
                            if type(b.get("approve")) is not bool:
                                raise ValueError("Choose approve or deny for a native permission request.")
                            s.answer_wait(str(b.get("agent")),b["approve"])
                        elif kind == "task":
                            result=broker.answer_question(s.dir,str(b.get("question_id")),b.get("text",""))
                            if not result["duplicate"]:
                                s.human_message("Answer to task question: "+result["text"]+"\n\n"+result["answer"],task_id=None if result.get('scope')=='run' else result["node"])
                                if s.st['id'] in engine.managed() and (s.st.get("stop") or {}).get("rule") == "Task information needed":
                                    s.control("resume")
                        else: raise ValueError("Unknown question type.")
                        s.save()
                        self._send(200,{"ok":True})
                        return
                    if u.path == "/api/completion-summary":
                        from . import completion_summary
                        self._send(200,completion_summary.request(s.dir,s.ws,s.st,force=True))
                        return
                    if u.path == "/api/task-summary":
                        task_id = str(b.get("task_id") or "")
                        if not broker.node(s.dir, task_id):
                            raise ValueError("Task record not found.")
                        broker.prepare_task_documents(s.dir, s.ws, task_id)
                        self._send(200, summary_agent.request(s.dir, task_id, s.st["agents"], force=True))
                        return
                    if u.path == "/api/message":
                        if s.st.get('status')=='done':
                            row=s.ask(str(b.get('text','')).strip(),provider=b.get('provider','auto'),request_id=b.get('request_id'))
                            self._send(200,dict(ok=True,conversation=row));return
                        res = s.human_message(str(b.get("text", "")).strip() or _fail("Write a message first."))
                        self._send(200, dict(res, ok=True))
                        return
                    elif u.path == "/api/ask":
                        row=s.ask(str(b.get('question','')).strip(),b.get('focus') or None,
                                  provider=b.get('provider','auto'),request_id=b.get('request_id'),retry_id=b.get('retry_id'))
                        self._send(200,dict(ok=True,conversation=row));return
                    elif u.path == "/api/rollback":
                        self._send(200, dict(s.rollback(b.get("turn")), ok=True))
                        return
                    elif u.path == "/api/checklist":
                        self._send(200, {"ok": True, "checklist": s.checklist_edit(str(b.get("action", "")), b.get("text"), b.get("id"))})
                        return
                    elif u.path == "/api/control":
                        if b.get('action') in ('resume', 'ready', 'more', 'retry'):
                            engine.ensure_room(s.ws, s.st['id'])
                        if b.get("action") == "retry":
                            s.st.pop("error", None)
                            s.save()
                        else:
                            s.control(b.get("action", ""))
                            if b.get('action') == 'stop':
                                workers.close(s.st)
                    elif u.path == "/api/steer":
                        s.steer(str(b.get("action", "")), b.get("value"))

                    elif u.path == "/api/answer":
                        if s.st['status'] not in session.ACTIVE or not s.panes_alive():
                            raise ValueError('The native workers for this request are no longer attached.')
                        s.answer_wait(str(b.get("agent", "")), bool(b.get("approve")))
                    elif u.path == "/api/restart":
                        engine.ensure_room(s.ws, s.st['id'])
                        s.restart_agent(str(b.get("agent", "")))
                    elif u.path == "/api/settings":
                        engine.ensure_room(s.ws, s.st['id'])
                        self._send(200, s.update_settings({k:v for k,v in b.items() if k != 'run_id'}))
                        return
                    elif u.path == "/api/keys":
                        if s.st['status'] not in session.ACTIVE or not s.panes_alive():
                            raise ValueError('These workers are not attached to this run. Use Continue first.')
                        pane = s.st["panes"].get(b.get("agent")) or _fail("No such pane.")
                        if b.get("key"):
                            send_key(pane, b["key"])
                        else:
                            send_text(pane, b.get("text", ""), enter=bool(b.get("enter", True)))
                    else:
                        self._send(404, {"error": "not found"})
                        return
                self._send(200, {"ok": True})
            except (ValueError, KeyError, RuntimeError, TypeError) as ex:
                self._send(400, {"error": str(ex)})

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    engine.server = srv
    return srv


RESTART = {"on": False}     # set by the Restart button: replace this process with a fresh engine


def replace_engine(port, home):
    """Start a clean interpreter in the selected core, without cached old imports."""
    target = coremgr.current_dir(home) or config.APP_DIR
    env = dict(os.environ, PYTHONPATH=str(target))
    os.chdir(target)
    os.execve(sys.executable, [sys.executable, '-m', 'engine', 'serve', str(port)], env)


def serve(port=None):
    trust.startup_setup()
    engine = Engine()
    srv = make_server(port or config.CFG["port"], engine)
    print("Duo engine on http://127.0.0.1:%d" % srv.server_address[1], flush=True)
    srv.serve_forever()
    if RESTART["on"]:
        # Agents run in tmux and keep going. The listening socket is closed before the exec, so the port is free.
        srv.server_close()
        print("Duo restarts to load the new code.", flush=True)
        replace_engine(srv.server_address[1], engine.duo_home())

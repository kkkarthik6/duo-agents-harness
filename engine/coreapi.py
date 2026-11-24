"""Duo core API. Contract version CONTRACT.

This is the only part of core a user feature may import:

    from engine import coreapi

Core is immutable. A feature adds and extends: it can listen to events, add routes, add UI panels in
named slots, add rules for agents, and add new stages and gates by inheriting Stage and Gate. It can
never replace or change a core stage, gate, prompt or default. Inside one contract major version, every
name in this file keeps working.

Contract history:
  1.0.0  events, routes, slots, instructions, settings, Stage, Gate, Context, RunView.
  1.1.0  past work: Context.sessions(), session(id), read_session_file(id, path), write_session(id, path, text);
         SessionView.
  1.2.0  components through adapters: Api.component(kind, **spec), Api.page(id, title, file, nav, order);
         COMPONENT_KINDS. A page is a full screen of the feature's own with a side bar button. Api.slot now
         declares a "panel" component (same behaviour as 1.0).
  1.3.0  typed feature settings, generated UI controls, and user-controlled component placement.
"""
from dataclasses import dataclass, field
from pathlib import Path

CONTRACT = "1.3.0"

# Events core sends. Each handler gets one dict argument.
#   app.started    {}                                  the engine started
#   run.started    {"run": RunView}
#   stage.changed  {"run": RunView, "from": str, "to": str}
#   gate.opened    {"run": RunView, "gate": str}
#   task.done      {"run": RunView, "task": str}
#   run.done       {"run": RunView}
EVENTS = ("app.started", "run.started", "stage.changed", "gate.opened", "task.done", "run.done")

# Named places in the page where a feature can show a panel.
SLOTS = ("home", "header", "settings")

# Kinds of UI component a feature can add. Each kind has an adapter in core (engine/components.py) and in
# the page. A kind that is not here needs a core request.
#   page   a full screen of the feature's own, with a side bar button; address #/f/<feature>/<id>
#   panel  a panel inside a core slot (see SLOTS)
COMPONENT_KINDS = ("page", "panel")

# Core stages, in order. A feature Stage or Gate runs after one of these.
CORE_STAGES = ("assess", "exchange", "plan_review", "understand", "equip", "research", "plan", "execute", "deliver")
METHODS = ("GET", "POST")


@dataclass(frozen=True)
class RunView:
    """A read-only copy of a run's state. Changing it changes nothing in core."""
    id: str = ""
    folder: str = ""
    task: str = ""
    stage: str = ""
    status: str = ""
    task_type: str = ""
    gate: str = ""
    agents: tuple = ()
    created: str = ""

    @classmethod
    def from_state(cls, st):
        st = st or {}
        return cls(id=str(st.get("id") or ""), folder=str(st.get("folder") or ""), task=str(st.get("task") or ""),
                   stage=str(st.get("stage") or ""), status=str(st.get("status") or ""),
                   task_type=str(st.get("task_type") or ""), gate=str(st.get("gate") or ""),
                   agents=tuple(st.get("agents") or ()), created=str(st.get("created") or ""))


@dataclass(frozen=True)
class SessionView:
    """A read-only line in the history: a session (kind "session")."""
    id: str = ""
    kind: str = ""
    title: str = ""
    task: str = ""
    status: str = ""
    created: str = ""
    task_type: str = ""
    folder: str = ""


class Context:
    """What a feature's stage, gate or route can use. Writes go only to <project>/duo/features/<name>/."""

    def __init__(self, feature, run=None, folder=None, notify=None, settings=None, runs=None, home=None):
        self.feature = feature
        self._home = home
        self.run = run
        self.folder = Path(folder) if folder else None
        self._notify = notify or (lambda text: None)
        self._settings = settings or {}
        self._runs = runs or (lambda: [])

    def out_dir(self):
        if self.folder is None:
            raise ValueError("This context has no project folder.")
        return self.folder / "duo" / "features" / self.feature

    def _inside(self, base, rel):
        p = (base / rel).resolve()
        if p != base.resolve() and base.resolve() not in p.parents:
            raise ValueError("Path is outside the allowed folder: %s" % rel)
        return p

    def write(self, rel, text):
        """Write a text file in this feature's folder of the project. Returns its path."""
        base = self.out_dir()
        base.mkdir(parents=True, exist_ok=True)
        p = self._inside(base, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def read(self, rel):
        """Read a text file anywhere in the project folder."""
        return self._inside(self.folder, rel).read_text(errors="replace")

    def note(self, text):
        """Add a line to the run's notes, shown in the page."""
        self._notify(str(text)[:500])

    def setting(self, key, default=None):
        return self._settings.get(key, default)

    def runs(self):
        """All runs the engine knows, as RunView objects."""
        return list(self._runs())

    # ---- past work (contract 1.1.0)
    def sessions(self):
        """Every past session and run, newest first, as SessionView objects."""
        from . import sessionlog
        return [SessionView(**i) for i in sessionlog.list_all(self._home)]

    def session(self, sid):
        """A summary of one session or run (a copy): title, task, status, chosen option, decisions, results,
        timeline, changed files, artifacts (files in its folder), delivery text. Unknown id: ValueError."""
        from . import sessionlog
        return sessionlog.detail(sid, self._home)

    def read_session_file(self, sid, rel):
        """Read a text file inside that session's folder (at most 200 kB)."""
        from . import sessionlog
        return sessionlog.read_file(sid, rel, self._home)

    def write_session(self, sid, rel, text):
        """Write a text file into <that session's folder>/duo/features/<this feature>/. Returns its path."""
        from . import sessionlog
        base = sessionlog.folder_of(sid, self._home) / "duo" / "features" / self.feature
        base.mkdir(parents=True, exist_ok=True)
        p = self._inside(base, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p


class Stage:
    """Inherit this to add a stage. It runs once, after the core stage named in `after`, without agents.
    It must finish within `timeout` seconds. It cannot replace a core stage."""
    name = ""
    after = "deliver"
    timeout = 10

    def run(self, ctx):
        raise NotImplementedError


class Gate:
    """Inherit this to add a gate. After the core stage named in `after`, core asks `needed(ctx)`.
    If true, the run waits and the page shows `card(ctx)`. The user's answer goes to `answer(ctx, payload)`."""
    name = ""
    after = "plan"
    timeout = 10

    def needed(self, ctx):
        return True

    def card(self, ctx):
        return {"title": self.name, "text": ""}

    def answer(self, ctx, payload):
        pass


@dataclass
class Api:
    """Passed to a feature's register(api). Each call records what the feature adds."""
    feature: str
    hooks: list = field(default_factory=list)          # (event, fn)
    routes: list = field(default_factory=list)         # (method, path, fn)
    slots: list = field(default_factory=list)          # (slot, file)
    rules: list = field(default_factory=list)          # text
    stages: list = field(default_factory=list)         # Stage instances
    gates: list = field(default_factory=list)          # Gate instances
    settings: dict = field(default_factory=dict)       # key -> default
    components: list = field(default_factory=list)     # (kind, spec)
    setting_specs: dict = field(default_factory=dict)  # editable setting descriptions

    def on(self, event, fn):
        if event not in EVENTS:
            raise ValueError("Unknown event %r. Known events: %s" % (event, ", ".join(EVENTS)))
        self.hooks.append((event, fn))

    def route(self, method, path, fn):
        """Serve fn at /api/f/<feature><path>. fn(req) gets {"body", "query", "ctx"} and returns (status, dict)."""
        method = method.upper()
        if method not in METHODS or not str(path).startswith("/"):
            raise ValueError("A route needs GET or POST and a path that starts with /.")
        self.routes.append((method, path, fn))

    def component(self, kind, **spec):
        """Add a UI component of a kind core has an adapter for (COMPONENT_KINDS). Core checks the spec when the
        feature loads: a wrong spec stops the feature from loading, with the reason."""
        if kind not in COMPONENT_KINDS:
            raise ValueError("Core has no adapter for the component kind %r (known: %s). Write a core request."
                             % (kind, ", ".join(COMPONENT_KINDS)))
        self.components.append((kind, dict(spec)))

    def page(self, id, title, file, nav="side", order=100):
        """Add a full page of this feature's own. `file` is a JS module (relative to the feature folder) with
        `export default function (el, duo)`; it fills the main view. nav "side" adds a side bar button."""
        self.component("page", id=id, title=title, file=file, nav=nav, order=order)

    def slot(self, name, file, order=100):
        """Show the JS module `file` (relative to the feature folder) in the page slot `name`."""
        if name not in SLOTS:
            raise ValueError("Unknown slot %r. Known slots: %s" % (name, ", ".join(SLOTS)))
        if ".." in Path(file).parts or Path(file).is_absolute() or not str(file).endswith(".js"):
            raise ValueError("A slot file must be a .js file inside the feature folder.")
        self.slots.append((name, str(file)))
        self.component("panel", slot=name, file=str(file), order=order)

    def instructions(self, text):
        """Rules for agents. Core rules always come first; these are added after them."""
        self.rules.append(str(text).strip())

    def setting(self, key, default=None, *, label=None, description="", choices=None, minimum=None, maximum=None):
        """Declare a feature setting. Scalar defaults receive typed controls in the Features screen.
        Optional choices, minimum and maximum constrain UI and API writes. Existing calls still work."""
        from .extensions import setting_spec
        key = str(key)
        spec = setting_spec(key, default, label, description, choices, minimum, maximum)
        self.settings[key] = default
        if spec:
            self.setting_specs[key] = spec

    def _check_new(self, cls, kind):
        inst = cls() if isinstance(cls, type) else cls
        base = Stage if kind == "stage" else Gate
        if not isinstance(inst, base):
            raise ValueError("A %s must inherit coreapi.%s." % (kind, base.__name__))
        if not inst.name or inst.name in CORE_STAGES:
            raise ValueError("A %s needs its own name, not a core stage name." % kind)
        if inst.after not in CORE_STAGES:
            raise ValueError("%s.after must be one of %s." % (kind.title(), ", ".join(CORE_STAGES)))
        return inst

    def stage(self, cls):
        self.stages.append(self._check_new(cls, "stage"))

    def gate(self, cls):
        self.gates.append(self._check_new(cls, "gate"))

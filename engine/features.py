"""User features: find them, check them, load them, and call them safely.

A feature is a folder user/features/<name>/ with feature.json, feature.py (with register(api)),
optional ui/*.js panels and tests/. It may import only the standard library, its own modules and
engine.coreapi. A feature that fails a check, needs a newer contract or raises while registering is not
loaded; the rest of Duo is not affected. Every call into feature code is guarded: an error is recorded,
never raised into core.
"""
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import types
from pathlib import Path

from . import components as compmod, coreapi

PKG = "duo_user_features"
NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
MAX_ERRORS = 50


# ------------------------------------------------------------------ versions
def _ver(s):
    parts = [int(p) for p in str(s).strip().split(".")]
    if not 1 <= len(parts) <= 3:
        raise ValueError
    return tuple(parts + [0] * (3 - len(parts)))


def satisfies(rng, version):
    """True when version (1.2.0) is inside rng: '>=1.0', '>=1.0,<2', '^1.1', '1.2.0'."""
    v = _ver(version)
    try:
        for part in str(rng).split(","):
            part = part.strip()
            m = re.match(r"^(>=|<=|>|<|==|\^)?\s*([\d.]+)$", part)
            if not m:
                raise ValueError
            op, want = m.group(1) or "==", _ver(m.group(2))
            ok = {">=": v >= want, "<=": v <= want, ">": v > want, "<": v < want, "==": v == want,
                  "^": v >= want and v[0] == want[0]}[op]
            if not ok:
                return False
    except (ValueError, KeyError):
        raise ValueError("Not a contract range: %r" % rng)
    return True


# ------------------------------------------------------------------ static check
def _bad_import(mod):
    return mod == "engine" or (mod.startswith("engine.") and mod != "engine.coreapi")


def static_check(feature_dir):
    """Problems with the feature's Python files: core imports other than engine.coreapi, or syntax errors."""
    out = []
    for f in sorted(Path(feature_dir).rglob("*.py")):
        rel = f.relative_to(feature_dir)
        if "tests" in rel.parts:
            continue                       # tests may use anything; they are not loaded by the engine
        try:
            tree = ast.parse(f.read_text(errors="replace"), str(rel))
        except SyntaxError as ex:
            out.append("%s: syntax error on line %s" % (rel, ex.lineno))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if _bad_import(a.name):
                        out.append("%s: imports %s. Use engine.coreapi only." % (rel, a.name))
            elif isinstance(node, ast.ImportFrom) and not node.level:
                mod = node.module or ""
                if mod == "engine":
                    bad = [a.name for a in node.names if a.name != "coreapi"]
                    if bad:
                        out.append("%s: imports engine.%s. Use engine.coreapi only." % (rel, bad[0]))
                elif _bad_import(mod):
                    out.append("%s: imports %s. Use engine.coreapi only." % (rel, mod))
            elif isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in ("import_module", "__import__") and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str) and _bad_import(node.args[0].value):
                    out.append("%s: loads %s by name. Use engine.coreapi only." % (rel, node.args[0].value))
    return out


# ------------------------------------------------------------------ discovery
def discover(user_dir):
    """Feature folders with a readable feature.json, sorted by name."""
    root = Path(user_dir) / "features"
    found = []
    if not root.is_dir():
        return found
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "feature.json").exists()):
        try:
            m = json.loads((d / "feature.json").read_text())
        except ValueError:
            m = {"_bad": True}
        found.append({"name": d.name, "dir": d, "manifest": m})
    return found


def check(entry, contract=coreapi.CONTRACT):
    """Problems that stop a feature from loading, before any of its code runs."""
    m, name = entry["manifest"], entry["name"]
    if m.get("_bad"):
        return ["feature.json is not valid JSON."]
    probs = []
    if not NAME.match(name):
        probs.append("The folder name must be lower case letters, digits and _.")
    if m.get("name", name) != name:
        probs.append("feature.json name must match the folder name.")
    try:
        if not satisfies(m.get("contract", ">=1.0"), contract):
            probs.append("Needs core contract %s. This core has %s." % (m.get("contract"), contract))
    except ValueError as ex:
        probs.append(str(ex))
    if not (entry["dir"] / "feature.py").exists():
        probs.append("feature.py is missing.")
    return probs + static_check(entry["dir"])


# ------------------------------------------------------------------ registry
class Registry:
    def __init__(self, user_dir=None):
        self.user_dir = Path(user_dir) if user_dir else None
        self.loaded, self.problems, self.errors = [], {}, []
        self.manifests = {}
        self.hooks = {e: [] for e in coreapi.EVENTS}
        self.routes = {}
        self.slots = {s: [] for s in coreapi.SLOTS}
        self.rules = []
        self.stages, self.gates = [], []
        self.components = []              # standard descriptions from the component adapters
        self.settings = {}
        self.setting_specs = {}
        self.runs_fn = lambda: []
        self.settings_fn = lambda feature: {}
        self.home = None                  # ~/Duo, for the session history; None means config.DUO_HOME

    # ---- errors
    def record(self, feature, where, error):
        self.errors.append({"feature": feature, "where": where, "error": str(error)[:2000],
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        del self.errors[:-MAX_ERRORS]

    def _guard(self, feature, where, fn, *args):
        try:
            return True, fn(*args)
        except Exception:
            self.record(feature, where, traceback.format_exc(limit=4))
            return False, None

    def _timed(self, feature, where, fn, timeout, *args):
        """Call fn in a thread; give up waiting after timeout seconds."""
        box = {}

        def go():
            box["ok"], box["val"] = self._guard(feature, where, fn, *args)
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self.record(feature, where, "Stopped waiting: it took more than %gs (time limit)." % timeout)
            return False, None
        return box.get("ok", False), box.get("val")

    # ---- merge one feature's registrations
    def add(self, api, manifest, comps=()):
        f = api.feature
        for event, fn in api.hooks:
            self.hooks[event].append((f, fn))
        for method, path, fn in api.routes:
            self.routes[(f, method, path)] = fn
        for c in comps:
            self.components.append(c)
            if c["kind"] == "panel":
                self.slots[c["slot"]].append((f, c["files"][0]))
        self.rules += [(f, r) for r in api.rules if r]
        self.stages += [(f, s) for s in api.stages]
        self.gates += [(f, g) for g in api.gates]
        self.settings[f] = dict(api.settings)
        self.setting_specs[f] = dict(api.setting_specs)
        self.manifests[f] = manifest
        self.loaded.append(f)

    # ---- calls into features
    def ctx(self, feature, run=None, folder=None, notify=None):
        vals = dict(self.settings.get(feature, {}))
        vals.update(self.settings_fn(feature) or {})
        return coreapi.Context(feature, run, folder, notify, vals, self.runs_fn, self.home)

    def emit(self, event, payload, wait=False, timeout=5):
        threads = []
        for f, fn in self.hooks.get(event, []):
            t = threading.Thread(target=self._guard, args=(f, "hook " + event, fn, dict(payload)), daemon=True)
            t.start()
            threads.append(t)
        if wait:
            for t in threads:
                t.join(timeout)

    def call_route(self, feature, method, path, body, query):
        fn = self.routes.get((feature, method.upper(), path))
        if fn is None:
            return 404, {"error": "No route %s %s in feature %s." % (method, path, feature)}
        req = {"body": body or {}, "query": query or {}, "ctx": self.ctx(feature)}
        ok, val = self._timed(feature, "route %s %s" % (method, path), fn, 30, req)
        if not ok:
            last = self.errors[-1]["error"].strip().splitlines()[-1] if self.errors else "error"
            return 500, {"error": "The feature %s failed: %s" % (feature, last)}
        try:
            code, payload = val
            return int(code), payload
        except (TypeError, ValueError):
            return 500, {"error": "The feature %s returned something other than (status, dict)." % feature}

    def after_stage(self, prev, run, folder, notify):
        """Run feature stages that follow core stage `prev`; return the feature gates that are needed now."""
        for f, s in self.stages:
            if s.after == prev:
                self._timed(f, "stage " + s.name, s.run, s.timeout,
                            self.ctx(f, run, folder, lambda t, f=f: notify("%s: %s" % (f, t))))
        return []  # Extension stages run; Duo approval cards do not block tasks.

    def answer_gate(self, gate_id, run, folder, payload, notify):
        _, f, name = gate_id.split(":", 2)
        g = next((g for ff, g in self.gates if ff == f and g.name == name), None)
        if g is None:
            return False
        c = self.ctx(f, run, folder, lambda t: notify("%s: %s" % (f, t)))
        ok, _ = self._timed(f, "gate %s answer" % name, g.answer, g.timeout, c, payload or {})
        return ok

    def serves(self, feature, rel):
        """True when rel is a file of one of the feature's components (the only feature files the page may load)."""
        return any(c["feature"] == feature and rel in c["files"] for c in self.components)

    def instruction_text(self):
        return "\n".join(r for _, r in self.rules)

    def user_rules(self):
        """The user's own rules from user/instructions.md, without HTML comments."""
        if not self.user_dir or not (self.user_dir / "instructions.md").exists():
            return ""
        text = re.sub(r"<!--.*?-->", "", (self.user_dir / "instructions.md").read_text(errors="replace"), flags=re.S)
        return text.strip()

    def all_instructions(self):
        return "\n".join(t for t in (self.instruction_text(), self.user_rules()) if t)

    def view(self):
        """What the page needs: each feature, its slots, and why a feature is not loaded."""
        names = sorted(set(self.loaded) | set(self.problems))
        out = []
        for n in names:
            m = self.manifests.get(n, {})
            out.append({"name": n, "loaded": n in self.loaded, "version": m.get("version", ""),
                        "description": m.get("description", ""), "problems": self.problems.get(n, []),
                        "slots": [s for s, items in self.slots.items() for ff, _ in items if ff == n]})
        comps = sorted((c for c in self.components if c.get("visible", True)), key=lambda c: (c["kind"], c.get("order", 100), c.get("title", ""), c["feature"]))
        return {"features": out, "slots": {s: [{"feature": f, "file": fl} for f, fl in v] for s, v in self.slots.items()},
                "components": [{k: v for k, v in c.items() if k != "files"} for c in comps],
                "errors": self.errors[-10:], "contract": coreapi.CONTRACT}


# ------------------------------------------------------------------ loading
def _purge():
    for k in [k for k in sys.modules if k == PKG or k.startswith(PKG + ".")]:
        del sys.modules[k]


def _import(entry):
    name = entry["name"]
    if PKG not in sys.modules:
        root = types.ModuleType(PKG)
        root.__path__ = []
        sys.modules[PKG] = root
    pkg_name = "%s.%s" % (PKG, name)
    init = entry["dir"] / "__init__.py"
    spec = importlib.util.spec_from_file_location(pkg_name, init if init.exists() else entry["dir"] / "feature.py",
                                                  submodule_search_locations=[str(entry["dir"])])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = pkg
    if init.exists():
        spec.loader.exec_module(pkg)
    mod_name = pkg_name + ".feature"
    spec2 = importlib.util.spec_from_file_location(mod_name, entry["dir"] / "feature.py")
    mod = importlib.util.module_from_spec(spec2)
    sys.modules[mod_name] = mod
    spec2.loader.exec_module(mod)
    return mod


def load(user_dir, disabled=(), contract=coreapi.CONTRACT):
    """Load every feature in user_dir that passes its checks. Never raises for a bad feature."""
    _purge()
    reg = Registry(user_dir)
    for entry in discover(user_dir):
        name = entry["name"]
        if name in set(disabled):
            reg.problems[name] = ["Turned off."]
            reg.manifests[name] = entry["manifest"]
            continue
        probs = check(entry, contract)
        if probs:
            reg.problems[name] = probs
            reg.manifests[name] = entry["manifest"] if not entry["manifest"].get("_bad") else {}
            continue
        api = coreapi.Api(name)
        try:
            mod = _import(entry)
            if not callable(getattr(mod, "register", None)):
                raise ValueError("feature.py has no register(api) function.")
            mod.register(api)
            comps, keys = [], set()
            for kind, spec in api.components:
                c = compmod.adapt(name, kind, spec, entry["dir"])
                k = (c["kind"], c.get("key") or c["module"])
                if k in keys:
                    raise ValueError("The %s %s is declared twice." % (kind, c.get("key") or c["module"]))
                keys.add(k)
                comps.append(c)
        except Exception as ex:
            reg.problems[name] = ["register failed: %s: %s" % (type(ex).__name__, ex)]
            reg.manifests[name] = entry["manifest"]
            continue
        reg.add(api, entry["manifest"], comps)
    return reg


_ACTIVE = [Registry()]


def activate(reg):
    _ACTIVE[0] = reg
    return reg


def active():
    return _ACTIVE[0]


# ------------------------------------------------------------------ tests
def has_tests(feature_dir):
    return any(Path(feature_dir, "tests").glob("test_*.py"))


def feature_tests(user_dir, core_dir, names=None, timeout=110):
    """Run each feature's tests in its own process, with core and the user folder on the path.
    Returns {name: (ok, output tail)}."""
    user_dir, core_dir = Path(user_dir), Path(core_dir)
    out = {}
    for entry in discover(user_dir):
        n = entry["name"]
        if names is not None and n not in names:
            continue
        if not has_tests(entry["dir"]):
            out[n] = (False, "This feature has no tests (features/%s/tests/test_*.py)." % n)
            continue
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=os.pathsep.join([str(core_dir), str(user_dir)]))
        try:
            r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "features/%s/tests" % n, "-t", "."],
                               cwd=str(user_dir), capture_output=True, text=True, timeout=timeout, env=env)
            tail = "\n".join((r.stderr or r.stdout).strip().splitlines()[-60:])
            out[n] = (r.returncode == 0, tail)
        except subprocess.TimeoutExpired:
            out[n] = (False, "The tests of %s did not finish in %ds." % (n, timeout))
    return out

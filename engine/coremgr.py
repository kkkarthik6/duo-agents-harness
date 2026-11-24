"""The sealed core: install it, check that nobody changed it, update it safely, and go back.

Layout in ~/Duo:
  core/<name>/          one folder per installed core (name = its CORE_VERSION, plus a suffix when a
                        same-numbered core is replaced). Only this module writes here.
  core/state.json       {"current": name, "previous": name|null}
  core/<name>/.duo-core.json   {"version", "contract", "files": {path: sha1}, "installed"}
  feature-state.json    features turned off after an update: {"disabled": {name: {"reason", "core", "since"}}}
  migrations.json       migrations already applied
  backups/<time>/       data copied before migrations run

An update: copy the new core beside the old one, run its core tests, run migrations (with a backup),
check every user feature against it, then switch. Failing core tests leave the old core running.
A failing feature is turned off alone, and an auto fix starts in a version clone. When the fix passes,
it is rolled up and the feature is turned on again.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import features, userspace

STATE = "state.json"
MANIFEST = ".duo-core.json"
SKIP_NAMES = {".git", "__pycache__", ".DS_Store", "node_modules", "target", ".superpowers", MANIFEST}
SKIP_TOP = {"config.json", "app/dist", "app/src-tauri/gen"}      # local settings and Mac app build output


def home(base=None):
    return userspace.home(base)


def root(base=None):
    return home(base) / "core"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _skip(rel):
    parts = rel.parts
    return (any(p in SKIP_NAMES for p in parts) or rel.suffix == ".pyc"
            or any(str(rel) == t or str(rel).startswith(t + "/") for t in SKIP_TOP))


def _files(src):
    src = Path(src)
    out = {}
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_file() and not p.is_symlink() and not _skip(rel):
            out[str(rel)] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def _copy(src, dest, files):
    for rel in files:
        d = dest / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(src) / rel, d)


def version_of(src):
    p = Path(src) / "CORE_VERSION"
    if not p.exists():
        raise ValueError("%s has no CORE_VERSION file, so it is not a Duo core." % src)
    return p.read_text().strip()


def contract_of(core_dir):
    p = Path(core_dir) / "engine" / "coreapi.py"
    m = re.search(r'^CONTRACT\s*=\s*"([\d.]+)"', p.read_text() if p.exists() else "", re.M)
    return m.group(1) if m else "0.0.0"


def _state(base):
    p = root(base) / STATE
    return json.loads(p.read_text()) if p.exists() else {"current": None, "previous": None}


def _save_state(base, st):
    userspace._write(root(base) / STATE, st)


def current_dir(base=None):
    cur = _state(base).get("current")
    return root(base) / cur if cur else None


def _manifest(d):
    p = Path(d) / MANIFEST
    return json.loads(p.read_text()) if p.exists() else {}


def info(base=None):
    st = _state(base)
    d = current_dir(base)
    m = _manifest(d) if d else {}
    prev = st.get("previous")
    return {"version": m.get("version"), "name": st.get("current"), "dir": str(d) if d else None,
            "contract": m.get("contract"), "previous": (_manifest(root(base) / prev).get("version") if prev else None),
            "previous_name": prev, "installed": m.get("installed")}


def verify(base=None):
    """Core files that differ from what was installed: changed, added or removed. Empty means sealed."""
    d = current_dir(base)
    if not d or not d.exists():
        return []
    want, have = _manifest(d).get("files", {}), _files(d)
    return sorted(k for k in set(want) | set(have) if want.get(k) != have.get(k))


def _stage(src, base):
    """Copy src into a new folder under core/. Returns (folder, version, files)."""
    ver, files = version_of(src), _files(src)
    root(base).mkdir(parents=True, exist_ok=True)
    tmp = root(base) / (".staging-%s-%d" % (ver, os.getpid()))
    if tmp.exists():
        shutil.rmtree(tmp)
    _copy(src, tmp, files)
    (tmp / MANIFEST).write_text(json.dumps({"version": ver, "contract": contract_of(tmp), "files": files,
                                            "installed": _now()}, indent=1))
    return tmp, ver, files


def _place(base, tmp, ver):
    """Move a staged core to core/<ver> (or core/<ver>-<n> when that name is taken). Returns the name."""
    name, n = ver, 2
    while (root(base) / name).exists():
        name, n = "%s-%d" % (ver, n), n + 1
    os.replace(tmp, root(base) / name)
    return name


def _same_as_current(base, files):
    d = current_dir(base)
    return bool(d and d.exists() and _manifest(d).get("files") == files)


def install(src, base=None):
    """First install, or re-install of the same code: no tests, no feature checks. Returns the core folder."""
    files = _files(src)
    if _same_as_current(base, files):
        return current_dir(base)
    tmp, ver, _ = _stage(src, base)
    name = _place(base, tmp, ver)
    st = _state(base)
    if st.get("current") and st["current"] != name:
        st["previous"] = st["current"]
    st["current"] = name
    _save_state(base, st)
    return root(base) / name


# ------------------------------------------------------------------ feature state
def _fs_path(base):
    return home(base) / "feature-state.json"


def _fs(base):
    p = _fs_path(base)
    return json.loads(p.read_text()) if p.exists() else {"disabled": {}}


def disabled_info(base=None):
    return _fs(base)["disabled"]


def disabled(base=None):
    return set(disabled_info(base))


def _disable(base, name, reason, core=None, fix=None):
    fs = _fs(base)
    old = fs["disabled"].get(name, {})
    fs["disabled"][name] = {"reason": reason, "core": core or old.get("core"), "since": old.get("since") or _now(),
                            "fix": fix or old.get("fix")}
    userspace._write(_fs_path(base), fs)


def _enable(base, name):
    fs = _fs(base)
    fs["disabled"].pop(name, None)
    userspace._write(_fs_path(base), fs)


# ------------------------------------------------------------------ tests
def run_core_tests(core_dir, timeout=110):
    """The tests an update must pass before Duo switches to it: tests/contract in the new core."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(core_dir))
    if not (Path(core_dir) / "tests" / "contract").is_dir():
        return False, "The new core has no tests/contract folder."
    try:
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests/contract", "-t", "."],
                           cwd=str(core_dir), capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return False, "The core tests did not finish in %ds." % timeout
    return r.returncode == 0, "\n".join((r.stderr or r.stdout).strip().splitlines()[-40:])


# ------------------------------------------------------------------ migrations
def _backup(base):
    h = home(base)
    dest = h / "backups" / time.strftime("%Y%m%d-%H%M%S")
    n = 2
    while dest.exists():
        dest = dest.with_name(dest.name.split("+")[0] + "+%d" % n)
        n += 1
    dest.mkdir(parents=True)
    for name in ("user-versions.json", "versions.json", "config.json", "feature-state.json", "migrations.json"):
        if (h / name).exists():
            shutil.copy2(h / name, dest / name)
    if (h / "user").exists():
        shutil.copytree(h / "user", dest / "user")
    return dest


def _restore(base, dest):
    h = home(base)
    for f in dest.iterdir():
        if f.name == "user":
            if (h / "user").exists():
                shutil.rmtree(h / "user")
            shutil.copytree(f, h / "user")
        else:
            shutil.copy2(f, h / f.name)


def run_migrations(core_dir, base=None):
    """Run the core's new migrations (engine/migrations/m*.py, each with migrate(home)), oldest first.
    Data is backed up first; if one fails, the backup is restored and the error is raised."""
    h = home(base)
    p = h / "migrations.json"
    done = json.loads(p.read_text()) if p.exists() else []
    todo = sorted(f for f in (Path(core_dir) / "engine" / "migrations").glob("m*.py") if f.stem not in done)
    if not todo:
        return []
    backup = _backup(base)
    ran = []
    try:
        for f in todo:
            spec = importlib.util.spec_from_file_location("duo_migration_" + f.stem, f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.migrate(h)
            ran.append(f.stem)
    except Exception:
        _restore(base, backup)
        raise
    userspace._write(p, done + ran)
    return ran


# ------------------------------------------------------------------ update
def _default_fix(base):
    def start(name, tail, core_dir):
        from . import feedback
        text = ("Fix the feature %s so it works with Duo core %s. Its tests failed after the core update. "
                "Keep what the feature does. Test output:\n%s" % (name, version_of(core_dir), tail[-3000:]))
        rec = feedback.submit(text, base, spawn=False, kind="minor", auto_fix=True, feature=name)
        _spawn_in(core_dir, rec["version"], base)
        return rec["version"]
    return start


def _spawn_in(core_dir, vid, base):
    """Start the feedback worker with the new core, so the fix is checked against it."""
    h = home(base)
    log = open(h / "feedback_worker.log", "ab")
    env = dict(os.environ, DUO_HOME=str(h), PYTHONPATH=str(core_dir))
    return subprocess.Popen([sys.executable, "-m", "engine.feedback", vid], cwd=str(core_dir), env=env,
                            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


def update(src, base=None, core_tests=run_core_tests, feature_tests=features.feature_tests, start_fix="default"):
    """Install a new core safely. Returns {"status": "updated"|"same"|"core_failed", "version", "detail",
    "disabled": [...], "fixes": [...]}."""
    if start_fix == "default":
        start_fix = _default_fix(base)
    files = _files(src)
    old = info(base)
    if _same_as_current(base, files):
        return {"status": "same", "version": old["version"], "detail": "Duo core %s is already installed." % old["version"],
                "disabled": [], "fixes": []}
    tmp, ver, _ = _stage(src, base)
    ok, tail = core_tests(tmp)
    if not ok:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"status": "core_failed", "version": old["version"], "disabled": [], "fixes": [],
                "detail": "The new core %s failed its own tests. Duo keeps core %s.\n%s" % (ver, old["version"], tail)}
    try:
        ran = run_migrations(tmp, base)
    except Exception as ex:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"status": "core_failed", "version": old["version"], "disabled": [], "fixes": [],
                "detail": "A data migration of core %s failed (%s). Duo restored your data and keeps core %s."
                          % (ver, ex, old["version"])}
    user = userspace.user_dir(base)
    contract = contract_of(tmp)
    off = disabled(base)
    bad = {}
    for e in features.discover(user):
        if e["name"] in off:
            continue
        probs = features.check(e, contract)
        if probs:
            bad[e["name"]] = "ERROR: " + "\nERROR: ".join(probs)
    names = [e["name"] for e in features.discover(user) if e["name"] not in off and e["name"] not in bad]
    for n, (fok, ftail) in feature_tests(user, tmp, names=names).items():
        if not fok:
            bad[n] = ftail
    name = _place(base, tmp, ver)
    st = _state(base)
    st["previous"], st["current"] = st.get("current"), name
    _save_state(base, st)
    fixes = []
    for n, ftail in sorted(bad.items()):
        fix = start_fix(n, ftail, root(base) / name) if start_fix else None
        _disable(base, n, "Its tests failed on core %s. Duo is fixing it." % ver, ver, fix)
        if fix:
            fixes.append(fix)
    new_core = root(base) / name

    def resend(text, kind):
        from . import feedback
        rec = feedback.submit(text, base, spawn=False, kind=kind)
        _spawn_in(new_core, rec["version"], base)
        return rec["version"]
    retried = retry_waiting(base, submit=resend if start_fix else (lambda text, kind: None))
    detail = "Updated Duo core from %s to %s." % (old["version"], ver)
    if ran:
        detail += " Data migrations: %s." % ", ".join(ran)
    if bad:
        detail += " Turned off until fixed: %s." % ", ".join(sorted(bad))
    return {"status": "updated", "version": ver, "previous": old["version"], "detail": detail,
            "disabled": sorted(bad), "fixes": fixes, "migrations": ran, "retried": [r for r in retried if r]}


def finish_fix(vid, status, base=None):
    """Called when an auto fix ends. Ready: roll it up and turn the feature on. Otherwise: keep it off."""
    v = userspace.entry(base, vid)
    name = v.get("feature")
    if status == "ready":
        try:
            new = userspace.roll_up(base, vid, auto=True)
        except ValueError as ex:
            if name:
                _disable(base, name, "The fix %s is ready but could not be rolled up: %s Roll it up by hand." % (v["number"], ex),
                         fix=vid)
            return None
        userspace.update(base, vid, detail="Auto fix after the Duo core update. Rolled up as %s." % new)
        if name:
            _enable(base, name)
        return new
    if name:
        _disable(base, name, "The auto fix %s did not pass (%s). Open Feedback and press Repair." % (v["number"], status),
                 fix=vid)
    return None


def retry_waiting(base=None, submit=None):
    """Send again every request that waited for a core change. Returns the new clone ids."""
    if submit is None:
        from . import feedback

        def submit(text, kind):
            return feedback.submit(text, base, spawn=True, kind=kind)["version"]
    out = []
    for v in list(userspace.load(base)["versions"]):
        if v.get("status") == "waiting_for_core":
            new = submit(v.get("feedback", ""), v.get("kind", "minor"))
            if new is None:
                continue
            userspace.update(base, v["id"], status="retried", detail="Sent again after a core update as %s." % new)
            out.append(new)
    return out


def go_back(base=None):
    """Switch to the previous core. Returns its version."""
    st = _state(base)
    if not st.get("previous") or not (root(base) / st["previous"]).exists():
        raise ValueError("There is no earlier core to go back to.")
    st["current"], st["previous"] = st["previous"], st["current"]
    _save_state(base, st)
    return info(base)["version"]

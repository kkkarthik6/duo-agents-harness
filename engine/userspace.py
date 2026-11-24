"""The user layer: your Duo's own features, outside the sealed core.

Layout in ~/Duo:
  user/                 the source_clone: rolled-up features. VERSION holds its number (V.0.0).
    features/<name>/    one folder per feature (see engine/features.py)
    instructions.md     your rules for agents, added after core rules
    contract.json       routes, hooks and slots your features offer each other
  versions/<id>/        version clones: copies of user/ made for one feature request or auto fix
  user-history/<num>/   earlier user layers, for Go back
  user-versions.json    the history: {"number", "versions": [...], "rolled": [...], "next_id"}

Numbers: the user layer is V.0.0. A clone made from it is V.(a+1).0 for a major feature or V.a.(b+1)
for a minor one, where a and b are the highest used so far. A roll-up makes the user layer (V+1).0.0.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import store

FIRST = "1.0.0"
KINDS = ("major", "minor")
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "_core_ref", "core_request.json")
INSTRUCTIONS = "<!-- Your rules for Duo's agents. Duo adds them after its own rules, which always come first. -->\n"


def home(base=None):
    return Path(base or os.environ.get("DUO_HOME") or Path.home() / "Duo")


def user_dir(base=None):
    return home(base) / "user"


def versions_dir(base=None):
    return home(base) / "versions"


def history_dir(base=None):
    return home(base) / "user-history"


def _manifest(base):
    return home(base) / "user-versions.json"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".uv-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _skeleton(u, number):
    (u / "features").mkdir(parents=True, exist_ok=True)
    if not (u / "features" / "__init__.py").exists():
        (u / "features" / "__init__.py").write_text("")
    if not (u / "instructions.md").exists():
        (u / "instructions.md").write_text(INSTRUCTIONS)
    if not (u / "contract.json").exists():
        (u / "contract.json").write_text(json.dumps({"version": "1.0.0", "hooks": [], "routes": [], "slots": []}, indent=1) + "\n")
    (u / "VERSION").write_text(number + "\n")


def ensure(base=None, start=FIRST):
    """Create the user layer and its history on first use. Keeps what already exists."""
    p = _manifest(base)
    if p.exists():
        data = load(base)
        u = user_dir(base)
        if not u.exists():
            _skeleton(u, data["number"])
        return data
    _skeleton(user_dir(base), start)
    versions_dir(base).mkdir(parents=True, exist_ok=True)
    data = {"number": start, "versions": [], "rolled": [], "next_id": 1, "created": _now()}
    _write(p, data)
    return data


def load(base=None):
    p = _manifest(base)
    if not p.exists():
        return {"number": FIRST, "versions": [], "rolled": [], "next_id": 1}
    return json.loads(p.read_text())


def save(base, data):
    _write(_manifest(base), data)


def migrate_legacy(base=None):
    """First run of the sealed-core layout. The user layer starts at the number of the legacy current version
    (an existing install keeps its number); a fresh install starts at 1.0.0. The legacy files are not changed."""
    if _manifest(base).exists():
        return load(base)
    start = FIRST
    legacy = home(base) / "versions.json"
    if legacy.exists():
        try:
            num = store.summary(home(base))["current_number"]
            if num:
                start = "%d.0.0" % store.parse(num)[0]
        except Exception:
            pass
    return ensure(base, start)


def entry(base, vid):
    v = next((x for x in load(base)["versions"] if x["id"] == vid), None)
    if v is None:
        raise ValueError("No version named %s." % vid)
    return v


def update(base, vid, **fields):
    data = load(base)
    v = next((x for x in data["versions"] if x["id"] == vid), None)
    if v is None:
        raise ValueError("No version named %s." % vid)
    v.update(fields)
    save(base, data)
    return v


def _used(data):
    """(a, b) pairs already used by clones of the current user number."""
    V = store.parse(data["number"])[0]
    out = []
    for v in data["versions"]:
        try:
            n = store.parse(v["number"])
        except ValueError:
            continue
        if n[0] == V:
            out.append((n[1], n[2]))
    return V, out


def next_number(data, kind):
    if kind not in KINDS:
        raise ValueError("The update type must be major or minor.")
    V, used = _used(data)
    top_a = max((a for a, _ in used), default=0)
    if kind == "major":
        return "%d.%d.0" % (V, top_a + 1)
    top_b = max((b for a, b in used if a == top_a), default=0)
    return "%d.%d.%d" % (V, top_a, top_b + 1)


def repair_number(base, vid):
    """The next free minor number in the same series as the version being repaired."""
    data = load(base)
    v = next(x for x in data["versions"] if x["id"] == vid)
    V, a, _ = store.parse(v["number"])
    top_b = max((b for aa, b in _used(data)[1] if aa == a), default=0)
    return "%d.%d.%d" % (V, a, top_b + 1)


def _trial(data):
    """The version being tried, if it is still ready; else None."""
    vid = data.get("trying")
    v = next((x for x in data["versions"] if x["id"] == vid), None) if vid else None
    return v if v and v.get("status") == "ready" and Path(v["path"]).is_dir() else None


def summary(base=None):
    data = load(base)
    t = _trial(data)
    return dict(data, next={k: next_number(data, k) for k in KINDS},
                trying={"id": t["id"], "number": t["number"]} if t else None)


def try_version(base, vid):
    """Use a ready version for a while without rolling it up. Your Duo's number does not change."""
    data = load(base)
    v = next((x for x in data["versions"] if x["id"] == vid), None)
    if v is None:
        raise ValueError("No version named %s." % vid)
    if v.get("status") != "ready":
        raise ValueError("Only a version that passed its tests can be used.")
    data["trying"] = vid
    v["tried"] = True
    save(base, data)
    return v["number"]


def stop_trying(base=None):
    data = load(base)
    data["trying"] = None
    save(base, data)


def active_dir(base=None):
    """The features to load: the version being tried, else your Duo (user/)."""
    t = _trial(load(base))
    return Path(t["path"]) if t else user_dir(base)


def clone(base, text, kind="minor", auto_fix=False, feature=None):
    """Copy the user layer into versions/<id>/ for one request. Returns the new entry."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Write the feedback first.")
    data = ensure(base)
    number = next_number(data, kind)
    vid = "c%d" % data.get("next_id", 1)
    dest = versions_dir(base) / vid
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(user_dir(base), dest, ignore=SKIP)
    v = {"id": vid, "number": number, "kind": kind, "base": data["number"], "feedback": text[:2000],
         "created": _now(), "path": str(dest), "status": "editing", "detail": "Duo is editing a copy.",
         "auto_fix": bool(auto_fix), "feature": feature}
    data["versions"].append(v)
    data["next_id"] = data.get("next_id", 1) + 1
    save(base, data)
    return v


def _bump_v(number):
    return "%d.0.0" % (store.parse(number)[0] + 1)


def roll_up(base, vid, auto=False):
    """Make a ready clone your Duo. The old user layer is kept for Go back. Returns the new number."""
    data = load(base)
    v = next((x for x in data["versions"] if x["id"] == vid), None)
    if v is None:
        raise ValueError("No version named %s." % vid)
    if v.get("status") != "ready":
        raise ValueError("Only a version that passed its tests can be rolled up.")
    if v.get("base") != data["number"]:
        raise ValueError("This version was made from %s. Your Duo is now %s. Send the feedback again to make it from %s."
                         % (v.get("base"), data["number"], data["number"]))
    u, src = user_dir(base), Path(v["path"])
    old, new = data["number"], _bump_v(data["number"])
    keep = history_dir(base) / old
    if keep.exists():
        shutil.rmtree(keep)
    keep.parent.mkdir(parents=True, exist_ok=True)
    tmp = home(base) / ".user-new"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp, ignore=SKIP)
    (tmp / "VERSION").write_text(new + "\n")
    os.replace(u, keep)
    os.replace(tmp, u)
    v.update(status="rolled_up", rolled_into=new, rolled_at=_now())
    data["number"] = new
    data["trying"] = None
    data.setdefault("rolled", []).append({"from": old, "to": new, "version": vid, "number": v["number"],
                                          "auto_fix": bool(auto), "at": _now()})
    save(base, data)
    return new


def go_back(base=None):
    """Undo the last roll-up: the previous user layer becomes your Duo again. Returns its number."""
    data = load(base)
    rolled = data.get("rolled") or []
    if not rolled:
        raise ValueError("There is no earlier version to go back to.")
    last = rolled[-1]
    keep = history_dir(base) / last["from"]
    if not keep.exists():
        raise ValueError("The earlier version %s is missing from %s." % (last["from"], keep))
    u = user_dir(base)
    undone = history_dir(base) / ("%s-undone" % last["to"])
    if undone.exists():
        shutil.rmtree(undone)
    os.replace(u, undone)
    os.replace(keep, u)
    data["number"] = last["from"]
    data["trying"] = None
    rolled.pop()
    for v in data["versions"]:
        if v["id"] == last["version"]:
            v.update(status="ready", rolled_into=None, detail="Went back to %s. You can roll this up again." % last["from"])
    data.setdefault("went_back", []).append({"from": last["to"], "to": last["from"], "at": _now()})
    save(base, data)
    return last["from"]

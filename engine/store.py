"""Versions of Duo on disk: ~/Duo/ holds the installed source, the feedback clones and versions.json.

Layout:
  ~/Duo/source/            the code that shipped with the installer (never edited)
  ~/Duo/clones/<id>/       a copy of a version, made when feedback is applied
  ~/Duo/versions.json      history and the current version: {"current": id, "versions": [...]}

Each version records the feedback text that produced it. Switching only moves "current";
nothing is deleted.
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "target", ".duo*")

# Version numbers read like 2.1.1 (major.minor.patch). Feedback is either "minor" (adds or changes a
# feature, bumps the middle number) or "major" (changes how Duo works, bumps the first number).
# "patch" is used by Repair: each repair attempt on a version bumps its last number.
FIRST_NUMBER = "2.0.0"
KINDS = ("minor", "major")


def parse(num):
    """'2.1.3' -> (2, 1, 3). Anything else is refused."""
    parts = str(num or "").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError("Not a version number: %s" % num)
    return tuple(int(p) for p in parts)


def bump(num, kind):
    major, minor, patch = parse(num)
    if kind == "major":
        return "%d.0.0" % (major + 1)
    if kind == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if kind == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    raise ValueError("The update type must be minor or major.")


def latest_number(data):
    return max((v["number"] for v in data["versions"]), key=parse, default=FIRST_NUMBER)


def _fill(data):
    """Give every version a number. Entries saved before numbering get one in order, as minor steps."""
    last = None
    for v in data["versions"]:
        if not v.get("number"):
            v["number"] = FIRST_NUMBER if v["id"] == "source" else bump(last or FIRST_NUMBER, "minor")
        last = v["number"]
    return data


def home(base=None):
    return Path(base or os.environ.get("DUO_HOME") or Path.home() / "Duo")


def _manifest(h):
    return h / "versions.json"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".versions-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def load(base=None):
    p = _manifest(home(base))
    data = json.loads(p.read_text()) if p.exists() else {"current": None, "versions": []}
    missing = any(not v.get("number") for v in data["versions"])
    _fill(data)
    if missing and p.exists():
        _write_json(p, data)            # save the numbers once, so they never move later
    return data


def summary(base=None):
    """What the page shows: the manifest, the current version number, and the number each update type would give."""
    data = load(base)
    cur = next((v for v in data["versions"] if v["id"] == data["current"]), None)
    return dict(data, current_number=cur["number"] if cur else None,
                next={k: bump(latest_number(data), k) for k in KINDS})


def install(bundled_source, base=None):
    """First launch: copy the bundled source to ~/Duo/source and record it as the first version. Idempotent."""
    h = home(base)
    data = load(base)
    if data["versions"]:
        return data
    src = h / "source"
    if not src.exists():
        shutil.copytree(bundled_source, src, ignore=SKIP)
    (h / "clones").mkdir(exist_ok=True)
    data = {"current": "source", "versions": [
        {"id": "source", "number": FIRST_NUMBER, "created": _now(), "path": str(src), "origin": "install",
         "feedback": "", "note": "Installed with Duo"}]}
    _write_json(_manifest(h), data)
    return data


def clone(from_id, feedback_text, base=None, make_current=True, kind="minor"):
    """Copy a version into clones/<new id> and record the feedback that asked for it.

    The new version gets the next number after the newest one: minor (2.1.0 -> 2.2.0) or major (2.1.0 -> 3.0.0).
    """
    h = home(base)
    data = load(base)
    origin = next((v for v in data["versions"] if v["id"] == from_id), None)
    if origin is None:
        raise ValueError("No version named %s." % from_id)
    text = (feedback_text or "").strip()
    if not text:
        raise ValueError("Write the feedback first.")
    if kind not in KINDS:
        raise ValueError("The update type must be minor or major.")
    number = bump(latest_number(data), kind)
    new_id = "v%d" % (len(data["versions"]) + 1)
    dest = h / "clones" / new_id
    if dest.exists():
        raise ValueError("%s already exists." % dest)
    shutil.copytree(origin["path"], dest, ignore=SKIP)
    data["versions"].append({"id": new_id, "number": number, "kind": kind, "created": _now(), "path": str(dest),
                             "origin": from_id, "feedback": text[:2000], "note": ""})
    if make_current:
        data["current"] = new_id
    _write_json(_manifest(h), data)
    return data["versions"][-1]


def switch(version_id, base=None):
    """Make an existing version the current one. Returns its folder."""
    h = home(base)
    data = load(base)
    v = next((v for v in data["versions"] if v["id"] == version_id), None)
    if v is None:
        raise ValueError("No version named %s." % version_id)
    if not Path(v["path"]).exists():
        raise ValueError("Version %s is missing its folder: %s" % (version_id, v["path"]))
    data["current"] = version_id
    _write_json(_manifest(h), data)
    return Path(v["path"])


def fingerprint(folder):
    """A hash of every file's relative path and bytes, so a changed source is noticed even when VERSION is the same."""
    import hashlib
    h = hashlib.sha1()
    root = Path(folder)
    for f in sorted(p for p in root.rglob("*") if p.is_file() and not _skipped(p)):
        h.update(str(f.relative_to(root)).encode() + b"\0")
        h.update(f.read_bytes() + b"\0")
    return h.hexdigest()


def _skipped(p):
    return any(part in ("__pycache__", ".git", "target") or part.startswith(".duo") for part in p.parts) or p.suffix == ".pyc"


def sync_source(bundled, base=None):
    """Keep ~/Duo/source equal to the bundled source (the installer or the folder you edit).
    Returns True when source changed. Clones are never touched."""
    h = home(base)
    data = load(base)
    src = h / "source"
    if not any(v["id"] == "source" for v in data["versions"]):
        install(bundled, base)
        return True
    if src.exists() and fingerprint(src) == fingerprint(bundled):
        return False
    tmp = h / ".source-new"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(bundled, tmp, ignore=SKIP)
    if src.exists():
        shutil.rmtree(src)
    tmp.rename(src)
    for v in data["versions"]:
        if v["id"] == "source":
            v["updated"] = _now()
    _write_json(_manifest(h), data)
    return True


def update_version(version_id, base=None, **fields):
    """Set fields on one version entry and save."""
    h = home(base)
    data = load(base)
    v = next((x for x in data["versions"] if x["id"] == version_id), None)
    if v is None:
        raise ValueError("No such version: %s" % version_id)
    v.update(fields)
    _write_json(_manifest(h), data)
    return v


def current_path(base=None):
    data = load(base)
    v = next((v for v in data["versions"] if v["id"] == data["current"]), None)
    return Path(v["path"]) if v else None


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

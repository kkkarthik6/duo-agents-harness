"""Read-only history of past work, for user features (core.api contract 1.1.0).

One kind:
  session  a session, listed in <projects root>/.duo-index.json, state in <workspace>/.duo/<id>/state.json

Features see copies only. Reading a file is limited to that session's own folder.
"""
import json
from pathlib import Path

from . import config, orchestration as orch

SKIP_DIRS = {".duo", ".git", "node_modules", "__pycache__", ".venv", "venv", "target", "duo"}
MAX_FILES = 300
MAX_READ = 200_000


def _home(home):
    return Path(home or config.DUO_HOME)


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _v2(home=None):
    from . import session_index
    for _, i in session_index.entries(home=home):
        st = _load(Path(i.get("duo_dir", "")) / "state.json")
        if st:
            yield st


def _first_line(text):
    return (str(text or "").strip().splitlines() or [""])[0][:120]


def _item(st, kind):
    return {"id": str(st.get("id", "")), "kind": kind,
            "title": st.get("title") or _first_line(st.get("task")),
            "task": str(st.get("task") or ""), "status": str(st.get("status") or ""),
            "created": str(st.get("created") or ""), "task_type": str(st.get("task_type") or ""),
            "folder": str(st.get("workspace") or st.get("folder") or "")}


def list_all(home=None, limit=200):
    items = [_item(st, "session") for st in _v2(home)]
    items.sort(key=lambda i: i["created"], reverse=True)
    return items[:limit]


def _find(sid, home):
    for st in _v2(home):
        if str(st.get("id")) == sid:
            return st, "session"
    raise ValueError("No session named %s." % sid)


def _artifacts(folder):
    root = Path(folder)
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]) or rel.name.startswith("."):
            continue
        if p.is_file():
            out.append(str(rel))
            if len(out) >= MAX_FILES:
                break
    return out


def detail(sid, home=None):
    """A summary of one session or run: what it was, what was decided, the results, and where the files are."""
    st, kind = _find(str(sid), home)
    d = _item(st, kind)
    d["detail"] = str(st.get("detail") or "")
    d["artifacts"] = _artifacts(d["folder"])
    if kind == "session":
        titles = {o.get("id"): o.get("title") for o in st.get("options") or []}
        d["chosen"] = titles.get(st.get("chosen"), st.get("chosen"))
        d["decisions"] = [{"id": x.get("id"), "question": x.get("title") or x.get("question") or "",
                           "chosen": titles.get(x.get("chosen"), x.get("chosen")), "status": x.get("status")}
                          for x in st.get("decisions") or []]
        entries = st.get("entries") or []
        d["results"] = [e.get("summary", "") for e in entries if e.get("phase") in ("verify", "smoke", "done")]
        d["timeline"] = [{"n": e.get("n"), "author": e.get("author"), "phase": e.get("phase"), "summary": e.get("summary", "")}
                         for e in entries[-300:]]
        d["changed"] = sorted({c for e in entries for c in (e.get("changed") or [])})
        d["delivery"] = ""
        d["chat"] = str(Path(st.get("duo_dir", "")) / "chat.md")
    for key in ("timeline", "results", "detail"):
        d[key] = orch.redact(st, d[key])
    return d


def folder_of(sid, home=None):
    st, _ = _find(str(sid), home)
    return Path(str(st.get("workspace") or st.get("folder") or ""))


def _inside(base, rel):
    p = (Path(base) / rel).resolve()
    b = Path(base).resolve()
    if p != b and b not in p.parents:
        raise ValueError("Path is outside the session folder: %s" % rel)
    return p


def read_file(sid, rel, home=None):
    p = _inside(folder_of(sid, home), rel)
    if not p.is_file():
        raise ValueError("No file %s in that session." % rel)
    return p.read_bytes()[:MAX_READ].decode("utf-8", "replace")

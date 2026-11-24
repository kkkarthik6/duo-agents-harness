"""Projects folder setup and pre-accepted folder trust for the agent CLIs."""
import contextlib
import fcntl
import json
import os
import re
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path

from . import config


def _atomic_write(path, text):
    """Write beside the file under a name no other process uses, then replace it in one step."""
    tmp = path.with_name("%s.duo-%d-%s.tmp" % (path.name, os.getpid(), os.urandom(4).hex()))
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


@contextlib.contextmanager
def _locked(path):
    """One writer at a time across processes. Two runs starting together used to overwrite each other's edit
    and could leave a half-written line in the user's own agent config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".duo-lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def trust_claude(path):
    """Claude Code: projects[path].hasTrustDialogAccepted = true in ~/.claude.json."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    cfg = Path(base) / ".claude.json" if base else Path(os.environ["HOME"]) / ".claude.json"
    try:
        with _locked(cfg):
            try:
                data = json.loads(cfg.read_text()) if cfg.exists() else {}
            except Exception:
                return False                      # never touch a file we can't parse
            entry = data.setdefault("projects", {}).setdefault(str(path), {})
            if entry.get("hasTrustDialogAccepted") is True:
                return True
            entry["hasTrustDialogAccepted"] = True
            _atomic_write(cfg, json.dumps(data, indent=2))
            return True
    except Exception:
        return False


def trust_codex(path):
    """Codex: [projects."<path>"] trust_level = "trusted" in ~/.codex/config.toml."""
    home = Path(os.environ.get("CODEX_HOME") or Path(os.environ["HOME"]) / ".codex")
    cfg = home / "config.toml"
    key = '[projects."%s"]' % str(path).replace("\\", "\\\\").replace('"', '\\"')
    try:
        with _locked(cfg):
            text = cfg.read_text() if cfg.exists() else ""
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                return False                  # a file Codex cannot read is the user's to fix; never add to it
            if key in text:
                return True
            if re.search(r"^\s*projects\s*=", text, re.M):
                return False                  # inline-table style; leave the user's file alone
            sep = "" if not text or text.endswith("\n") else "\n"
            new = text + sep + "\n%s\ntrust_level = \"trusted\"\n" % key
            tomllib.loads(new)                # never write something Codex could not load
            _atomic_write(cfg, new)
            return True
    except Exception:
        return False


def trust_folder(path):
    if not config.CFG.get("auto_trust", True):
        return {"claude": False, "codex": False}
    path = Path(path).resolve()
    return {"claude": trust_claude(path), "codex": trust_codex(path)}


def in_root(path):
    try:
        Path(path).resolve().relative_to(config.root().resolve())
        return True
    except ValueError:
        return False


def setup_project(ws):
    """Make a folder ready for the agents: git repo, .duo kept out of git, trusted."""
    ws = Path(ws)
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=ws, check=False)
    if (ws / ".git").is_dir():
        excl = ws / ".git" / "info" / "exclude"
        excl.parent.mkdir(parents=True, exist_ok=True)
        cur = excl.read_text() if excl.exists() else ""
        if ".duo/" not in cur:
            excl.write_text(cur + ("\n" if cur and not cur.endswith("\n") else "") + ".duo/\n")
    return trust_folder(ws)


def list_projects():
    root = config.root()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() and not p.name.startswith((".", "_")):
            n = len(list((p / ".duo").glob("*/state.json"))) if (p / ".duo").is_dir() else 0
            out.append({"name": p.name, "sessions": n,
                        "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")})
    return out


def startup_setup():
    root = config.root()
    root.mkdir(parents=True, exist_ok=True)
    trust_folder(root)
    for p in list_projects():
        trust_folder(root / p["name"])


def unique_project_dir(name):
    root = config.root()
    base = re.sub(r"[/:\\]+", "-", name).strip(" .") or "project"
    cand, i = root / base, 2
    while cand.exists():
        cand, i = root / ("%s-%d" % (base, i)), i + 1
    return cand

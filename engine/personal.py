"""Feedback and personalization. Feedback becomes small, undoable layers; nothing edits base files.

Every change passes the security guard first, then an allow-list. Anything else is declined.
"""
import json
import re
import shutil
import subprocess
import time

from . import agents, config, security, ste

UI = {"density": ("comfortable", "compact"), "summary_lines": (1, 2), "theme": ("light", "dark", "system"),
      "sidebar": ("open", "closed"), "inspector_default": ("insights", "agents", "last_decision"),
      "confirm_restart": (True, False)}
SETTINGS = {"decision_mode": config.MODES, "default_task_type": config.TASK_TYPES}
BOOLS = ()
RANGES = {"max_rounds": (1, 8), "auto_restart": (0, 5), "stall_minutes": (1, 60)}
KINDS = ("setting", "rule", "ui", "code")
SCHEMA = """Allowed changes (anything else is declined):
- setting: decision_mode (auto|big|always), default_task_type (programming|research|general), max_rounds 1-8, auto_restart 0-5, stall_minutes 1-60
- rule: path "house_rules", value = one short rule, 25 words or fewer, plain technical English
- ui: density comfortable|compact, summary_lines 1|2, theme light|dark|system, sidebar open|closed, inspector_default insights|agents|last_decision, confirm_restart true|false
- code: {"title", "request"} for a change to Duo's own code (needs review before it ships)
Never change security. Reply with JSON only: {"changes":[{"feedback_ids":[],"kind":"","path":"","value":null,"why":""}],"declined":[{"feedback_id":"","why":""}]}"""


def _fb_path():
    return config.root() / ".duo-feedback.jsonl"


def _layers_path():
    return config.root() / ".duo-personal.json"


def _proposals_path():
    return config.root() / ".duo-proposals.json"


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ----------------------------------------------------------------- feedback
def list_feedback():
    p = _fb_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return list(reversed(out))


def _save_feedback(items):
    _write(_fb_path(), "".join(json.dumps(f) + "\n" for f in reversed(items)))


def add_feedback(text, context=None, reaction=None):
    text = (text or "").strip()
    if not text:
        raise ValueError("Write some feedback first.")
    f = {"id": "f%d" % (len(list_feedback()) + 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "text": text[:2000],
         "context": context or {}, "reaction": reaction, "status": "pending", "result": ""}
    p = _fb_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(f) + "\n")
    return f


def _mark(ids, status, result):
    items = list_feedback()
    for f in items:
        if f["id"] in ids:
            f["status"], f["result"] = status, result
    _save_feedback(list(reversed(items)))


# ------------------------------------------------------------------- layers
def layers():
    p = _layers_path()
    return json.loads(p.read_text()) if p.exists() else []


def _active(kind=None):
    return [l for l in layers() if l["active"] and (kind is None or l["kind"] == kind)]


def _validate(ch):
    """Returns the cleaned (kind, path, value). Raises ValueError with a reason the user can read."""
    kind, path, value = ch.get("kind"), str(ch.get("path", "")), ch.get("value")
    found = security.check_layer({"kind": kind, "path": path, "value": value})
    if found:
        for f in found:
            security.log(f, source="feedback", action="denied")
        raise ValueError("Security: %s" % found[0].reason)
    if kind == "setting":
        if path in SETTINGS and value in SETTINGS[path]:
            return kind, path, value
        if path in BOOLS and isinstance(value, bool):
            return kind, path, value
        lo_hi = RANGES.get(path)
        if lo_hi and isinstance(value, int) and not isinstance(value, bool) and lo_hi[0] <= value <= lo_hi[1]:
            return kind, path, value
        raise ValueError("Not an allowed setting or value: %s = %s" % (path, value))
    if kind == "ui":
        if path in UI and value in UI[path]:
            return kind, path, value
        raise ValueError("Not an allowed UI choice: %s = %s" % (path, value))
    if kind == "rule":
        text = str(value or "").strip()
        if path != "house_rules" or not text:
            raise ValueError("Rules go in house_rules.")
        if len(text.split()) > 25:
            raise ValueError("A house rule has 25 words at most.")
        return kind, "house_rules", text
    raise ValueError("Not an allowed change kind: %s" % kind)


def apply_change(ch):
    kind, path, value = _validate(ch)
    if kind == "code":
        raise ValueError("Code changes go through a proposal.")
    previous = config.CFG.get(path) if kind == "setting" else None
    layer = {"id": "l%d" % (len(layers()) + 1), "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "feedback_ids": list(ch.get("feedback_ids") or []), "kind": kind, "path": path, "value": value,
             "previous": previous, "why": str(ch.get("why", ""))[:300], "active": True}
    _write(_layers_path(), json.dumps(layers() + [layer], indent=1))
    apply_settings()
    return layer


def undo(layer_id):
    ls = layers()
    hit = next((l for l in ls if l["id"] == layer_id), None)
    if not hit:
        raise ValueError("No such change.")
    hit["active"] = False
    _write(_layers_path(), json.dumps(ls, indent=1))
    if hit["kind"] == "setting":
        first = next(l for l in ls if l["kind"] == "setting" and l["path"] == hit["path"])
        config.CFG[hit["path"]] = first["previous"]
    apply_settings()
    return hit


def apply_settings():
    for l in _active("setting"):
        config.CFG[l["path"]] = l["value"]


def ui_prefs():
    out = {}
    for l in _active("ui"):
        out[l["path"]] = l["value"]
    return out


def house_rules():
    return [l["value"] for l in _active("rule")]


# Rules every run follows. The user cannot switch these off; house rules come on top.
BUILTIN_RULES = (
    "Never use synthetic, simulated, sample or made-up data for research or downloads.",
    "If real data is missing, stop and report it. Do not fill the gap with invented data.",
)


def protocol_rules():
    rules = list(BUILTIN_RULES) + house_rules()
    return ("\n## House rules\nThe user set these rules. Follow them.\n" + "".join("- %s\n" % r for r in rules)) if rules else ""


# -------------------------------------------------------------- interpreter
def build_prompt(items):
    lines = ["Duo is a tool where agents work together. Turn the user's feedback into small changes.", SCHEMA,
             "Current changes: %s" % json.dumps([{k: l[k] for k in ("kind", "path", "value")} for l in _active()]),
             "Feedback:"]
    for f in items:
        lines.append("- id %s%s: %s  (context: %s)" % (f["id"], " [%s call]" % f["reaction"] if f.get("reaction") else "",
                                                       f["text"], json.dumps(f.get("context") or {})))
    return "\n".join(lines)


def cli_runner(prompt):
    """Ask an installed agent CLI in headless mode. Uses the user's own login; no API key."""
    for cmd in (["claude", "-p"], ["codex", "--no-daemon", "exec", "--ephemeral"]):
        if shutil.which(cmd[0]):
            guarded = agents.guarded_argv(cmd[0], cmd + [prompt], config.root())
            r = subprocess.run(guarded, cwd=config.root(), capture_output=True, text=True, timeout=180,
                               env=agents.codex_env() if cmd[0] == "codex" else None)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
    return ""


def _json_from(text):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    try:
        return json.loads(m.group(1)) if m else None
    except ValueError:
        return None


def _queue_proposal(ch):
    p = _proposals_path()
    items = json.loads(p.read_text()) if p.exists() else []
    found = security.check_layer({"kind": "code", "path": str(ch.get("title", "")), "value": ch.get("value")})
    if found:
        for f in found:
            security.log(f, source="feedback", action="denied")
        raise ValueError("Security: %s" % found[0].reason)
    item = {"id": "p%d" % (len(items) + 1), "title": str(ch.get("title") or ch.get("why") or "Change")[:120],
            "request": str(ch.get("value") or "")[:1000], "feedback_ids": ch.get("feedback_ids") or [], "status": "queued"}
    _write(p, json.dumps(items + [item], indent=1))
    return item


def proposals():
    p = _proposals_path()
    return json.loads(p.read_text()) if p.exists() else []


def update_proposal(pid, **kw):
    items = proposals()
    hit = next((x for x in items if x["id"] == pid), None)
    if not hit:
        raise ValueError("No such proposal.")
    hit.update(kw)
    _write(_proposals_path(), json.dumps(items, indent=1))
    return hit


def get_proposal(pid):
    hit = next((x for x in proposals() if x["id"] == pid), None)
    if not hit:
        raise ValueError("No such proposal.")
    return hit


def process(runner=cli_runner):
    """Turn pending feedback into layers. Returns what happened."""
    pending = [f for f in reversed(list_feedback()) if f["status"] == "pending"]
    out = {"applied": [], "declined": [], "proposed": []}
    plain = [f for f in pending if f.get("reaction")]
    if plain:
        _mark({f["id"] for f in plain}, "noted", "Saved. Reactions shape later suggestions.")
    todo = [f for f in pending if not f.get("reaction")]
    if not todo:
        return out
    data = _json_from(runner(build_prompt(todo)) or "")
    if not isinstance(data, dict):
        return out                                    # interpreter gave nothing usable: stays pending
    handled = set()
    for ch in data.get("changes") or []:
        ids = [i for i in ch.get("feedback_ids") or [] if i]
        try:
            if ch.get("kind") == "code":
                item = _queue_proposal(ch)
                out["proposed"].append(item)
                _mark(ids, "proposed", "Queued for review: %s" % item["title"])
            else:
                layer = apply_change(ch)
                out["applied"].append(layer)
                _mark(ids, "applied", layer["why"] or "Applied.")
        except ValueError as ex:
            out["declined"].append({"feedback_ids": ids, "why": str(ex)})
            _mark(ids, "declined", str(ex))
        handled |= set(ids)
    for d in data.get("declined") or []:
        if d.get("feedback_id") and d["feedback_id"] not in handled:
            _mark({d["feedback_id"]}, "declined", str(d.get("why", "")))
            handled.add(d["feedback_id"])
    rest = {f["id"] for f in todo} - handled
    if rest:
        _mark(rest, "noted", "Saved. No change needed.")
    return out

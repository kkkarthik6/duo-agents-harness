"""Settings. Defaults below; a version's own config.json overrides them, and ~/Duo/config.json overrides both."""
import json
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DUO_HOME = Path(os.environ.get("DUO_HOME") or Path.home() / "Duo")   # installed versions, feedback clones, versions.json
PHASES = ("frame", "scout", "research_check", "decide", "feasibility", "setup", "smoke", "deep", "verify")
MODES = ("auto", "big", "always")
TASK_TYPES = ("programming", "research", "general")

DEFAULTS = {
    "cli_adapters": {},         # explicit argv protocols for additional agent CLIs
    "skill_paths": [],          # additional shared skill directories
    "model_discovery_ttl_seconds": 60,
    "model_discovery_timeout_seconds": 15,
    "model_tiers": {},           # additive, explicit role policy for newly discovered models
    "port": 8787,
    "root": "~/Duo Workouts",
    "tmux_session": "duo",
    "max_active_runs": 4,
    "auto_trust": True,
    "decision_mode": "auto",
    "max_rounds": 100,
    "sandbox": "off",             # no automatic Duo container; native CLI sandbox controls remain
    "sandbox_cpus": 4,
    "sandbox_memory": "6g",
    "sandbox_build_timeout": 1500,
    "ping_pong_cap": 3,
    "triage": "auto",           # auto: a short single-part task is done by one agent, like a CLI alone; full: every task gets the whole pipeline
    "quick_review": False,      # a quick task gets one independent review when this is on
    "auto_completion_summary": False,  # write the completion summary only when asked
    "fast_close": True,         # approved, verified work ends without the separate summary and goal-check turns
    "auto_restart": 3,          # times Duo restarts an agent that exits before asking you
    "notifications": True,
    "start_page": "home",            # first screen when no run is working: home, new, or f:<feature>:<page>
    "stall_minutes": 5,         # notify when a working agent's screen has not changed for this long
    "active_time_limit_enabled": False,
    "active_time_limit_hours": 6.0,
    "unfinished_timeouts_enabled": False,  # confirmed work is not interrupted for elapsed time
    "turn_timeout_seconds": 1800,  # long experiments must run as recorded background jobs
    "review_timeout_seconds": 600,
    "task_ack_seconds": 45,
    "progress_nudge_seconds": 300,
    "progress_timeout_seconds": 600,
    "long_job_timeout_seconds": 21600,  # fallback only when a job has no runtime estimate
    "job_runtime_multiplier": 1.5,
    "task_recovery_limit": 3,
    "automatic_model_switching": False,  # keep the CLI default unless the user configures model routing
    "role_model_routing": True,
    "role_models": {},
    "summary_agent_enabled": False,   # per-task summary pages cost a model call each; turn on to get them
    "summary_models": {"claude": "claude-haiku-5-5", "codex": "gpt-6.1-terra"},
    "completion_summary_provider": "auto",
    "completion_summary_models": {"claude":"claude-sonnet-5-5", "codex":"gpt-6.1-sol"},
    "chat_models": {"claude":"claude-sonnet-5-5", "codex":"gpt-6.1-sol"},
    "completion_summary_timeout_seconds": 900,
    "boot_seconds": 6,
    "submit_delay": 0.6,
    "agents": {},   # per-agent command overrides: {"codex": {"auto": "..."}}
}


def _merge(base, extra):
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def shared_path():
    """~/Duo/config.json: the settings every version reads, so a switch keeps them."""
    return DUO_HOME / "config.json"


def _read(p):
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def load():
    """Defaults, then the version's own config.json, then the shared ~/Duo/config.json (it wins)."""
    cfg = _merge(_merge(DEFAULTS, _read(APP_DIR / "config.json")), _read(shared_path()))
    cfg["root"] = str(Path(os.path.expanduser(cfg["root"])))
    return cfg


CFG = load()


def save_global(changes):
    """Write settings to the shared ~/Duo/config.json and apply them to this running engine.
    The first save also keeps what the version's own file had, so nothing is lost in the move."""
    if 'root' in changes:
        from . import session_index
        session_index.remember(root(), changes['root'])
    p = shared_path()
    data = _read(p) if p.exists() else _read(APP_DIR / "config.json")
    data.update(changes)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, p)
    CFG.update(changes)
    return dict(CFG)


def root():
    return Path(os.path.expanduser(CFG["root"]))


def code_id():
    """Fingerprint of the code on disk, so `duo` can tell when a running engine is out of date."""
    import hashlib
    h = hashlib.sha1()
    files = sorted(list((APP_DIR / "engine").glob("*.py")) + [APP_DIR / "engine" / "PROTOCOL.md", APP_DIR / "web" / "index.html"]
                   + list((APP_DIR / "rubrics").glob("*.json")))
    for f in files:
        if f.exists():
            st = f.stat()
            h.update(("%s:%d:%d;" % (f.name, st.st_size, int(st.st_mtime))).encode())
    return h.hexdigest()[:12]

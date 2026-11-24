"""Allow-list for settings changed from the UI. Anything not listed here is refused."""
import os
import math
from pathlib import Path

GLOBAL = {
    "model_discovery_ttl_seconds": ("int", 1, 3600),
    "model_discovery_timeout_seconds": ("int", 1, 60),
    "max_active_runs": ("int", 1, 4),
    "fast_close": ("bool",),
    "triage": ("enum", ("auto", "full")),
    "quick_review": ("bool",),
    "auto_completion_summary": ("bool",),
    "decision_mode": ("enum", ("auto","big","always")),
    "completion_summary_provider": ("enum", ("auto", "claude", "codex")),
    "active_time_limit_enabled": ("bool",),
    "active_time_limit_hours": ("number", 0.01, 8760),
    "sandbox": ("enum", ("auto", "on", "off")),
    "stall_minutes": ("int", 1, 60),
    "notifications": ("bool",),
    "root": ("dir",),                            # where projects are saved
    "start_page": ("start",),                    # first screen: home, new, or a feature page f:<feature>:<id>
    "job_runtime_multiplier": ("number", 1, 10),
    "long_job_timeout_seconds": ("int", 60, 2592000),
    "unfinished_timeouts_enabled": ("bool",),
    "turn_timeout_seconds": ("int", 60, 86400),
    "review_timeout_seconds": ("int", 60, 86400),
    "progress_nudge_seconds": ("int", 30, 86400),
    "progress_timeout_seconds": ("int", 60, 172800),
    "task_recovery_limit": ("int", 1, 20),
}


def _check_dir(value):
    from . import config
    if not isinstance(value, str) or not value.strip():
        raise ValueError("root must be a folder path.")
    path = Path(os.path.expanduser(value.strip()))
    if not path.is_absolute():
        raise ValueError("root must be a full path, like ~/Duo Workouts.")
    resolved = path.resolve()
    if resolved == config.APP_DIR or config.APP_DIR in resolved.parents:
        raise ValueError("root cannot be inside Duo's own folder.")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError("root cannot be used: %s." % (e.strerror or e))
    if not os.access(path, os.W_OK):
        raise ValueError("root is not writable.")
    return str(path)


def _check_value(key, value, rule):
    if rule[0] == "dir":
        return _check_dir(value)
    if rule[0] == "start":
        from .components import start_page_ok
        if not isinstance(value, str) or not start_page_ok(value):
            raise ValueError("start_page must be home, new, or a feature page like f:history:sessions.")
        return value
    if rule[0] == "bool":
        ok, what = isinstance(value, bool), "true or false"
    elif rule[0] == "enum":
        ok, what = value in rule[1], "one of " + ", ".join(rule[1])
    elif rule[0] == "number":
        ok = type(value) in (int, float) and math.isfinite(value) and rule[1] <= value <= rule[2]
        what = "a number from %g to %g" % (rule[1], rule[2])
    else:
        lo, hi = rule[1], rule[2]
        ok = isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi
        what = "a whole number from %d to %d" % (lo, hi)
    if not ok:
        raise ValueError("%s must be %s." % (key, what))
    return value


def check_global(patch):
    out = {}
    for key, value in patch.items():
        if key not in GLOBAL:
            raise ValueError("%s cannot be changed here." % key)
        out[key] = _check_value(key, value, GLOBAL[key])
    if {"progress_nudge_seconds", "progress_timeout_seconds"} & set(out):
        from . import config
        effective = dict(config.CFG, **out)
        if effective["progress_nudge_seconds"] >= effective["progress_timeout_seconds"]:
            raise ValueError("The progress checkpoint must occur before stall recovery")
    return out


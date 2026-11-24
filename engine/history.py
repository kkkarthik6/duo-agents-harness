"""Typical turn durations per phase, kept per task type to estimate time left. One JSON line per finished run."""
import json
import statistics
from pathlib import Path


def record_durations(path, task_type, per_phase):
    """Median turn seconds per phase for one finished run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps({"kind": "durations", "task_type": task_type, "per_phase": per_phase}) + "\n")


def medians(path, task_type):
    """{phase: median of the recorded per-run medians} for one task type."""
    by_phase = {}
    for r in _lines(path):
        if r.get("kind") == "durations" and r.get("task_type") == task_type:
            for phase, secs in (r.get("per_phase") or {}).items():
                by_phase.setdefault(phase, []).append(secs)
    return {p: statistics.median(v) for p, v in by_phase.items()}


def _lines(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out

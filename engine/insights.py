"""Failure-mode flags (a MAST subset) and session stats for the insights panel."""
import re
import statistics

from .config import PHASES

TEXT = {
    "premature_termination": "Approved on the first review without listing any checks",
    "no_verification": "Finished without a verify step",
    "derailment": "Tried to hand off work outside the ask",
    "ping_pong": "The same item went back and forth too often",
}
AGENT_PHASES = [p for p in PHASES if p != "decide"]


def _flag(kind, n):
    return {"kind": kind, "entry_n": n, "text": TEXT[kind]}


def _is_agent(e):
    return e["author"] not in ("human", "system")


def flags(entries, integrated_review=False):
    out, critiques = [], set()
    has_verify = any(e["phase"] == "verify" for e in entries) or integrated_review
    for e in entries:
        n = e["n"]
        if e.get("kind") == "critique":
            first = not critiques
            critiques.add(n)
            listed = re.search(r"^\s*([-*+]|\d+[.)])\s+", e.get("prose", ""), re.M)
            if first and e.get("verdict") == "APPROVED" and not listed and not integrated_review:
                out.append(_flag("premature_termination", n))
        if e.get("kind") == "done" and not has_verify:
            out.append(_flag("no_verification", n))
        if "handoff outside the ask" in e.get("bounds", []):
            out.append(_flag("derailment", n))
        if "ping-pong limit reached" in e.get("bounds", []):
            out.append(_flag("ping_pong", n))
    return out


def _budget(state):
    """Expected agent turns per phase in the role workflow."""
    return {"frame": 2, "scout": 2, "research_check": 1, "feasibility": 1, "setup": 3, "smoke": 0,
            "deep": state.get("max_rounds", 4) * 2, "verify": 2}


def stats(entries, state):
    cycle = state.get("cycle", 1)
    agent_entries = [e for e in entries if _is_agent(e) and e.get("cycle", 1) == cycle]
    n_agents = len(state.get("agents") or {e["author"] for e in agent_entries}) or 1
    budget = _budget(state)
    per = {}
    for p in AGENT_PHASES:
        es = [e for e in agent_entries if e["phase"] == p]
        if es:
            per[p] = {"turns": len(es), "seconds": int(sum(e.get("duration") or 0 for e in es)), "budget": budget[p]}
    return {"turns": len(agent_entries), "per_phase": per}


def progress(state, history_medians=None):
    """How far the run is (0..1) and a rough time left in seconds, [low, high] or None."""
    history_medians = history_medians or {}
    cycle = state.get("cycle", 1)
    entries = state.get("entries") or []
    agent_entries = [e for e in entries if _is_agent(e) and e.get("cycle", 1) == cycle]
    budget = _budget(state)
    if state.get("status") == "done":
        return {"done": 1.0, "phase": None, "phase_done": 1.0, "eta_s": None}
    requested = state.get('stage_requests', {})
    phases = ['frame'] + (['scout', 'research_check'] if 'research' in requested else []) + (
        ['feasibility'] if 'feasibility' in requested else []) + (['setup'] if state.get('sandbox_on') else []) + ['deep'] + (
        ['verify'] if 'verify' in requested or 'security' in requested else [])
    weight = {p: budget.get(p, 0) for p in phases}
    total = sum(weight.values())
    cur = state.get("phase") if state.get("phase") in phases else None
    idx = phases.index(cur) if cur else 0
    used = {p: sum(1 for e in agent_entries if e["phase"] == p) for p in phases}
    phase_done = min(1.0, used[cur] / budget[cur]) if cur and budget.get(cur) else 0.0
    done = (sum(weight[p] for p in phases[:idx]) + phase_done * (weight[cur] if cur else 0)) / total if total else 0.0

    med = {}
    for p in phases:
        ds = [e["duration"] for e in agent_entries if e["phase"] == p and e.get("duration")]
        if ds:
            med[p] = statistics.median(ds)
        elif p in history_medians:
            med[p] = history_medians[p]
    left = []
    for i, p in enumerate(phases[idx:]):
        n = budget.get(p, 0)
        if i == 0 and cur:
            n = max(0, n - used[p])
        left.append((p, n))
    if not any(p in med for p, _ in left):
        eta = None
    else:
        secs = sum(n * med[p] for p, n in left if p in med)
        eta = [round(0.7 * secs), round(1.5 * secs)]
    return {"done": done, "phase": cur, "phase_done": phase_done, "eta_s": eta}

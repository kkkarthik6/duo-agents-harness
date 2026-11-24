"""Estimated token use and wall time per turn, so the run page shows where the cost goes.

Duo cannot read exact usage from the CLIs, so tokens count the characters Duo sends and receives (about 4 per
token); files an agent reads on its own are not counted, which makes them floors. Seconds are real wall time from
sending a turn to receiving its reply."""
import time

CHARS_PER_TOKEN = 4


def tokens(chars):
    return int(round((chars or 0) / CHARS_PER_TOKEN))


def record_issue(state, pending, model=None, now=None):
    now = time.time() if now is None else now
    rows = state.setdefault("tokens", [])
    row = next((r for r in rows if r["n"] == pending["n"]), None)
    if row is None:
        row = {"n": pending["n"], "kind": pending["kind"], "role": (pending.get("role_assignment") or {}).get("role"),
               "agent": pending["agent"], "model": model, "prompt_chars": 0, "reply_chars": 0, "attempts": 0,
               "seconds": None, "started_at": now}
        rows.append(row)
    row["attempts"] += 1
    row["prompt_chars"] += len(pending.get("prompt", ""))
    row["model"] = model or row["model"]
    return row


def record_reply(state, n, reply_chars, seconds=None, now=None):
    """`seconds` is wall time from the first send of the turn, including any failed attempt that was retried."""
    now = time.time() if now is None else now
    row = next((r for r in state.get("tokens", []) if r["n"] == n), None)
    if row:
        row["reply_chars"] = reply_chars
        row["seconds"] = round(now - row["started_at"], 1) if row.get("started_at") is not None else seconds
    return row


def summary(state):
    """Totals, wall time, and the costliest turns."""
    rows = state.get("tokens", [])
    by_kind = {}
    for r in rows:
        k = by_kind.setdefault(r["kind"], {"turns": 0, "in": 0, "out": 0, "seconds": 0})
        k["turns"] += 1
        k["in"] += tokens(r["prompt_chars"])
        k["out"] += tokens(r["reply_chars"])
        k["seconds"] += int(r.get("seconds") or 0)
    started = [r["started_at"] for r in rows if r.get("started_at") is not None]
    ended = [r["started_at"] + r["seconds"] for r in rows if r.get("started_at") is not None and r.get("seconds")]
    wall = int(max(ended) - min(started)) if started and ended else 0
    top = sorted(rows, key=lambda r: -(r.get("seconds") or 0))[:3]
    return {"turns": len(rows), "in": sum(k["in"] for k in by_kind.values()), "out": sum(k["out"] for k in by_kind.values()),
            "seconds": wall, "by_kind": by_kind,
            "top": [{"n": r["n"], "kind": r["kind"], "role": r["role"], "model": r["model"], "seconds": int(r.get("seconds") or 0),
                     "in": tokens(r["prompt_chars"]), "out": tokens(r["reply_chars"]), "attempts": r["attempts"]} for r in top]}

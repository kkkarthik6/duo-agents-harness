"""Measure observed active work, without counting pauses or broker downtime."""
import time


def working(state):
    if state.get("status") != "running" or state.get("paused") or state.get("gate"):
        return False
    pending = state.get("pending", [])
    if isinstance(pending, dict):
        pending = pending.values()
    return any(not item.get("waiting") and not item.get("interrupted") for item in pending)


class Counter:
    def __init__(self, state, clock=None):
        self.clock = clock or time.monotonic
        self.last = self.clock()
        self.persisted = self.last
        self.active = working(state)
        state.setdefault("active_runtime_seconds", 0.0)
        state.setdefault("runtime_accounting", "observed-active-v1")

    def observe(self, state):
        now = self.clock()
        if self.active:
            state["active_runtime_seconds"] += max(0.0, now - self.last)
        self.last, self.active = now, working(state)

    def due(self):
        return self.last - self.persisted >= 30

    def saved(self):
        self.persisted = self.last

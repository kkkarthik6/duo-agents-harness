"""Phase engine for the role workflow. Pure: mutates and reads a state dict.

The lead writes a plan, the challenger reviews it, the builder builds, and the critic
reviews the result. Agents choose any additional assessment. Each run has a fixed turn
budget (max_rounds). When the budget runs out, the run stops and asks the user.

next_turns(state) is called when no turns are pending. It returns the next turns to
issue, a "gate:<name>" request, "pause", "stalled" when the run must stop and ask the
user, or "done".
"""
from dataclasses import dataclass, field
from . import relay, repeat_defect


@dataclass
class Turn:
    agent: str
    phase: str
    kind: str
    option_ids: list = field(default_factory=list)


STALL_LIMIT = 2      # this many build/revise turns in a row that change no files stop the run
QUICK_ROUNDS = 2     # a quick task still unfinished after this many build rounds becomes a full run


def _escalate(state):
    """The one-agent attempt did not finish, so the task was bigger than it looked: plan it, review the plan, and
    finish it with independent review. Work already done stays on disk and the planner is told about it."""
    state.update(size="standard", escalated=True, quick=False, phase=None, plan_verdict=None, role_plan_step=None,
                 accepted_plan_signature=None, chosen=None, round=0, stall=0, last_changed=None,
                 size_reason="escalated: the quick attempt did not finish")
    return []


def _deep(state, kind):
    if kind in ("build", "revise"):
        # A stalled revision is a loop, not progress: stop before paying for another turn.
        if state.get("last_changed") == 0:
            state["stall"] = state.get("stall", 0) + 1
        elif state.get("last_changed"):
            state["stall"] = 0
        if state.get("stall", 0) >= STALL_LIMIT:
            return _escalate(state) if state.get("size") == "trivial" else "stalled"
        state["smoke_ok"] = None
        state["smoke_evidence"] = None
    state["phase"], state["deep_step"] = "deep", ("critique" if kind == "critique" else "build")
    role = "critic" if kind == "critique" else "builder"
    return [Turn(state["roles"][role], "deep", kind, [state["chosen"]])]


def _start_build(state):
    """The sandbox needs its prerequisites planned and built before any code is written."""
    if state.get("task_type") == "research":
        # A paper does not need an invented application or Docker smoke gate.
        state["sandbox_on"] = False
    if state.get("sandbox_on") and not state.get("setup_done"):
        state.update(phase="setup", setup_step="wrote", setup_tries=0)
        return [Turn(state["roles"]["builder"], "setup", "setup", [state["chosen"]])]
    return _deep(state, "build")


def next_turns(state):
    phase, roles, agents = state["phase"], state["roles"], state["agents"]

    if state.get("size") == "trivial" and phase in (None, "frame"):
        state["phase"] = "decide"                   # a quick task is not planned or plan-reviewed
        return []

    if phase in (None, "frame"):
        state["phase"] = "frame"
        if phase is None or state.get("role_plan_step") == "revise":
            state["role_plan_step"] = "draft"
            return [Turn(roles["lead"], "frame", "exchange")]
        if state.get("role_plan_step") == "draft":
            state["role_plan_step"] = "review"
            return [Turn(roles["challenger"], "frame", "plan_review")]
        if state.get("plan_verdict") != "APPROVED":
            state["plan_review_rounds"] = state.get("plan_review_rounds", 0) + 1
            if state["plan_review_rounds"] >= state["max_rounds"]:
                return "stalled"
            state["role_plan_step"] = "draft"
            return [Turn(roles["lead"], "frame", "exchange")]
        state["phase"] = "decide"
        return []

    if phase == "decide":
        state["round"] = 1
        return _start_build(state)
    if phase == "setup":
        g = state.pop("gate_result", None)
        if g is None:
            return "gate:image"                       # a turn just finished: build the image
        if not g["ok"]:
            state["setup_tries"] = state.get("setup_tries", 0) + 1
            state["gate_log"] = g["log"]
            if state["setup_tries"] > 3:
                return "pause"
            return [Turn(roles["builder"], "setup", "setup_fix", [state["chosen"]])]
        if state.get("setup_step") == "wrote" and len(agents) > 1:
            state["setup_step"] = "checked"
            other = roles["critic"] if roles["critic"] != roles["builder"] else next(a for a in agents if a != roles["builder"])
            return [Turn(other, "setup", "setup_check", [state["chosen"]])]
        state["setup_done"] = True
        return _deep(state, "build")
    if phase == "smoke":
        g = state.pop("gate_result", None)
        if g is None:
            return "gate:smoke"
        if g["ok"]:
            state["smoke_ok"] = True
            if relay.required(state, 'verify'):
                state['phase'] = 'verify'
                return [Turn(roles['verifier'], 'verify', 'verify', [state['chosen']])]
            if relay.required(state, 'security'):
                state.update(phase='verify', deep_step='secure', verify_passed=True)
                return [Turn(roles.get('security', roles['critic']), 'verify', 'secure', [state['chosen']])]
            state.update(phase='done', verify_passed=True)
            return 'done' 
        state["smoke_ok"], state["smoke_log"] = None, g["log"]
        if state["round"] >= state["max_rounds"]:
            return "stalled"
        state["round"] += 1
        state["last_verdict"] = None
        return _deep(state, "revise")
    if phase == "deep":
        if state.get("deep_step") == "build":
            if state.pop("ready_for_review", True) is False:
                if state["round"] >= state["max_rounds"]:
                    return "stalled"
                if state.get("size") == "trivial" and state["round"] >= QUICK_ROUNDS:
                    return _escalate(state)
                state["round"] += 1
                return _deep(state, "revise")
            if state.get("size") == "trivial" and not state.get("quick_review"):
                state.update(phase="done", verify_passed=True, quick=True)   # one agent built and checked it
                return "done"
            return _deep(state, "critique")
        state["review_exchanges"] = state.get("review_exchanges", 0) + 1
        if state.get("last_verdict") == "APPROVED":
            if not relay.required(state, 'verify') and not relay.required(state, 'security'):
                state['verify_passed'] = True
                state['phase'] = 'done'
                return 'done'
            if relay.required(state, 'security') and not relay.required(state, 'verify'):
                state.update(phase='verify', deep_step='secure', verify_passed=True)
                return [Turn(roles.get('security', roles['critic']), 'verify', 'secure', [state['chosen']])]
            if state.get("sandbox_on") and not state.get("smoke_ok"):
                state["phase"] = "smoke"
                return "gate:smoke"
            state["phase"] = "verify"
            return [Turn(roles["verifier"], "verify", "verify", [state["chosen"]])]
        if state["round"] >= state["max_rounds"]:
            return "stalled"
        if state.get("repeat_defect") or repeat_defect.looping(state.get("review_prints", [])):
            state["repeat_defect"] = True
            return "stalled"
        state["round"] += 1
        return _deep(state, "revise")
    if phase == "verify":
        if state.get("deep_step") == "secure":
            if state.get("secure_ok"):
                state["phase"] = "done"
                return "done"
            if state["round"] >= state["max_rounds"]:
                return "stalled"
            state["round"] += 1
            state["last_verdict"] = None
            state["verify_passed"] = None
            return _deep(state, "revise")
        if state.get("verify_passed"):
            if state.get("code_changed") and relay.required(state, 'security'):
                state["deep_step"] = "secure"
                who = state["roles"].get("security") or state["roles"]["verifier"]
                return [Turn(who, "verify", "secure", [state["chosen"]])]
            state["phase"] = "done"
            return "done"
        state["verify_failures"] = state.get("verify_failures", 0) + 1
        if state["round"] >= state["max_rounds"]:
            return "stalled"
        state["round"] += 1
        state["last_verdict"] = None
        return _deep(state, "revise")
    return "done"

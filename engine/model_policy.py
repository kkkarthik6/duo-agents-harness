"""Role-specific model routing. Model identities are exposed only in human UI views."""
from . import config, roleflow


def native_launches(state):
    """Every run routes models by role through the native CLIs."""
    return True


def active(state):
    return bool(config.CFG.get("role_model_routing", True) and state.get("role_model_routing", True))


def ensure(state, providers=None):
    return roleflow.ensure(state)


def select(state, agent, kind, task_id, providers=None):
    return roleflow.ensure(state)[roleflow.role_for(kind)]['model']


def escalate(state, task_id, kind, reason):
    return False  # Recovery selects an eligible replacement without changing the role's tier.


def human_view(state, providers=None):
    try:
        profile = ensure(state, providers)
    except ValueError:
        # An unavailable account must not make the saved run page unreadable.
        profile = state.get('_role_models', {})
    out = []
    for role, spec in profile.items():
        assignment = state.get("_model_assignments", {}).get(role, {})
        out.append(dict(role=role, provider=spec["provider"], model=spec["model"] or "CLI default", size="large" if assignment.get("escalated") else spec["size"],
                        fallback=spec.get("fallback"),
                        selected_model=assignment.get("model") or spec["model"] or "CLI default",
                        selected_provider=assignment.get("provider") or spec["provider"],
                        escalated=assignment.get("escalated", False), mode="agentic"))
    return out

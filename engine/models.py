"""Per-stage model choice. Pure functions, no I/O.

A model is chosen by agent, stage (turn kind) and task type. The running agent is switched
with its own /model command, only when its current model differs. Config can override any
entry under "models" in config.json, as {agent: {stage: model}} or {"programming": {...}}.

Native role routing is defined in model_policy.py. This table supports explicit legacy overrides.
"""

STAGE_MODELS = {
    "claude": {"frame": "claude-opus-5-5", "diverge": "claude-opus-5-5", "score": "claude-sonnet-5-5",
               "build": "claude-opus-5-5", "revise": "claude-opus-5-5", "critique": "claude-sonnet-5-5",
               "verify": "claude-haiku-5-5", "secure": "claude-haiku-5-5"},
    "codex": {"frame": "gpt-6-astra", "score": "gpt-6-luna", "build": "gpt-6.1-sol",
              "revise": "gpt-6.1-sol", "critique": "gpt-6.1-terra", "verify": "gpt-6.1-terra",
              "secure": "gpt-6-luna"},
}
TASK_OVERRIDES = {}

SWITCH = {"claude": "/model {m}", "codex": "/model {m}"}


def model_for(agent, kind, task_type=None, overrides=None):
    """The model this agent should use for this turn, or None when Duo leaves it alone."""
    overrides = overrides or {}
    if task_type and kind in overrides.get(task_type, {}).get(agent, {}):
        return overrides[task_type][agent][kind]
    if kind in overrides.get(agent, {}):
        return overrides[agent][kind]
    if task_type and kind in TASK_OVERRIDES.get(task_type, {}).get(agent, {}):
        return TASK_OVERRIDES[task_type][agent][kind]
    return STAGE_MODELS.get(agent, {}).get(kind)


def switch_command(agent, model):
    """Text to type into the agent's pane to change its model, or None if the agent has none."""
    from . import cli_adapters
    try:
        cli_adapters.get(agent)
        template = "/model {m}"
    except ValueError:
        template = SWITCH.get(agent)
    return template.format(m=model) if template else None

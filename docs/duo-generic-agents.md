# Generic CLI agents

New runs use `workflow: "roles"`. Saved runs retain their recorded workflow.

| Role | Required model tier | Output |
|---|---|---|
| Planner | large | Source-informed plan, selected skills, outputs, and observable checks |
| Plan reviewer | large | Independent approval or specific required corrections |
| Coder | small | Implementation, actual check results, and remaining work |
| Code reviewer | medium | Independent requirement and evidence review |

An agent is a model, role instructions, tools, and a fresh session. Model identity does not establish authorship.
One CLI can fill all four roles in separate sessions. Each reviewer's tier is at least that of the agent it reviews, so a reviewer is never weaker than the author.
When available, Duo prefers another provider for reviews. Provider diversity is optional.

The role rubrics are editable in [agent-roles.json](../rubrics/agent-roles.json).
The planner receives a local skill inventory. It must read selected skills and record paths and reasons in the plan.
Discovery includes project skill folders, `~/.agents/skills`, `~/Duo/skills`, adapter skill folders, and configured shared folders.
Discovery does not install skills or run skill scripts.

## CLI adapters

Built-in adapters support Codex, Claude, and Antigravity (`agy`).
The [Antigravity catalog notes](antigravity-model-catalog.md) list researched models and account-access limits.
Other detected CLIs remain listed without becoming launchable until an adapter is configured.
An unknown executable needs an explicit adapter. Duo does not run arbitrary executables to guess agent behavior.

Each adapter defines its executable, model flag, fresh-call arguments, output protocol, capability declarations, and skill paths.
Detection checks the executable version and required flags in CLI help. Capability results appear in the agent API and `duo doctor`.
Help checks establish flag support. Tools and fresh-session behavior remain adapter declarations.
Actual account and model access are checked when an assignment runs. Detection makes no paid model call.

Add adapters and models to `~/Duo/config.json`. This example uses placeholder model IDs.
Replace the executable, arguments, and IDs with values supported by your CLI.

```json
{
  "workflow": "roles",
  "cli_adapters": {
    "local-agent": {
      "executable": "local-agent",
      "label": "Local Agent",
      "provider": "local",
      "protocol": "jsonl",
      "args": ["run", "--fresh"],
      "model_flag": "--model",
      "version_args": ["--version"],
      "help_args": ["run", "--help"],
      "required_flags": ["--model", "--fresh"],
      "capabilities": ["tools", "fresh"],
      "skill_paths": ["~/.local-agent/skills"]
    }
  },
  "model_catalog": [
    {"cli": "local-agent", "id": "planner-model", "tier": "large", "capabilities": ["tools"], "available": true},
    {"cli": "local-agent", "id": "critic-model", "tier": "medium", "capabilities": ["tools"], "available": true},
    {"cli": "local-agent", "id": "coder-model", "tier": "small", "capabilities": ["tools"], "available": true}
  ],
  "skill_paths": ["~/Shared Skills"]
}
```

`args` are argument lists. Duo does not interpolate shell commands into them.
The generic adapter sends the assignment through standard input. The CLI must execute its own tools.
For a CLI that requires a prompt argument, set `prompt_mode` to `arg` and optionally set `prompt_flag`, such as `--prompt`.
An empty `prompt_flag` supplies the prompt as the last positional argument. Standard input is preferable for large assignments.
`text` protocol returns plain response text. It must exit with a nonzero status when execution fails.
`jsonl` protocol returns one JSON object per line:

```json
{"type":"text","text":"Saved the requested output."}
{"type":"usage","usage":{"input_tokens":100,"output_tokens":30}}
{"type":"model","model":"coder-model"}
{"type":"error","api_error_status":429,"retry_after":60,"message":"rate limit"}
```

Codex, Claude, and Antigravity adapters normalize their native event formats into the same worker operations.
Native CLI permissions remain active. Duo does not add permission bypass flags for configured CLIs.

## Model catalog and routing

The catalog records explicit tiers, tool capability, and configured availability.
Duo never estimates model capability from a name. Built-in catalog entries are configurable routing defaults.
Their availability source is configuration, with account access checked on invocation.
Configuring `model_catalog` replaces the default catalog. Include every model you want available for selection.
Set `available: false` to exclude an entry. Every role requires an available model at its exact tier.
Missing tiers stop the run with an explanation. Duo does not silently promote coding work to an expensive model.

`GET /api/models/catalog` returns live CLI choices, discovery status, and separate configured policies.
See [dynamic model discovery](dynamic-model-catalog.md) for refresh rules, new-model tier policy, and access limits.
`GET /api/models/roles` returns the role models for detected CLIs under the configured workflow.
Run state records `agent_instances`, role authors, selected models, and `agent_availability` for inspection.

## Independent review and corrections

Every role assignment starts a fresh process. It receives task requirements and relevant artifacts, without the author's live conversation.
Reviewers can inspect source and run checks in the existing project. A fresh session does not create a separate filesystem sandbox.
Plan reviews must return a verdict. Required corrections return to the large planner.
The coding stage starts only after independent plan approval. Approval records a SHA-256 fingerprint of the plan.
Plan changes invalidate that approval. Empty or missing plans cannot receive approval.
Code reviews return required corrections to the small coder. Existing output receipts still prevent stale evidence from completing work.
Missing review verdicts stop after `task_recovery_limit` attempts. Plan correction rounds use the existing run budget.

Further goal cycles still retain independent assessments and detailed summaries.
New build assignments return through planning and independent plan review before more code runs.

## Failure recovery

Failures are classified as spending limits, temporary rate limits, expired login, unavailable models, permission refusals, or process failures.
Spending and login failures exclude the provider account across its models within that run.
A model or process failure excludes that CLI/model pair. Exclusions persist with the run.

Duo selects an available replacement at the required tier and retries the interrupted stage.
It retains the logical task ID and saved work. It does not pay for another continuation review after an account failure.
When no replacement exists, permanent account failures stop the run with a clear explanation.
After restoring account access, use Resume for one explicit new attempt.

Temporary rate limits use the reported `retry_after` value, or 60 seconds when no value exists.
When another eligible model is unavailable, Duo waits until the retry time. Automatic attempts use `task_recovery_limit`.
After that budget is exhausted, the run remains stopped. Stopping a run prevents scheduled retries.
Provider availability is tracked per run. Another active run can independently discover the same account limit.

Optional summaries also respect saved account exclusions. An unchanged failed summary is not requested repeatedly.

## Validation and use

Focused contracts are in [test_generic_agents.py](../tests/test_generic_agents.py).
The desktop transport test is in [test_generic_session.py](../tests/test_generic_session.py).
It runs local fake agents through tmux and makes no paid model calls.
Validation records are in [Generic Agents](../Validation%20Logs/Generic%20Agents/).

Run `python3.13 -m unittest tests.test_generic_agents tests.test_codex_worker` for focused checks.
Run `python3.13 -m unittest tests.test_generic_session` where tmux and local process access are permitted.
The application must restart after installing an updated core to load the new code. Saved tasks do not resume automatically.

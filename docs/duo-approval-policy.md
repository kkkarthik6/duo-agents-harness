# Duo workflow approvals

Core 1.12.4 removes Duo-owned approval gates. Native Claude and Codex controls remain active.

## What continues without a user click

| Earlier gate | Current behavior |
|---|---|
| Option or high-impact decision | Agents select the proposed option; decision reasons remain visible. |
| Plan | Materialize the agent plan and continue. |
| Skill selection | Use skills selected by the task agent; no second Duo trust or command approval. |
| Reference selection | Use agent-selected references; preserve explicit source exclusions. |
| Final delivery | Deliver after the agent assessment succeeds. |
| Extension approval cards | Skip the approval card; extension stages still run. |
| Reviewed local code changes | Apply the requested change after agent review, retaining version-control rollback. |
| Reviewed local feature changes | Apply the requested change and retain the prior local version. |
| Automatic Docker setup | Off by default; agents can use Docker as a native task tool. |

Exact native tool permissions remain with the CLI. Duo observes and forwards a real native request; it does not add its own command review, denial, or automatic keystroke. Existing explicit custom provider settings remain effective.

Missing facts, missing credentials, unavailable inputs, actual native denials, review defects, and failed operations are not approvals. Duo reports them accurately rather than inventing an answer or claiming success. In particular, it does not fabricate user permission for synthetic data or a change to stated task constraints.

## Saved work and updates

Old plan, reference, skill, delivery, and extension cards continue through the same gate-free path on resume. An old option approval uses the saved agent proposal, recorded as an agent decision. Missing-access cards become an accurate missing-access report, without a Mark done requirement. Old unrelated pending feature versions are not silently applied by an installation; newly completed requested changes apply automatically.

The core reload API can reattach existing workers without restarting them. This preserves an active cognitive_core assignment during installation. No global provider sandbox setting is changed. Core installation retains its automatic compatibility checks and rollback snapshot.

## Validation

Direct local probes exercise every routine gate, saved delivery cards, agent-selected decisions in all earlier decision modes, skipped extension cards, automatic reviewed code/feature application, and retained missing-information reports. The local feature probe keeps a rollback version. Python and UI script syntax checks pass. No pytest, linter, new scientific experiment, or real pending feature application ran for these probes.

## Startup repair: core 1.12.5

The first 1.12.4 reload exposed an older startup rule that closed existing terminal workers. Core 1.12.5 removes that rule. Startup reconnects live saved workers and does not terminate their sessions. If terminals are already absent, resume uses the existing task files. This repair includes all gate removals described above.

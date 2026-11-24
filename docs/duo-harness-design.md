# Duo as a harness: guard rails and orchestration design

Status: implemented in part. Historical design notes; the engine code and README describe current behavior.

## Goal

Duo should decide every turn whether a run is progressing, blocked, answering a question, or looping. It should say which, explain why in plain words, and offer the next action. A run stops only for a named rule, and a person can always see the rule, its numbers, and the fix.

## Non-goals

- Replacing the agent CLIs (Claude Code, Codex). Duo orchestrates them; it does not reimplement them.
- Git-based workflows. The project has no git repository, so checkpoints do not use git.
- Cost accounting that the CLIs do not report. Cost limits apply only where a CLI exposes usage.

## What is wrong today (observed in run 1115-164926)

1. One counter (files changed by git status) decides progress. It misses edits to files that were already modified, so real work counted as "no change".
2. Docker being unreachable looked like a loop. The guard fired twice in a row, and the message did not name Docker.
3. Questions from the person ("why did it stop") took build turns and produced explanations, not work.
4. Approval prompts were answered 37 times in one run, including repeated identical prompts and an option labelled "No".
5. Stop messages named the rule only loosely, and the person had to infer the limit.
6. A quit removed the run's container while the run still recorded it as ready.

## Best-practice defaults (decided here, so they can be changed in one place)

| Setting | Default | Reason |
|---|---|---|
| Hard turn cap per run | 40 (existing runs keep their own value) | Matches current behaviour; raise on demand |
| Wall-clock cap per run | 6 hours, warn at 5 | Stops runs left overnight from quietly burning time |
| Cost cap per run | Off unless the CLI reports usage; then $25, warn at $20 | Cost is only enforceable where it can be measured |
| No-progress guard | 2 build turns without product changes | Check logs alone cannot reset the guard |
| Verify failures | 2 in a row | Unchanged |
| Checkpoints | Before build and revise turns | Read-only checks do not copy the project again |

Progress resets the no-progress count. Answered turns do not count toward the guard, and they do not reset it either.

## Tier 1 (P0): fix the stops

### 1.1 Typed turn outcomes
Every agent turn ends with one outcome, written in the reply's footer as a line `Outcome: progress | blocked | answered | needs_human`, with the reason code when blocked.

- `progress`: at least one progress signal (see 1.4).
- `blocked`: the turn could not do its work. Requires a reason code.
- `answered`: the turn answered a person's question and did no work.
- `needs_human`: the agent needs an approval or a decision.

A reply with no outcome line is treated as `progress` only if signals exist, otherwise as `blocked: no_outcome`. The protocol check that already rejects malformed replies enforces this.

### 1.2 Blocker reason codes
Fixed codes: `docker_unreachable`, `container_missing`, `approval_needed`, `data_missing`, `tool_missing`, `no_outcome`. Each code maps to a preset message with the fix, for example `docker_unreachable` → "Start Docker Desktop, then resume."

A blocked turn does not count toward the no-progress guard. The harness reroutes the work the agent did not block on, when one exists.

### 1.3 Environment preflight
Before each build, verify, smoke, or secure turn, the engine runs a preflight:

1. `docker info` answers (the engine runs on the Mac, so it can check). If not: pause with `docker_unreachable`.
2. The run container exists. If not and the image exists: start it again (the resume fix). If the image is missing: pause with `container_missing`.
3. The wrapper `.duo/sbx` runs `true` inside the container. If it fails: pause with `container_missing`.

Preflight results go in the ledger. A failed preflight does not spend a turn.

### 1.4 Progress signals
A turn has progress if any of these hold:

- A project file changed by content: compare size and modified time before and after, and by hash for files under 1 MB. This replaces the git status check.
- A recorded command with a saved result can support verification. A log alone does not establish build progress or reset its stall counter.
- A deliverable item (from the checklist, 3.1) moved to done.

The engine does not trust the agent's claim that something ran. It checks the saved log.

### 1.5 Stop cards
Every stop shows: the rule, the numbers, the fix, and one-click actions (resume, resume with a message, skip the blocked item, raise a budget, abort). The current stop reason text becomes this card.

## Tier 2 (P1): stop loops and route work

### 2.1 Loop detection
Flag, in addition to the counter:

- The same reply text (normalized) or the same diff hash twice in a row from one agent.
- The same approval prompt answered more than 3 times in one run.
- More than 3 consecutive `answered` turns with no build or verify turn between them.

Each flag pauses the run with the reason and offers: reroute, change the prompt, or stop.

### 2.2 Approval policy
Approvals become rules, not key presses:

- Each approval prompt is classified (dataset download, package install, network write, file delete, other). Duo never selects an option labelled "No" or "deny" on its own.
- Allowed categories are listed in the run settings, with the existing dataset allowlist as the default.
- Every approval is logged with its category, the command, and the decision.
- A repeated approval (same category, same command, more than 3 times) escalates to the person.

### 2.3 Budgets
Per run: turns, wall-clock, and cost (when measurable). Each has a warning at 80% and a stop at 100%, with the numbers on the stop card. The run settings show current use against each limit.

### 2.4 Message routing
Messages go into a queue with a visible status: `queued`, `read at turn N`, or `waits for resume`. A message is classified on arrival:

- A question → the next turn is `answer-only` and writes no files.
- An instruction → it is read at the start of the next build or revise turn.

Repeated identical messages are collapsed (already done).

## Tier 3 (P2): recovery, visibility, and finish line

### 3.1 Deliverable checklist
When the decision is made, Duo writes an acceptance checklist to `.duo/checklist.md`: each deliverable, its test or evidence, and its status. The run is done when every item is closed, not when a verdict says APPROVED. The page shows what is still open.

### 3.2 Checkpoints without git
Before each build and revise turn, Duo writes a checkpoint to `.duo/checkpoints/<turn>/`:

- A manifest of every project file: path, size, mtime, SHA-256.
- Copies of changed files, when the turn changes them.

Each checkpoint is capped (default 200 MB; data directories are excluded and listed instead). Rollback restores the copies for one turn and shows the diff first. Checkpoints are pruned to the last 20 turns.

### 3.3 Turn ledger
One row per turn: outcome, reason code, duration, files changed, commands run with exit codes, approvals, cost if known, and checkpoint id. The run page shows the ledger and the guard counters, so anyone can see why the run is or isn't moving.

### 3.4 Recovery actions
Resume after a container restart, roll back one turn, and skip a blocked checklist item are one-click actions from the stop card.

## Data and interface changes

- `state.json` gains: `outcome` per entry, `preflight` per turn, `budgets`, `approval_log`, `checklist`, `checkpoints`.
- `.duo/` gains: `logs/`, `checkpoints/`, `checklist.md`.
- The page gains: stop card actions, the ledger, budget bars, the checklist, and the message queue status.

## Testing

- Unit tests for each outcome, reason code, preflight branch, progress signal, loop flag, approval class, and budget edge.
- A simulated run: scripted agent replies that cover blocked, answered, and progress turns, with Docker reported down and then up.
- A regression test for the three observed failures: already-modified file edits, the Docker stop, and the repeated "No" approval.
- Manual check on the Mac: stop with Docker down, start Docker, resume, see the card and the ledger.

## Rollout

1. Tier 1 behind a flag `harness_v2` (default on for new runs, off for existing runs until resumed).
2. Tier 2 after Tier 1 has run cleanly on one real run.
3. Tier 3 last.

## Open risks

- The CLIs may not report usage, so cost caps may not apply to every agent.
- Checkpoint copies can grow large for data-heavy projects; the exclusion list must be kept current.
- Preflight depends on Docker on the Mac. If Duo runs elsewhere, the preflight must change.

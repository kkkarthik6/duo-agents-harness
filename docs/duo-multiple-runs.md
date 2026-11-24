# Two independent runs

Core 1.13.0 supports two active runs in separate projects. The limit applies to
the role workflow. Each run retains its own assessment,
build, review, revision, decisions, context, and completion summary.

## Use

1. Start a task as usual.
2. Select New task and choose a separate project.
3. Start the second task.
4. Select either run in the sidebar to read progress or send a message.

Pause, Stop, permissions, model restart, and settings target the displayed run.
Stop closes that run's native workers. It does not stop the other run.
Switching the displayed run does not restart its workers. Composer drafts stay
associated with their run. Opening detached history does not attach workers.
Continue remains explicit.

Defaults → Maximum active runs accepts one or two. The default is two. Lowering
the limit does not cancel existing work. A start beyond the limit returns an
explanation and leaves existing runs unchanged. There is no automatic queue.

## Isolation

New run IDs and saved run folders include a random suffix. Every run owns a
separate `duo-run-<digest>` tmux session. The session has a saved run identity tag.
The workers retain their existing native permission settings and ephemeral call
behavior. Shared authentication does not share the workers' conversation memory.

The engine retains a run registry separately from the displayed run. It ticks
every active run and records failures against that run. One run's tick failure
does not prevent another run's tick. Worker processes execute concurrently;
the broker serializes state changes with its existing lock.

Requests include `run_id`. Requests without a target are refused when multiple
runs are active. Existing single-run requests remain supported. Saved-run `id`
parameters remain supported for task questions and summary APIs. Question IDs and
checklist item IDs remain separate from the routing identity.

Ownership checks cover the session tag and each saved pane's session. A saved
pane number alone cannot reconnect a worker. Delayed deliveries check the worker
generation before sending. A worker restart cannot receive an old delivery.

## Project and job reservations

Concurrent runs cannot use the same resolved project, nested paths, symlink
aliases, or subfolders of the same Git working copy. Separate projects can still
use shared resources that Duo cannot infer from folder paths, such as a remote
database, a fixed network port, or a device. Their task requirements must describe
those shared resources; folder isolation does not isolate all external services.

Registered detached jobs retain the project and a run slot even after native
workers stop. Liveness checks compare process ID and start identity, so a reused
process ID does not reserve completed work. An unavailable job inspection or a
damaged existing job registry does not authorize a duplicate workload. History
deletion refuses runs with a current reservation.

Work started outside Duo is not automatically coordinated.

## Recovery and rollback

An engine reload starts a clean interpreter in the selected installed core. It
keeps the HTTP port and leaves workers running. On startup, the engine reconnects
matching tagged workers across remembered project roots. It does not relaunch
workers merely to display history. Legacy workers require both their old tmux
session and exact project directory to match before reconnection.

Quitting the app closes all attached run workers. Detached jobs follow the
existing job policy. Saved job records protect against duplicate work on the
next launch. The previous sealed core remains available through Duo's existing
core rollback mechanism. Finish or stop active runs before switching cores.

## Validation

`tests/test_multi_runs.py` covers admission, folder conflicts, request targeting,
late delivery, detached jobs, worker ownership, clean engine reload, and error
isolation. A private tmux server checks worker restart, targeted input, Stop,
reconnection, and Quit. Local fake agents exercise two complete workflows
concurrently. These tests do not establish provider throughput or account limits.

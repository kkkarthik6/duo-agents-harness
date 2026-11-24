# Current process and timeout policy: core 1.16.0

Confirmed live jobs have no elapsed-time cutoff by default.
The monitor checks process start identities. A reused PID cannot keep an old job alive.
Workers can register each job's logs with repeated `--log <project-relative-path>` options.
Duo records changes to those logs without using another run's logs as evidence.
A live job can continue beyond its estimated runtime, including quiet periods.
The estimate remains available for reporting. Repeated registration does not erase the original deadline metadata.

Ordinary unfinished assignments also have no elapsed-time cutoff by default.
Output checkpoint requests and quiet-screen notices remain advisory.
Build-round limits, worker failure checks, delivery checks, and missing-input handling remain active.
A process exit does not prove task completion. The worker must collect and assess the result.

The `unfinished_timeouts_enabled` setting defaults to false.
Turning it on restores elapsed turn, review, output-stall, and estimated job limits.
An unavailable process or container inspection remains explicitly unconfirmed.
Its existing bounded wait can pause the run. Duo preserves the job and does not start a duplicate.
Native tool permissions and provider limits remain separate from Duo's timers.

Registration example:

```text
<assignment registration command> --pid <PID> --log experiment-progress.log --estimate-seconds 43200 --estimate-basis 'Pilot timing scaled to the full job'
```

The records and original monitoring details below still apply, except where this current policy supersedes fixed elapsed limits.

# Task process monitoring

Core 1.7.3 checks task-related processes and applies estimated runtime budgets.
A quiet training process can run for an hour without writing files or using measurable host CPU.
Process liveness is evidence that work still exists. It is not proof of completion or correct results.

## Monitor the task

At assignment, Duo records the terminal process and its existing descendants.
During build and research, it checks for new workload descendants every 15 seconds.
It excludes provider processes, the terminal worker, and common MCP and language-server processes.
Existing processes and unrelated process trees cannot protect a task from stall recovery.
Each tracked process has a PID and start-time identity. PID reuse does not extend the old job.
Tracked processes remain associated with the task after they detach or change parents.
The registry uses locked, atomic writes and survives engine reloads.

## Register detached jobs

Each assignment includes the exact registration command and registry path.
Before returning from an assignment, register a detached job with that command:

```text
<assignment registration command> --pid <PID> --estimate-seconds 43200 --estimate-basis 'Pilot timing scaled to the full job'
```

The PID must have its working directory inside the assigned project.
Registration records the process start identity. A later reused PID is a different process.
Automatic detection covers descendants observed by the monitor.
Registration covers jobs that detach before the next sample or outlive their agent.

For a detached Docker container, add its task label when starting it:

```text
docker run -d --label duo.task_id=T1 <image> <training command>
<assignment registration command> --container <container ID>
```

The container must be running with the matching task label.
Duo checks its container ID, start identity, and running state.
A removed container ends the wait. A failed daemon connection leaves the outcome unknown.
The job registry is per task and per run. It does not use one shared T1 registry for all runs.

## Wait without duplicate work

While a related job remains alive or inspection is unavailable:

- Suppress the five-minute output checkpoint request and ten-minute output stall recovery.
- Suspend the ordinary thirty-minute agent turn deadline.
- Hold builder replies and review until the job ends.
- Keep the same task_id, owned paths, saved output, and registered job IDs.
- Do not restart a dead agent automatically while its tracked job remains active.
- Keep independent tasks available to other agents.

New registry entries invalidate a cached idle decision immediately.
Duo performs a fresh process check before recovery and before accepting a completed reply.
After a job ends, Duo gives the agent a new result-collection period.
The normal output, review, acceptance, and delivery requirements still apply.
A process exit alone does not establish success. The agent must collect its result and diagnose failures.

## Bound the wait

Use a pilot measurement, comparable run, or workload size and measured throughput to estimate the full runtime.
Record the data size, hardware, epochs or steps, and timing assumptions when known.
Do not claim measured timing when no measurement exists.
The task estimate supplies a default for its jobs. Registration can supply a job-specific estimate.
The job registration command accepts estimates.

The default formula is runtime limit = estimated runtime multiplied by 1.5.
An estimated 12-hour job receives an 18-hour limit. An estimated 48-hour job receives a 72-hour limit.
Set job_runtime_multiplier in Duo configuration to change the multiplier for new deadlines.
Deadlines start at registration or first observation and persist in the task job registry.
Repeated polling, registration, or estimate changes cannot slide an established estimated deadline.
An initial estimate can replace the unknown-runtime fallback before that fallback expires.

When no estimate exists, long_job_timeout_seconds supplies the six-hour fallback.
At its deadline, Duo pauses scheduling, sends one local notification, and preserves the job.
It does not kill the job or start a replacement. The notification identifies the task and runtime limit.
The user's Resume action grants another original runtime allowance to the same job.
Resume does not relaunch the experiment. The job keeps its identity and task_id.

Monitored waiting time receives credit against the separate six-hour run budget.
Parallel job waits receive one shared time credit, not the sum of their runtimes.
Credit stops at the fixed job deadlines. A job cannot extend the run indefinitely through repeated status checks.
Time waiting for the user after a runtime pause does not consume the resumed run budget.
An unavailable process or container inspection prevents an automatic duplicate launch within the bounded wait.
Remote scheduler jobs without a local process or registered container need a future scheduler-specific monitor.

## Validation

- A real silent host process stayed assigned while the test clock advanced past an hour.
- Real PID registration succeeded. Process exit restored result collection without a retry.
- A real labelled detached Docker job survived the same simulated clock advance.
- Stopping and removing the test container ended the wait.
- Regression checks cover unrelated processes, provider-only activity, reparenting, PID reuse, zombies, and inspection failure.
- Engine checks cover ordinary timeout suppression, premature review prevention, and pause without replacement at the job limit.

The hour-long cases use a simulated harness clock and real short-lived workloads. They do not run training for an actual hour.

## Dynamic runtime validation

- A real silent process with a 12-hour estimate stayed assigned at 13 simulated hours.
- It reached the runtime pause after 18 simulated hours. The notification was checked.
- Resume extended the same process deadline. Its PID stayed unchanged.
- Regression checks cover fixed deadlines across reloads, estimate validation, one-time notifications, and parallel wait credits.
- Plan and task files preserve the runtime estimate and its basis.
- These tests use a simulated clock. They do not run an actual 18-hour training job.

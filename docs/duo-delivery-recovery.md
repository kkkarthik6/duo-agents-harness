# Assignment delivery and recovery

Core 1.9.3 prevents a prior acknowledgment from confirming a repair request. Task IDs remain stable. Every issue or repair has its own receipt and assignment path. Saved assignments from older cores receive new receipt paths. Original receipts remain available.

## Recovery rules

| Failure | Engine action |
| --- | --- |
| Worker is busy when delivery starts | Record the delivery error. Retry when the worker is idle. |
| Old acknowledgment exists | Require the current attempt's receipt. |
| Worker is idle without a reply | Wait for the receipt interval, then resend the saved assignment. |
| Worker exits | Report the missing worker. Legacy worker restarts remain bounded. |
| Worker writes a reply before finishing its call | Wait for the worker prompt before parsing the reply. |
| Two senders target one pending turn | Permit one sender lease. Reject stale senders and superseded turns. |
| Engine restarts during a send | Discard the prior engine's sender lease. Preserve the assigned model and task ID. |
| A turn was interrupted before restart | Clear the interruption flag before resending. Renew the receipt. |
| Delivery retries are exhausted | Stop with a task ID and reason. Notify the user. |
| Sender remains active for over 90 seconds | Stop visibly. Do not start a second sender. |
| Same engine exception occurs three times | Stop and notify the user. Preserve saved output. |
| Previous output contains approval words | Recognize the completed worker prompt. Do not treat old prose as a current approval. |
| User pauses the run | Stop automatic delivery and scheduling until resume. |
| A quiet training or experiment job is alive | Keep waiting for its recorded deadline. Do not start a replacement job. |

The default receipt interval is 45 seconds. The default retry limit is three. These use the existing task acknowledgment and recovery settings. Every final stop states the required next action.

A retry resumes the same assignment and saved files. An acknowledgment confirms delivery, not successful completion. Review and verification still decide whether the output meets requirements.

## Remaining boundaries

A live process can be unhealthy. Process existence cannot prove that training is making useful progress. Recorded job deadlines remain necessary. Credentials, explicit approvals, missing data, disk failures, and unavailable providers can still stop work. These conditions must produce visible blockers rather than silent retry loops. This update does not bypass those blockers or restart live experiments.

## Checks

Regression checks cover receipt isolation, old-state migration, failed sends, duplicate senders, superseded turns, missing replies, exhausted retries, dead workers, interrupted resumes, repeated engine errors, explicit user pause, and quiet live jobs. Integration checks exercise invalid-reply repair and a complete build with local test agents.

## Scout output and format recovery (core 1.9.4)

The research Scout can write `research/source-notes.md`, `research/access-log.md`, and its assigned session Scout directory. The assignment and path checker share these exact paths. Implementation files remain outside Scout scope.

Each assignment includes two valid JSON examples with its actual task ID and phase or stage. Examples illustrate syntax only; agents must replace their content with observed evidence. Assignment files preserve code-block line breaks. Plan discussion artifacts also have examples.

Scout replies must report every assigned option exactly once, with a nonempty evidence probe. Rejected turns keep their original task ID and change baseline for resume. The known old Scout-path failure is reconstructed from saved replies and retried; it is not automatically accepted. Saved notes remain in place.


## Saved goal-report recovery (core 1.16.4)

A met criterion must cite at least one existing deliverable, source file, or check result. It can also cite existing worker replies as supporting history. Worker replies alone cannot prove acceptance. Missing files and files outside the workspace remain invalid. Progress evidence must still identify substantive product files or check results.

Known goal-report and cycle-summary validation pauses now show the validation reason and required next action. Other raw engine diagnostics remain in the diagnostic record.

Continue can recheck a saved achieved report after a goal-validation pause when no work is pending. The request, user updates, signature, and input hashes must still match. Normal reviewed-delivery checks still apply. A successful recovery records the diagnostic resolution with evidence. It does not launch another research cycle or accept changed output.

Continue resumes an attached run when it is paused or has a stop reason. Selecting an attached live run preserves its workers, including a live job in waiting status.

# Goal loop

New runs use an evidence-based goal check before completion.
A stage review can pass while the user goal remains unfinished.
The goal checker reads the results and decides whether more work is needed.

## Execution

1. Send identical task packets to two independent assessors.
2. Combine their findings into a substantial build assignment.
3. Build the output and apply required critique fixes.
4. Write a separate detailed summary of requirements, work, evidence, failures, and open questions.
5. Check the original user goal against that summary and the current evidence.
6. If work remains, record its assignment and start fresh independent assessments.
7. Repeat from assessment until the goal is achieved or useful independent work cannot continue.

Each new assessment receives the previous detailed summary and goal decision.
Assessors use separate fresh processes and cannot read peer answers.
The builder receives both assessments before the next build assignment.
A summary is required for every cycle, including cycles that repeat an unsuccessful result.

The checker uses a fresh reviewer process. It does not change product files or start experiments.
The builder receives the previous summary, the next action, and the complete goal report.
Earlier results remain available. Workers must inspect existing jobs before starting replacements.

The final detailed handoff remains available after completion.
The new goal-cycle summaries supply feedback before completion.
The final handoff also includes goal status and cycle records.

## Goal status

| Status | Meaning |
|---|---|
| summarizing | A separate worker is writing the detailed cycle summary. |
| assessing | Two fresh workers assess the assigned unfinished work independently. |
| assigning | The builder combines both assessments into the next build assignment. |
| active | Required work remains. Duo can perform useful work. |
| checking | The reviewer is checking the goal against saved evidence. |
| achieved | The reviewer found evidence for all listed requirements. |
| blocked | Required work remains, but useful independent work cannot continue. |

The run page shows goal status, cycle count, and the latest reason.
A blocked goal remains unfinished. It does not become Done.
Existing build-round limits remain. The optional active-time limit remains off by default. A budget pause does not mean success.
There is no fixed total goal-cycle limit while new evidence supports continued work within those budgets.

## Progress and evidence

The goal report maps requirements to met or unmet criteria.
Met criteria need existing files inside the project.
Continued work needs an unmet criterion, answered questions, alternatives, and a concrete next action.
A negative experiment can supply useful evidence. A negative research result can also satisfy the requested research goal.
Optional improvements do not extend an achieved goal.

Duo checks file content hashes. These hashes identify the exact contents reviewed.
Changed inputs or new user requirements invalidate the assessment.
The engine checks the evidence again before marking the run Done.
Goal reports, assignments, replies, and completion summaries cannot establish progress.
Renaming or copying an already seen result does not establish new progress.
Three consecutive cycles without new evidence block continuation.
Invalid reports receive two repair attempts. A third failure pauses the run.

The model judges the scientific meaning of evidence and requirement coverage.
Content hashes detect changes; they do not prove that a change is useful or scientifically correct.
Independent review and explicit failure questions supply that assessment.

When a new goal task changes an earlier output, its independent review must cover that change.
Duo retains the earlier receipt and records the later reviewed replacement.
An unreviewed or stale replacement cannot satisfy the earlier receipt.

## Research creativity

Research method development uses five steps before selecting a method:

1. Contextualize the gap, assumptions, and possible contribution.
2. Diverge across formal, qualitative, causal, cross-disciplinary, and contrarian lenses.
3. Combine A+D, B+E, and C+D. State the question, method, and unusual connection.
4. State necessary conditions and the first likely failure. Form a testable hypothesis.
5. Anchor the hypothesis in two or three close primary papers, a bounded gap, and reviewer objections.

A lens that does not fit needs an explanation.
Workers reuse valid analysis. A failed assumption or changed result triggers another method assessment.
Proposed gains remain hypotheses until experiments support them.
Missing literature access must remain visible. Workers cannot invent citations or claim universal novelty.

## Saved files and compatibility

Records: `.duo/<session>/goal-cycles/`.

Open `goal-cycles/full-cycle-history.md` for the complete chronological record.
Open `goal-cycles/contents.md` for links and edit times.
Each `cycle-0001` style folder contains:

- `00-goal-and-requirements.md`.
- `01-independent-assessment-A.md` and `01-independent-assessment-B.md`.
- `02-build-assignment.md`, including the original accepted plan in the first cycle.
- `03-build-and-critique.md`, with complete saved replies in turn order.
- `04-summary-input.json`, with the evidence versions used by the summary writer.
- `05-detailed-summary.md`, with no fixed word-count cap.
- `06-goal-check-input.json`, `06-goal-review.md`, and `06-goal-decision.json`.
- `07-further-work-assignment.md` when the goal remains unfinished.

The engine combines the complete records. It does not cut them to short summaries.
Input snapshots remain separate from the readable combined history.
The numbered folders preserve earlier cycle summaries and decisions.
Numbering continues across explicit follow-ups, so a new goal does not overwrite earlier records.
Cycle records cannot count as product progress evidence.
A requested product summary can satisfy its own goal criterion.

New runs use `goal_loop_version: 2`.
Version-one loops adopt the new sequence at a clean completion boundary.
An explicit desktop follow-up starts a fresh version-two goal history and preserves previous goal history.
Older saved runs without a goal-loop field keep their existing execution contract.
No saved experiment is automatically resumed during an update.
See [process monitoring](duo-job-monitoring.md) for the current timeout policy.


## Stage progress repair: core 1.16.1

Every cycle summary, goal check, reassessment, and assignment now records a saved handoff and progress event.
A finished worker turn does not remain Working while another worker handles the next stage.
For older records, the display reads saved replies to restore stage completion and progress.
Restored entries are labelled saved_stage_reply. The event history remains unchanged.
A saved reply reports the stage result. It does not prove that the user goal passed validation.

Duo excludes its own mutable task indexes, generated checklists, and UI summaries from product evidence hashes.
Allocating another stage can update those files without changing a research deliverable.
Product documents and research evidence remain bound to their contents.
Older input snapshots keep their original signatures; comparison ignores only identified Duo control files.
This prevents an index update from causing repeated summary and goal-check turns.

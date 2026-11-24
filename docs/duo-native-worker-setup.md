# Native worker setup

Core 1.12.3 repairs the cognitive_core session's permission dead end.

## Cause

Duo wrapped Claude in a non-interactive call with `acceptEdits`. Reads and edits worked, but web tools and several shell commands needed approval. No interactive approver existed. The resulting network_access stop described requests that never reached a remote host.

## Current behavior

- Automatic Claude calls use native `--permission-mode auto` and retain `--no-session-persistence`.
- Automatic Codex calls use native `--approve-for-me`, ephemeral execution, disabled prompt history, and no shared daemon. The native configuration home restores the user's configured tools.
- Native deny rules, account restrictions, and sandbox controls still apply. No bypass-permissions or full-access flag is added.
- Duo's old tool hook is passive. Native worker edits no longer pass through Duo's command catalog, post-change security denial, scope veto, or dependency/deletion approval pause.
- Known old default launch commands migrate at worker restart. Explicit custom modes and settings stay unchanged.
- Explicit inherited cache settings stay unchanged. Absent cache settings still receive task-local defaults.
- Fresh tasks no longer receive nonexistent research artifact paths from a different workflow.
- Native permission refusals remain visible and are labelled approval_needed. Actual DNS, connection, or download failures remain network_access.
- The existing Duo container setting remains unchanged. A separate approval review rejected changing its default.

## Live evidence

[Claude probe](../Validation%20Logs/Native%20Worker%20Setup/claude-native-probe.log) executed sysctl and Python, searched the web, and obtained the Toolformer page through its native web-fetch agent. It reported the indirect fetch explicitly.

[Codex probe](../Validation%20Logs/Native%20Worker%20Setup/codex-native-probe.log) executed Python and used native approval retries for hardware access and the arXiv download. Initial sandbox failures and subsequent approved successes are retained in [its result](../Validation%20Logs/Native%20Worker%20Setup/Codex/probe-result.json).

Neither probe ran pytest, lint, package installation, or scientific experiments. These observations establish these native routes in these calls; they do not promise unrestricted access to every service or operation.

## Use

Load core 1.12.3 and restart the stopped workers. Resume the same cognitive_core task to reuse its saved protocol and partial environment details. Native tools must recheck actual source access. The saved failed log is historical evidence, not a current access test.

Rollback uses `bin/duo core go-back`. The previous installed core remains intact.

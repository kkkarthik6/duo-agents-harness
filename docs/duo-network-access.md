# Native worker update: core 1.12.3

Automatic workers now use each CLI’s native automatic approval mode. Claude uses `auto`; Codex uses `--approve-for-me`. Native denial rules and sandbox controls remain active. The previous non-interactive `acceptEdits` setup could not answer approval requests. Native tool refusal is now labelled as a permission issue. No global provider settings were changed. Explicit cache environment settings are preserved. See [current setup](duo-native-worker-setup.md). The older details below remain historical where they conflict with this section.

# Downloads and native CLI permissions

Core 1.9.6 makes Codex and Claude responsible for tool permissions and URL decisions.
Duo does not inject command hooks, URL lists, secret-path rules, or automatic approval answers into these providers.
User and organization CLI settings remain in effect.

## Why downloads failed

Earlier cores applied several overlapping checks. Duo checked commands against its own catalog.
It also installed native tool hooks and restricted network hosts.
A public source or redirect could fail because its host was absent from the list.
Successful access through a direct CLI therefore did not guarantee successful access through Duo.

## Current behavior

- Codex automatic launches retain workspace-write and on-request approvals. Command networking is enabled without a Duo domain filter.
- Claude automatic launches use acceptEdits. Duo does not add sandbox settings or WebFetch domain permissions.
- Helpers, reviewers, and task workers use the same launch preparation.
- Duo shows native approval requests. It does not automatically approve or deny them, even when Duo auto_approve is enabled.
- Users can answer a request through its approval control.
- Saved runs lose the exact old Duo Claude default settings when a new worker starts. Custom settings remain unchanged.
- Saved Codex launches receive network access unless the launch explicitly sets its network option.
- Package caches, managed Python, and build scratch files stay under `.duo/runtime-cache/` in the task workspace.
- Task scope, receipts, budgets, progress checks, and final code review remain part of Duo orchestration.
- Other providers retain the fallback Duo command checks.

A CLI can still deny a request. Missing credentials, source restrictions, server failures, and organization policies can also prevent downloads.
A direct CLI invocation is not a guarantee of download success.

## Applying the change

Install core 1.9.6. Start fresh workers to remove hooks already loaded by an older process.
Existing worker processes retain their launch settings until restarted.
Do not interrupt an active download or experiment solely to update workers.

## Validation

Regression tests check launch arguments, preserved custom settings, saved launch migration, and both approval handlers.
They verify that Duo adds no URL filter or tool hook and sends no approval key for a native request.
Previous 1.9.5 live download results are historical evidence only; they do not validate this core.

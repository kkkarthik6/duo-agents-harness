# Native setup update: core 1.12.3

Codex now uses its native configuration home, from `CODEX_HOME` or `~/.codex`, instead of forcing Duo’s separate home. This restores native tools and configuration. Each call still uses `exec --ephemeral`, `--no-daemon`, and `history.persistence="none"`. Claude still uses `--no-session-persistence`. The in-memory worker context remains. Existing provider history is unchanged. Earlier separate-home instructions below describe the previous setup.

# Ephemeral Codex execution

Core 1.7.1 replaces persistent Codex terminal sessions with an ephemeral worker.
The installed Codex CLI version 0.169.0 rejects --ephemeral in interactive mode.
It accepts the flag for codex exec.

## Execution

Every builder, researcher, verifier, and fresh reviewer assignment starts a new codex exec --ephemeral call.
Helpers and one-shot reviewers use the same enforced ephemeral policy.
The launch path adds --no-daemon and history.persistence="none".
It forces Duo's separate Codex home for shell launches and uses codex_env() for subprocess calls.
Saved-thread resume, fork, remote launches, and managed worktree launches are rejected.
An unsupported ephemeral flag causes an error. There is no persistent execution fallback.
Existing provider history is not deleted or archived.

The worker keeps recent assignments and responses in process memory.
It supplies that context to the next call, together with the current assignment.
Older turns are dropped when the context exceeds the configured character limit.
Project files remain the source of truth. Restarting or compacting the worker clears its memory context.
Duo's saved assignments, task output records, and local conversation files remain available.
The worker supports model selection, usage reporting, interruption, and a ready prompt for existing tmux drivers.
Command hooks and native sandbox settings remain active.

## Validation on 2026-11-17

- Live two-assignment worker test: created hello.txt and used the first response during the second assignment.
- Compared session file paths and indexed thread IDs before and after the test.
- Duo Codex home: zero new session files and zero new indexed threads.
- Personal Codex home: zero new session files and zero new indexed threads.
- Live tmux driver test: assignment file handoff, build output, fresh review, and original worker restoration passed.
- The tmux test also produced zero new session files and zero new indexed threads in both Codex homes.
- 201 focused checks passed. The final launcher checks also passed.
- Unit checks cover forced ephemeral calls, launch overrides, memory context, bounded history, child interruption, and actual child process I/O.

The checks establish local session and thread-index behavior. They do not inspect the ChatGPT account's server-side history.
They do not promise zero provider retention or zero operational logs.
The separate Codex home alone did not disable persistence in earlier versions.

## References

- [Official CLI reference](https://learn.chatgpt.com/docs/cli/reference): ephemeral execution skips session rollout files; no-daemon avoids the shared background server.
- The installed CLI help was also checked. Its behavior controls Duo's launch implementation.

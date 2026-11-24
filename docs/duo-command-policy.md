# Current approval policy: core 1.12.4

Duo workflow approval gates are removed. Agents choose routine task decisions and extra assessment stages. Plans, skills, references, delivery, and extension cards need no approval click. Automatic Docker setup defaults off. Native CLI controls and genuine missing-information requests remain. This section supersedes older approval instructions below. See [current approval policy](duo-approval-policy.md).

# Duo command policy

## Current policy: core 1.12.3

Claude and Codex own tool, command, file, and network permission decisions. Duo adds no command catalog, tool hook vote, or post-execution file-change denial for these workers. The old guard-hook entry point returns an empty decision so old registrations cannot veto native decisions. Existing native user and organization rules remain effective.

Automatic Claude workers use native `--permission-mode auto`. Automatic Codex workers use native `--approve-for-me`. These modes retain native safety review; neither is a permissions-bypass flag. Exact saved Duo default launch commands migrate when workers restart. Explicit custom commands and settings remain unchanged. A custom manual approval configuration can still require native interaction.

Codex uses its native configuration home (`CODEX_HOME` when supplied, otherwise `~/.codex`), including configured tools and plugins. Calls remain ephemeral with prompt history disabled. Claude calls retain `--no-session-persistence`. Duo does not edit global provider settings.

The broker retains message delivery, reply parsing, output receipts, progress reporting, task budgets, and agent review. Native agents choose relevant extra assessment stages. Automatic post-change dependency/deletion pauses no longer duplicate native worker decisions. Duo's existing container setting is unchanged.

A native permission refusal is reported as `approval_needed`, not proof of a failed remote host. Tool results and actual connection errors remain visible. See [native worker setup](duo-native-worker-setup.md).

## Historical policy

The following describes the earlier core 1.7.0–1.9.5 policy. It is retained for provenance.

Core 1.7.0 enables direct Docker calls. Agents can use Docker, Docker Compose, Podman, and the project container wrapper.
Role instructions state the work and required output. Command hooks enforce command restrictions.

## Broad catalog with deny rules

The catalog contains 425 command prefixes and 134 public read hosts.
Entries cover source reading, builds, package tools, documents, research, and containers.
These entries are not blanket permission to execute with host access.
Public reads can receive automatic permission after argument checks.
Project scripts and build tools retain the native sandbox and native permission checks.
An unknown command delegates to those checks. It does not create a new Duo approval prompt by itself.

Deny rules take precedence. Agent calls cannot use Git, rm, unlink, destructive disk tools, or privilege escalation.
The checks cover common shell wrappers, absolute executable paths, and nested shell commands.
Explicit credential paths and secret variable reads are blocked.
Protected paths include agent settings, command guards, installed Duo cores, and harness control state.
File tool calls receive path checks. Patch deletion requests are blocked.
Harness-owned source snapshots and internal version-control operations remain separate from agent commands.
Public content hashes and task output hashes remain available. They are not authentication secrets.

## Direct container tools

Routine Docker inspection, project builds, and scoped container execution are permitted.
Bind mounts and build contexts must remain inside the assigned project.
Privileged containers, host namespaces, added capabilities, devices, and remote daemon selection are blocked.
Operations on an unregistered container receive native permission checks.
Compose runtime commands first render the configuration as JSON.
The guard checks mounts, build contexts, host powers, and external volume settings.
The docker-compose executable receives the same checks as docker compose.

## Enforcement

Codex and Claude launches register PreToolUse and PermissionRequest hooks.
Helper calls and fresh review processes use the same launch path.
The hooks use the supported response format for each provider.
Installed cores verify the guard files against the sealed core manifest before native agent launch.
Claude excludes Docker tools from its shell sandbox so they can reach the Docker socket.
The command hooks still inspect those calls.
Codex retains workspace-write sandboxing and on-request native approvals.
Global provider settings are unchanged.

Command parsing cannot prove arbitrary programs safe. Project programs still need the native sandbox.
Provider hook timeouts and unsupported tool types retain provider behavior.
These checks are not a replacement for operating-system isolation.
Third-party agent providers without native hook support retain the existing terminal guard.

## Source provenance

- [Codex public safe-command source, rust-v0.106.0](https://github.com/openai/codex/blob/rust-v0.106.0/codex-rs/shell-command/src/command_safety/is_safe_command.rs): pinned command names and argument checks. This is not the current complete Codex policy.
- [Claude permission documentation](https://code.claude.com/docs/en/permissions#read-only-commands): documented read commands. Its complete internal list is not public.
- [Codex hooks](https://learn.chatgpt.com/docs/hooks) and [Claude hooks](https://code.claude.com/docs/en/hooks): launch integration and decision formats.

The catalog records provenance for imported entries and Duo additions.
User-specific provider approval files were not copied. Imported Git allowances were removed to apply the requested deny rule.

## Engine cost

The engine skips unchanged state writes and chat regeneration.
Build startup reuses the existing activity snapshot.
Policy files are cached until their modification time changes.
An in-process sample of 1,000 simple policy checks took 0.066 seconds.
This sample excludes CLI startup, hook process startup, and provider time.

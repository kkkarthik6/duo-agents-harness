---
name: duo
description: Hand a coding task to Duo, which plans it with one model, reviews the plan with another, builds it, and has a third pass review the code, across the Claude, Codex and Antigravity CLIs installed on this machine. Use when the user says "run this through Duo", "use Duo", "/duo <task>", or wants an independently reviewed result for a non-trivial change.
---

# Duo

Duo is a local multi-agent broker. Small asks run on one agent; larger asks go plan, plan review, build, code review. It runs on this machine and shows progress in the browser at http://127.0.0.1:8787.

## Steps

1. **Check Duo is installed.** Run `command -v duo`. If missing, run `npx duo-agents setup` (needs Node, Python 3.13+ and tmux; it prints the exact install command for anything missing). Do not install agent CLIs for the user; Duo uses the ones already signed in.
2. **Start the engine.** Run `duo serve-bg`.
3. **Start the task in the current project folder.** Send the user's request unchanged:
   ```bash
   curl -s -X POST http://127.0.0.1:8787/api/start -H 'Content-Type: application/json' \
     -d "$(python3 -c 'import json,os,sys; print(json.dumps({"task": sys.argv[1], "workspace": os.getcwd()}))' "<the user's task>")"
   ```
   Build the JSON with a tool, never by pasting the task into a shell string.
4. **Tell the user** the run started and give http://127.0.0.1:8787 to watch it. Duo works in the background; do not wait in a loop.
5. **When asked for status or the result**, run `curl -s http://127.0.0.1:8787/api/sessions` and report the run's state. Finished runs keep their files in `<project>/.duo/<run>/` (plan, reviews, summary).

## Notes

- If `duo doctor` shows no agent CLIs, tell the user to install and sign in to Claude Code, Codex or Antigravity first.
- Duo auto-approves its agents' actions by default. Mention this if the project holds anything sensitive.
- For a one-line change, just do it yourself; Duo is for work that benefits from a second opinion.

# Install Duo

Duo makes your AI coding CLIs (Claude Code, Codex, Antigravity) plan, review and check each other's work
from one task. It runs on your computer and uses the CLIs you are already signed in to. It needs no API keys.

## What you need first

1. **At least one agent CLI, signed in.** Install Claude Code, Codex or Antigravity yourself, open it once in
   a terminal and sign in. Duo never installs these for you.
2. **Node 18 or newer.** Check with `node -v`. Get it from https://nodejs.org if it is missing.
3. **Python 3.13 or newer.** Check with `python3 --version`.
   - macOS: `brew install python@3.13`
   - Ubuntu/Linux: `sudo apt install python3.13`, or
     `curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.13`
4. **tmux 3.2 or newer.**
   - macOS: `brew install tmux`
   - Ubuntu/Linux: `sudo apt install tmux`

**Windows:** Duo runs inside WSL. In PowerShell run `wsl --install -d Ubuntu`, restart, open the **Ubuntu**
app, and do everything below inside it. Install and sign in to the agent CLIs inside Ubuntu too.

## Install

```bash
npx duo-agents
```

This installs Duo into `~/Duo`, adds the `duo` command, starts it and opens http://127.0.0.1:8787 in your
browser. If Python or tmux is missing, it prints the exact command to run; run `npx duo-agents` again after.
Open a new terminal window once so the `duo` command is on your PATH.

From a source checkout instead: `git clone https://github.com/kkkarthik6/duo && cd duo && sh install.sh`.

## Use it

```bash
duo                              # open Duo
duo "add a dark mode toggle"     # start a task
duo doctor                       # check what Duo can find
duo models                       # show the models your CLIs report
duo quit                         # stop Duo and close its agents
```

Small tasks run on one agent. Bigger ones go plan, plan review, build, code review. Watch progress in the
browser. With two or more CLIs installed, Duo has them review each other's work.

To use Duo from inside Claude Code or Codex, add the skill and say "run this through Duo":

```bash
npx skills add kkkarthik6/duo@duo
```

## Heads-up

Duo's agents run in auto-approve mode: they can edit files and run commands in your project without asking.
Use it on projects under git and keep backups. `duo quit` stops running agents, so do not run it mid-task.

## Update

```bash
npx duo-agents setup
duo core go-back        # return to the previous version if something breaks
```

## If something goes wrong

- `duo doctor` shows what Duo can and cannot find.
- A CLI shows as signed out: open it once in a normal terminal and sign in again.
- The page does not load: run `duo` again.
- Codex will not start: `duo repair-codex` shows the problem; add `--apply` to fix it.
- Windows: make sure you run every command inside the Ubuntu (WSL) window, not PowerShell.

Source and issues: https://github.com/kkkarthik6/duo

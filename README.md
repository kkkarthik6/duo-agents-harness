# Duo

Duo makes the AI coding CLIs you already have check each other's work.

Give it one task. A large model writes a plan, a second large model critiques the plan, a smaller model
builds it, and a third pass reviews the code. Each step is a fresh session, so no model grades its own
work. Small asks skip all of that and run on one agent, the way the CLI would on its own.

Duo works with **Claude Code**, **Codex** and **Google Antigravity**. It uses the CLIs you have installed
and signed in to. It never installs them and never asks for API keys.

## Install

```bash
npx duo-agents
```

You need:

| | macOS | Linux | Windows |
|---|---|---|---|
| Node 18+ | yes | yes | inside WSL |
| Python 3.13+ | `brew install python@3.13` | `sudo apt install python3.13` (or `uv python install 3.13`) | inside WSL |
| tmux | `brew install tmux` | `sudo apt install tmux` | inside WSL |
| At least one agent CLI, signed in | `claude`, `codex` or `agy` | same | inside WSL |

Windows runs Duo inside WSL (Ubuntu): `wsl --install -d Ubuntu`, open Ubuntu, then run `npx duo-agents`.
If something is missing, the installer says which command to run.

Step-by-step guide: [INSTALL.md](INSTALL.md).

The installer puts Duo in `~/Duo`, adds a `duo` command, starts the engine and opens
http://127.0.0.1:8787 in your browser. From a source checkout, run `sh install.sh` instead.

## Use

```bash
duo                      # start Duo and open it in the browser
duo "add a dark mode toggle to settings"
duo doctor               # show what Duo can find and what is missing
duo models               # show the models each CLI reports
duo quit                 # stop the engine and close all agents
```

On a Mac, `duo setup` can also build an optional desktop app (needs Xcode tools and Rust).

**From inside an agent.** Install the skill, then say "run this through Duo":

```bash
npx skills add kkkarthik6/duo@duo
```

## How a task runs

Duo first sizes the task.

- **Quick path.** A short, single-part ask (about 90 words or fewer, no words like "architecture",
  "migrate" or "refactor") goes to one agent using the CLI's default model. There is no plan or review
  step. If it is not finished after two rounds or it stalls, Duo escalates to the full pipeline on its own.
- **Full pipeline.**
  1. A large model inspects the project and writes a plan with checks you can observe.
  2. A fresh large model critiques the plan and lists every required fix in one pass.
  3. A smaller model builds the approved plan and records real check results.
  4. A fresh medium model reviews the changes. Required fixes go back to the builder.

A reviewer is never a weaker tier than the author. When you have two CLIs, Duo reviews across providers,
which is what catches an author's blind spots. With one CLI it works, and tells you reviews share a provider.

Models are matched to roles by tier (large, medium, small) from what your CLIs report. If a model has no
tier, Duo uses the nearest tier, then the CLI's own default. Failures are tracked, so a model that is out of
quota or signed out is skipped and the work moves to another one. Saved work is kept.

Open **What this run contributed** in the task map to see the latest reported result for each named outcome.
Expand **Build review history** to compare cycles and open their evidence. Quick runs show the builder's report.
When a build package needs an external input, Duo can finish independent packages within the existing run budget.
The task map shows which items must wait.

For a UI reference task, ask Duo to create a PNG mockup. Open **Recent images** on Home, or see **Images** at the top of the run’s task map.
Select an image to open it at full size. Previews support PNG, JPEG, WebP,
and GIF files up to 24 MiB each.

For a follow-up, name the saved project file, link, or product in the request. For example:
“Build me an app like this: `references/reading-list.png`. Find a comparable reading list,
explain what works and what you would change, and test adding a book.”

Each handoff is a short brief generated from the run's state: the ask, constraints, open defects, changed
files and who does what next. Older turns collapse to counts, so context stays small without losing work.

Up to four runs can work at once, in different projects. Runs in the same project wait their turn.
The Insights panel shows time and estimated tokens for each turn.

## Safety

**By default Duo's agents run with their CLIs' automatic-approval modes, so they can edit files and run
commands in the project without asking you.** Use it on projects under version control and keep backups.
Each run works in its own project folder, but Duo is not a sandbox. Native CLI safeguards stay on, and
agents ask you when they are missing information. See the [approval policy](docs/duo-approval-policy.md)
and [command policy](docs/duo-command-policy.md).

Duo runs Codex with `exec --ephemeral`, so its runs do not appear in your Codex history.
See [session privacy](docs/duo-session-privacy.md).

## Settings

Duo reads `~/Duo/config.json`. Useful keys:

```json
{"triage": "auto", "max_rounds": 100, "max_active_runs": 4, "quick_review": false, "fast_close": true}
```

- `triage`: `auto` sizes each task; `full` always runs the whole pipeline.
- `quick_review`: add one independent review to quick tasks.
- `fast_close`: end approved, verified work without extra summary turns.

The app's Defaults and Features screens expose the rest. Assign a role to a specific model with
`role_models`; see [generic agents](docs/duo-generic-agents.md) and
[model discovery](docs/dynamic-model-catalog.md).

## Updating and recovery

Duo installs as sealed, versioned cores in `~/Duo/core`.

```bash
npx duo-agents setup                       # install the newest published version
duo update --from <folder|file.tgz>        # test a new core, check your features, then switch
duo core go-back                           # return to the previous core
```

Your features and settings live outside the core and survive updates.

## Troubleshooting

- `duo doctor` lists what Duo can find: Python, tmux, each agent CLI and the engine.
- A CLI shows as signed out: open it once in a normal terminal and sign in, then rescan in Duo.
- Codex will not start after a crash: `duo repair-codex` shows the problem; add `--apply` to fix it.
- The page does not load: run `duo` again. Quitting the app or closing the terminal that started the
  engine stops it.
- tmux must be 3.2 or newer.

## Develop

```bash
python3.13 -m unittest discover -s tests -t .              # unit tests, about 25 seconds
python3.13 -m unittest discover -s tests/integration -t .  # real tmux with fake agents
```

The engine uses only the Python standard library. More detail: [multiple runs](docs/duo-multiple-runs.md),
[goal loop](docs/duo-goal-loop.md), [job monitoring](docs/duo-job-monitoring.md),
[delivery recovery](docs/duo-delivery-recovery.md), [UI extensions](docs/duo-ui-extensions.md),
[network access](docs/duo-network-access.md), [native worker setup](docs/duo-native-worker-setup.md).

## License

MIT. See [LICENSE](LICENSE).

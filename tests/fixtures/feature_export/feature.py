"""Example user feature. Copy this folder to ~/Duo/user/features/<name>/ to start a new one.

It shows every part of the contract a feature usually needs:
  - a Stage that runs after a core stage and writes into the project's duo/features/<name>/ folder
  - a route the page can call
  - a panel in the home slot, and a full page of its own with a side bar button (contract 1.2)
  - an event handler
  - a rule for agents
It imports only engine.coreapi.
"""
from engine import coreapi

DONE = []


class ExportSummary(coreapi.Stage):
    name = "export_summary"
    after = "deliver"

    def run(self, ctx):
        r = ctx.run
        ctx.write("summary.md", "# Run %s\n\n- Task: %s\n- Type: %s\n- Agents: %s\n" % (r.id, r.task, r.task_type, ", ".join(r.agents)))
        ctx.note("Wrote duo/features/feature_export/summary.md")


def list_runs(req):
    runs = req["ctx"].runs()
    return 200, {"runs": [{"id": r.id, "task": r.task, "stage": r.stage, "status": r.status, "folder": r.folder} for r in runs]}


def register(api):
    api.stage(ExportSummary)
    api.route("GET", "/runs", list_runs)
    api.slot("home", "ui/home.js")
    api.page("runs", "Runs", "ui/runs_page.js")
    api.on("run.done", lambda e: DONE.append(e["run"].id))
    api.instructions("At the end of each task, list the files you changed.")

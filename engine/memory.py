"""Run memory (memory.md): the bounded handoff brief, regenerated from saved state after every turn.

Never written by a model, so it cannot drift from what happened. Agents that rejoin read it first."""
import os
from pathlib import Path

from . import handoff, orchestration as orch


def build_memory(st, rel=".duo"):
    return handoff.brief(st, None, rel)


def write(st, duo_dir, project_dir):
    duo_dir, project_dir = Path(duo_dir), Path(project_dir)
    rel = os.path.relpath(duo_dir, project_dir)
    (duo_dir / "memory.md").write_text(orch.redact(st, build_memory(st, rel)))

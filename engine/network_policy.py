"""Workspace-local runtime files. Native CLIs own network permission decisions."""
import os
from pathlib import Path


def task_environment(workspace, inherited=None):
    """Keep downloads, package caches, build scratch files, and managed Python inside the task."""
    root = Path(workspace).resolve() / '.duo' / 'runtime-cache'
    locations = {'UV_CACHE_DIR':'uv','UV_PYTHON_INSTALL_DIR':'python','UV_TOOL_DIR':'tools',
                 'PIP_CACHE_DIR':'pip','HF_HOME':'huggingface','HF_HUB_CACHE':'huggingface/hub',
                 'XDG_CACHE_HOME':'xdg','PYTHONPYCACHEPREFIX':'pycache','TMPDIR':'tmp','TEMP':'tmp','TMP':'tmp'}
    env = dict(os.environ if inherited is None else inherited)
    for name, folder in locations.items():
        if name not in env:
            path=root/folder;path.mkdir(parents=True,exist_ok=True);env[name]=str(path)
    return env

"""Test package. Every test process gets its own tmux server, so a hung or killed run can never
leave sessions in (or kill sessions from) the user's real tmux."""
import atexit
import os
import shutil
import subprocess
import tempfile

SOCKET = "duo-test-%d" % os.getpid()
os.environ.setdefault("DUO_TMUX_SOCKET", SOCKET)
# A test must never write to the user's real files: ~/Duo (cores, settings, remembered logins), ~/.codex/config.toml
# and ~/.claude.json (folder trust). Every test process gets a throwaway home for all of them.
if "DUO_TEST_HOME" not in os.environ:
    _HOME = tempfile.mkdtemp(prefix="duo-test-home-")
    os.environ["DUO_TEST_HOME"] = _HOME
    os.environ["HOME"] = _HOME
    os.environ["DUO_HOME"] = os.path.join(_HOME, "Duo")
    os.environ["CODEX_HOME"] = os.path.join(_HOME, ".codex")
    os.environ.pop("CLAUDE_CONFIG_DIR", None)
    atexit.register(shutil.rmtree, _HOME, True)


def _stop_private_server():
    socket = os.environ.get("DUO_TMUX_SOCKET", "")
    if socket.startswith("duo-test-"):
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)


atexit.register(_stop_private_server)

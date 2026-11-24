"""Thin wrappers over the tmux CLI."""
import os
import re
import subprocess
import time

from .config import CFG

_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07]*\x07")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # control chars, keeping tab and newline
_CHROME = set("─│┌┐└┘├┤╭╮╰╯═║> $ ❯ › ›│")


def command(*args):
    """The tmux argv. DUO_TMUX_SOCKET names a private server so a test run can never touch the user's sessions."""
    socket = os.environ.get("DUO_TMUX_SOCKET")
    return ["tmux", "-L", socket, *args] if socket else ["tmux", *args]


def pane_env():
    """Environment every agent pane must start with, whatever the tmux server inherited when it first started.
    A server begun by another process (a test run, an SSH session) can carry a different HOME, which makes an agent
    CLI look signed out because it cannot find its login."""
    home = os.environ.get("HOME")
    return ["-e", "HOME=" + home] if home else []


def tmux(*args, check=True):
    r = subprocess.run(command(*args), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("tmux %s failed: %s" % (" ".join(args[:2]), r.stderr.strip()))
    return r.stdout


def split(target, cwd, cmd):
    """Start a worker beside `target` and return its pane id. A window that has run out of room gets a new window
    in the same session instead of failing the run."""
    base = ["-t", target, "-c", str(cwd), "-P", "-F", "#{pane_id}"]
    for flag in ("-h", "-v"):
        try:
            return tmux("split-window", *pane_env(), flag, *base, cmd).strip()
        except RuntimeError as error:
            if "no space" not in str(error):
                raise
    return tmux("new-window", *pane_env(), "-d", *base, cmd).strip()


def capture(pane, lines=0):
    """lines=0 captures only what is on screen now (no scrollback)."""
    args = ["capture-pane", "-p", "-J", "-t", pane] + (["-S", "-%d" % lines] if lines else [])
    return tmux(*args, check=False)


def tail_lines(screen, n=3):
    """The last n lines of a pane that carry text, with escape codes and box/prompt chrome removed."""
    text = _CTRL.sub("", _OSC.sub("", _CSI.sub("", screen)))
    lines = [" ".join(l.split())[:160] for l in text.split("\n")
             if any(c not in _CHROME and not c.isspace() for c in l)]
    return lines[-n:] if n else []


def pane_dead(pane):
    return tmux("display-message", "-p", "-t", pane, "#{pane_dead}", check=False).strip() != "0"


def send_text(pane, text, enter=True):
    if text:
        tmux("send-keys", "-t", pane, "-l", text)
    if enter:
        time.sleep(CFG["submit_delay"])
        tmux("send-keys", "-t", pane, "Enter")


def send_key(pane, key):
    tmux("send-keys", "-t", pane, key)

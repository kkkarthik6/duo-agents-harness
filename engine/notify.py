"""Desktop notifications (macOS osascript, Linux notify-send). Every notification is also kept in SENT for the UI and tests."""
import shutil
import subprocess

ENABLED = True
SENT = []          # [(title, text)]


def _q(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')[:220]


def send(title, text):
    SENT.append((title, text))
    del SENT[:-50]
    if ENABLED and not shutil.which("osascript") and shutil.which("notify-send"):
        subprocess.run(["notify-send", str(title)[:80], str(text)[:220]], capture_output=True)
    elif ENABLED and shutil.which("osascript"):
        subprocess.run(["osascript", "-e", 'display notification "%s" with title "%s" sound name "Glass"' % (_q(text), _q(title))],
                       capture_output=True)

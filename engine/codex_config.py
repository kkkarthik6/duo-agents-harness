"""Check and repair ~/.codex/config.toml.

A Codex config that does not parse makes every Codex call fail before it starts, and Duo then quietly runs on
another CLI. Two things break it: a half-written line, and thousands of [projects."..."] trust entries left by test
runs. repair() removes only those, keeps a backup, and never writes a file that does not parse."""
import os
import re
import shutil
import time
import tomllib
from pathlib import Path

# A folder made by tempfile.mkdtemp() has a name like tmpab12cd34 inside the system temp directory. Those are what test runs
# create, and no real project lives in one, so their trust entries go whether or not the folder still exists.
TEMP_PROJECT = re.compile(r'^\[projects\."((?:/private)?/(?:var/folders/[^"/]+/[^"/]+/T|tmp)/tmp[a-z0-9_]{8}(?:/[^"]*)?)"\]')


def path():
    from . import agents
    return Path(agents.CODEX_HOME) / "config.toml"


def check(cfg=None):
    """None when the file loads (or does not exist); otherwise a one-line problem."""
    cfg = Path(cfg or path())
    if not cfg.exists():
        return None
    try:
        tomllib.loads(cfg.read_text())
        return None
    except tomllib.TOMLDecodeError as error:
        return "%s does not load (%s). Codex will fail until it is repaired: run `duo repair-codex`." % (cfg, error)


def _blocks(text):
    blocks, current = [], []
    for line in text.split("\n"):
        if line.startswith("["):
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    blocks.append(current)
    return blocks


def repair(cfg=None, apply=False, backup_dir=None):
    """What would change (apply=False) or what changed. Returns a report dict."""
    cfg = Path(cfg or path())
    text = cfg.read_text()
    kept, gone = [], 0
    for block in _blocks(text):
        match = TEMP_PROJECT.match(block[0]) if block else None
        if match:
            gone += 1
            continue
        kept.append(block)
    out = re.sub(r"\n{3,}", "\n\n", "\n".join("\n".join(b) for b in kept))
    fragments = []
    for _ in range(200):
        try:
            tomllib.loads(out)
            break
        except tomllib.TOMLDecodeError as error:
            number = int(re.search(r"line (\d+)", str(error)).group(1)) - 1
            lines = out.split("\n")
            fragments.append(lines.pop(number))
            out = "\n".join(lines)
    else:
        raise ValueError("The config could not be repaired automatically.")
    data = tomllib.loads(out)
    report = {"file": str(cfg), "removed_test_folders": gone, "removed_fragments": fragments,
              "projects_kept": len(data.get("projects", {})), "bytes_before": len(text.encode()), "bytes_after": len(out.encode()),
              "applied": False, "backup": None}
    if apply:
        folder = Path(backup_dir or Path.home() / "Duo" / "backups")
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / ("codex-config-%s.toml" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(cfg, backup)
        tmp = cfg.with_name("%s.duo-%d.tmp" % (cfg.name, os.getpid()))
        tmp.write_text(out)
        os.replace(tmp, cfg)
        report.update(applied=True, backup=str(backup))
    return report

#!/usr/bin/env python3
"""Duo desktop bridge: select the sealed core without importing bundled engine code."""
import json
import os
import re
import sys
from pathlib import Path


def main():
    home = Path(os.environ.get("DUO_HOME") or Path.home() / "Duo")
    try:
        current = json.loads((home / "core/state.json").read_text())["current"]
        if not isinstance(current, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", current) or current in (".", ".."):
            raise ValueError("Invalid core selection")
        command = home / "core" / current / "bin/duo"
        if not command.is_file():
            raise ValueError("Installed core launcher is missing")
        os.execv(sys.executable, [sys.executable, str(command)] + sys.argv[1:])
    except (OSError, ValueError, KeyError) as error:
        sys.exit("Duo desktop launcher: %s. Run sh install.sh from the Duo source folder." % error)


if __name__ == "__main__":
    main()

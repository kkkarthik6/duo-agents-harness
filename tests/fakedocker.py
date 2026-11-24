#!/usr/bin/env python3
"""A stand-in for the docker CLI. Logs every call; fails on request. Used only by tests."""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log = os.environ.get("FAKE_DOCKER_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(args) + "\n")
fail = os.environ.get("FAKE_DOCKER_FAIL", "")
cmd = args[0] if args else ""
if cmd == "info":
    sys.exit(1 if fail == "info" else 0)
if cmd == "build":
    df = Path(args[args.index("-f") + 1]) if "-f" in args else Path(args[-1]) / "Dockerfile"
    if fail == "build" or (df.exists() and "BROKEN" in df.read_text()):
        print("Step 3/4 : RUN apt-get install nope\nE: Unable to locate package nope")
        sys.exit(1)
    sys.exit(0)
if cmd == "run":
    if "--rm" in args:
        if fail == "smoke":
            print("smoke: app did not start")
            sys.exit(1)
        sys.exit(0)
    sys.exit(1 if fail == "run" else 0)
if cmd == "exec":
    joined = " ".join(args)
    if fail.startswith("tool:") and fail[5:] in joined:
        print("sh: not found")
        sys.exit(127)
    sys.exit(0)
if cmd == "ps":
    print("abc123\ndef456")
    sys.exit(0)
sys.exit(0)

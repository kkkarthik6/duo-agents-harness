"""Retired Duo hook. Native CLIs decide tool permissions; this hook never votes."""
import argparse
import json
import sys


def check(event, workspace, container=None):
    return "review", "Native CLI permissions apply; Duo only routes messages"


def response(event, decision, reason, provider):
    return {}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--container")
    parser.parse_args(argv)
    # Consume the event without authorizing or denying any action.
    sys.stdin.read()
    print(json.dumps({}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

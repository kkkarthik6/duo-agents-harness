"""Pane driver: idle detection, slash commands with prompt-wait, /usage parsing. Talks to tmux only."""
import re
import time

from .tmux import capture, send_key, send_text

PROMPTS = {"claude": re.compile(r"^\s*[>›❯]\s*$|^\s*[>›❯]\s+\S*$"), "codex": re.compile(r"^\s*[›>❯]\s*$|codex>\s*$"),
           "agy": re.compile(r"^\s*[>›❯]\s*$")}
APPROVAL_HINTS = ("do you want to", "allow this", "approve this", "press enter to confirm", "esc to cancel", "(y/n)", "[y/n]",
                  "yes, proceed", "select login method", "tab to amend", "don't ask again")
CONTEXT_WARN = re.compile(r"context (left|window|limit)|compact", re.I)
PICKER = re.compile(r"(select|choose) (a )?model|↑|↓|use arrow", re.I)
USAGE_TOKENS = re.compile(r"([\d,.]+)\s*[kKmM]?\s*tokens", re.I)
USAGE_COST = re.compile(r"\$\s*([\d.]+)")
POLL = 0.5


def last_line(screen):
    lines = [l.rstrip() for l in screen.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def has_question(screen):
    if re.search(r"DUO_WORKER_RESULT:(?:success|failed)\s*\n(?:❯|codex>)\s*$", screen):
        return False
    tail = "\n".join(screen.splitlines()[-12:]).lower()
    return any(h in tail for h in APPROVAL_HINTS)


def idle(screen, agent="claude"):
    """The prompt is showing and no approval question is on screen."""
    if has_question(screen):
        return False
    pat = PROMPTS.get(agent, PROMPTS["claude"])
    lines = [l.strip() for l in screen.splitlines() if l.strip()]
    # Real CLIs show a status footer below the input prompt.
    while lines and re.search(r"accept edits on|shift\+tab|\? for shortcuts|context left|bypass permissions on", lines[-1], re.I):
        lines.pop()
    return bool(lines and pat.search(lines[-1]))


def wait_idle(pane, agent, timeout, cap=capture, sleep=time.sleep):
    end = time.time() + timeout
    screen = cap(pane)
    while time.time() < end:
        if idle(screen, agent):
            return True, screen
        sleep(POLL)
        screen = cap(pane)
    return False, screen


def command(pane, agent, text, timeout=20, cap=capture, send=send_text, key=send_key, sleep=time.sleep, picker_value=None):
    """Type one slash command when the pane is idle and wait for the prompt to return.
    Returns (ok, screen). A picker (model menu) is answered with picker_value, then Enter."""
    screen = cap(pane)
    if not idle(screen, agent):
        return False, screen
    send(pane, text)
    sleep(POLL)
    screen = cap(pane)
    if picker_value and PICKER.search("\n".join(screen.splitlines()[-15:])):
        send(pane, picker_value)
        sleep(POLL)
    ok, screen = wait_idle(pane, agent, timeout, cap, sleep)
    return ok, screen


def _num(s):
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_usage(screen):
    """{'tokens': int, 'cost': float} from a /usage or /cost panel, or None."""
    tail = "\n".join(screen.splitlines()[-30:])
    toks = [_num(m.group(1)) for m in USAGE_TOKENS.finditer(tail)]
    toks = [t for t in toks if t is not None]
    cost = [_num(m.group(1)) for m in USAGE_COST.finditer(tail)]
    cost = [c for c in cost if c is not None]
    if not toks and not cost:
        return None
    return {"tokens": int(max(toks)) if toks else None, "cost": max(cost) if cost else None}


def usage(pane, agent, cap=capture, send=send_text, key=send_key, sleep=time.sleep, timeout=10):
    cmd = "/usage" if agent == "claude" else "/status"
    screen = cap(pane)
    if not idle(screen, agent):
        return None
    send(pane, cmd)
    sleep(1.0)
    screen = cap(pane)
    data = parse_usage(screen)
    key(pane, "Escape")
    wait_idle(pane, agent, timeout, cap, sleep)
    return data


def compact_if_needed(pane, agent, turns_since, cap=capture, send=send_text, sleep=time.sleep, every=8):
    screen = cap(pane)
    if not idle(screen, agent):
        return False
    if turns_since >= every or CONTEXT_WARN.search("\n".join(screen.splitlines()[-5:])):
        return command(pane, agent, "/compact", 60, cap, send, sleep=sleep)[0]
    return False

"""Stop a revise loop that cannot end: the reviewer keeps raising the same defect, and the builder cannot fix it
(for example a bug in a file outside the project). Counts and numbers change every round, so they are ignored."""
import re

LOOP_AFTER = 3          # this many REVISE reviews in a row that say the same thing
SAME = 0.75             # word overlap that counts as "the same"


def fingerprint(text):
    words = re.findall(r"[a-z_]{3,}", re.sub(r"\d+", " ", str(text).lower()))
    return sorted(set(words))


def _same(a, b):
    a, b = set(a), set(b)
    return bool(a and b) and len(a & b) / len(a | b) >= SAME


def looping(prints):
    last = list(prints)[-LOOP_AFTER:]
    return len(last) == LOOP_AFTER and all(_same(last[0], p) for p in last[1:])

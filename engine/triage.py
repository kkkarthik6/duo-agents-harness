"""Decide from the task text alone, before any model call, whether a task is small enough for one agent.

A planner turn costs a minute or two by itself, so the choice cannot wait for one. A trivial task gets what
Claude or Codex would give it alone: one agent does the work, runs it, and reports. Anything that looks
bigger, riskier or multi-part keeps the full plan, plan review, build and code review."""
import re

MAX_WORDS = 90
MAX_LINES = 6
# Work that touches more than one thing, or where a mistake is expensive.
COMPLEX = re.compile(
    r"\b(refactor|migrat\w*|architecture|redesign|rewrite|database|schema|api|endpoint|server|backend|frontend|"
    r"deploy\w*|docker|kubernetes|ci/cd|pipeline|authenticat\w*|authoriz\w*|password|secret|credential|payment|"
    r"security|encrypt\w*|concurren\w*|distributed|microservice|framework|monorepo|multi[- ]?(?:file|module|service|step|stage)|"
    r"integrat\w*|production|research|paper|literature|benchmark|train\w*|dataset|machine learning|"
    r"existing (?:code|project|codebase|repo)|across (?:the )?(?:repo|codebase|project|files)|all (?:files|modules|tests))\b", re.I)


def classify(task):
    """'trivial' or 'standard', with the reason, from the task text."""
    text = (task or "").strip()
    words = len(text.split())
    lines = len([l for l in text.splitlines() if l.strip()])
    if not text:
        return "standard", "empty task"
    if words > MAX_WORDS:
        return "standard", "long task (%d words)" % words
    if lines > MAX_LINES:
        return "standard", "many separate requirements (%d lines)" % lines
    hit = COMPLEX.search(text)
    if hit:
        return "standard", "mentions %s" % hit.group(0).lower()
    return "trivial", "short single-part task"

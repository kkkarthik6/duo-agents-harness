"""Parse an agent reply: STE prose plus one fenced ```duo JSON block."""
import json
import re
from dataclasses import dataclass, field, asdict

from .config import PHASES, TASK_TYPES

END_MARK = "<!-- END -->"
BLOCK = re.compile(r"^```duo[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
OPTION_ID = re.compile(r"^O\d+$")


@dataclass
class Reply:
    freeform: bool = False
    prose: str = ""
    summary: str = ""
    phase: str = ""
    task_type: str = None
    options: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    handoffs: list = field(default_factory=list)
    verdict: str = None
    outcome: str = None
    blocker: str = None
    ready_for_review: bool = True
    task_id: str = None
    complete: bool = None
    outputs: list = field(default_factory=list)
    remaining: list = None
    build_evidence: dict = None
    blocker_detail: dict = None
    stage_requests: dict = field(default_factory=dict)
    security: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _valid_score(v):
    return (isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and not isinstance(v[0], bool)
            and 0 <= v[0] <= 3 and isinstance(v[1], str) and v[1].strip() != "")


def parse(text):
    text = text.replace(END_MARK, "")
    blocks = list(BLOCK.finditer(text))
    r = Reply(prose=BLOCK.sub("", text).strip())
    if not blocks:
        r.errors.append("no duo block")
        return r
    try:
        data = json.loads(blocks[-1].group(1))
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
    except ValueError as ex:
        r.errors.append("bad JSON: %s" % ex)
        return r

    r.summary = str(data.get("summary") or "").strip()
    r.phase = data.get("phase") or ""
    r.task_type = data.get("task_type")
    r.verdict = data.get("verdict")
    r.outcome = data.get("outcome")
    r.blocker = data.get("blocker")
    r.build_evidence = data.get("build_evidence")
    r.blocker_detail = data.get("blocker_detail")
    r.stage_requests = data.get("stage_requests", {})
    r.task_id = data.get("task_id")
    r.complete = data.get("complete")
    r.outputs = data.get("outputs", [])
    r.remaining = data.get("remaining")
    r.ready_for_review = data.get("ready_for_review", True)
    if not isinstance(r.ready_for_review, bool):
        r.errors.append("ready_for_review must be true or false")
    for name in ("options", "decisions", "handoffs", "security"):
        v = data.get(name) or []
        if not isinstance(v, list):
            r.errors.append(name + " must be a list")
        setattr(r, name, v if isinstance(v, list) else [])

    if not r.summary:
        r.errors.append("summary missing")
    elif len(r.summary.split()) > 20:
        r.errors.append("summary over 20 words")
    if r.phase not in PHASES:
        r.errors.append("unknown phase '%s'" % r.phase)
    if r.task_type not in TASK_TYPES + (None,):
        r.errors.append("unknown task_type '%s'" % r.task_type)
    if r.verdict not in ("APPROVED", "REVISE", None):
        r.errors.append("verdict must be APPROVED, REVISE or null")
    for o in r.options:
        oid = str(o.get("id", "")) if isinstance(o, dict) else ""
        if not OPTION_ID.match(oid):
            r.errors.append("option id '%s' must look like O1" % oid)
            continue
        if not str(o.get("title", "")).strip():
            r.errors.append("%s: title missing" % oid)
        scores = o.get("scores") or {}
        if not isinstance(scores, dict):
            r.errors.append("%s: scores must be an object" % oid)
            continue
        for crit, v in scores.items():
            if not _valid_score(v):
                r.errors.append("%s.%s: score needs [0-3, reason]" % (oid, crit))
    return r

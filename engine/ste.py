"""Structural checks from ASD-STE100 Issue 9 (no dictionary check).

Exempt: fenced code (including the ```duo block), inline code, block quotes,
"quoted text", URLs, headings and table rows.
"""
import re
from dataclasses import dataclass

IMPERATIVE_VERBS = set("""
add remove run use check write read set make keep open close install test replace move
delete create update fix split merge return call send show select start stop give get put
apply build change compare copy define do find follow go include let list load look mark
note pass print push pull rename report restart retry review save scan see skip sort store
take tell try turn verify wait commit choose ask measure probe score drop cut
""".split())
MODALS = re.compile(r"\b(should|would|could|may|might)\b", re.I)
CONTRACTION = re.compile(r"\b\w+(n['’]t|['’](re|ll|ve|d|m))\b", re.I)
FENCE = re.compile(r"^```.*?^```[ \t]*$", re.M | re.S)


@dataclass
class Violation:
    rule: str
    sentence: str
    detail: str


def _clean(md):
    md = FENCE.sub("\n", md)
    md = re.sub(r"`[^`\n]*`", "CODE", md)
    md = re.sub(r"https?://\S+", "URL", md)
    md = re.sub(r'"[^"\n]*"|“[^”\n]*”', "QUOTE", md)
    lines = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith((">", "#", "|", "<!--")):
            s = ""
        lines.append(s)
    return "\n".join(lines)


def _units(md):
    """Yield (is_list_item, text) blocks: list items alone, paragraphs joined."""
    para = []
    for line in _clean(md).splitlines() + [""]:
        item = re.match(r"^([-*+]|\d+[.)])\s+(.*)", line)
        if item or not line:
            if para:
                yield False, " ".join(para)
                para = []
            if item:
                yield True, item.group(2)
        else:
            para.append(line)


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def lint(markdown):
    out = []
    for is_item, block in _units(markdown):
        sents = _sentences(block)
        if not is_item and len(sents) > 6:
            out.append(Violation("paragraph", block[:80], "%d sentences (max 6)" % len(sents)))
        for s in sents:
            words = s.split()
            first = re.sub(r"[^a-z]", "", words[0].lower()) if words else ""
            limit, rule = (20, "length-procedural") if first in IMPERATIVE_VERBS else (25, "length-descriptive")
            if len(words) > limit:
                out.append(Violation(rule, s, "%d words (max %d)" % (len(words), limit)))
            m = MODALS.search(s)
            if m:
                out.append(Violation("modal", s, "uses '%s'" % m.group(1)))
            if ";" in s:
                out.append(Violation("semicolon", s, "split into two sentences"))
            c = CONTRACTION.search(s)
            if c:
                out.append(Violation("contraction", s, "write '%s' in full" % c.group(0)))
    return out

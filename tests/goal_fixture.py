"""Goal-check replies for local scripted workers; no provider calls."""
import json
import re
from pathlib import Path


def write_report(root, text, status='achieved', evidence=None, reason='Saved output meets the requested goal.'):
    inputs = Path(re.search(r'Read the saved goal input at (.*?)\. Read referenced', text).group(1))
    source = json.loads(inputs.read_text())
    report = Path(re.search(r'Save the goal report to `([^`]+)`', text).group(1))
    evidence = evidence or [p for p in source['files'] if p.endswith(('make.py', 'readme.md', 'work.txt'))]
    if not evidence:
        evidence = list(source['files'])[:1]
    data = dict(input_signature=source['signature'], status=status,
                summary='The saved outputs were checked against the user goal.', reason=reason,
                criteria=[dict(requirement=source['requirement'], status='unmet' if status != 'achieved' else 'met', evidence=evidence)],
                questions=[dict(question='Why did the first method fail?', answer='The evidence leaves a required result unresolved.')],
                alternatives=[dict(approach='Use a smaller discriminating experiment.', tradeoff='Test the failed assumption before scaling up.')],
                next_action='Save the missing result and compare it with the baseline.' if status != 'achieved' else '',
                progress_evidence=evidence)
    report.write_text(json.dumps(data))
    return data


def write_summary(text):
    from engine.goal_loop import SUMMARY_HEADINGS
    output = Path(re.search(r'account to `([^`]+)`', text).group(1))
    source_file = Path(re.search(r'complete cycle input at (.*?)\. Read referenced', text).group(1))
    source = json.loads(source_file.read_text())
    output.write_text('# Detailed cycle summary\n\n' + '\n\n'.join(
        '## ' + heading + '\n' + 'Saved fixture observations for ' + source['requirement'] +
        '. Referenced versions: ' + ', '.join(source['files']) + '. Unknown results remain unmeasured.'
        for heading in SUMMARY_HEADINGS))
    return output

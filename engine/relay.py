"""Shared helpers for the role workflow: task input, optional extra stages and the review brief."""
from pathlib import Path

EXTRA_STAGES = {'research', 'equip', 'feasibility', 'verify', 'security'}


def required(state, stage):
    """An extra stage runs only when the planner asked for it with a reason."""
    return stage in state.get('stage_requests', {})


def request_errors(requests, allowed=EXTRA_STAGES):
    if not isinstance(requests, dict):
        return ['stage_requests must be an object of stage names and task-specific reasons']
    return ['Invalid optional stage or missing reason: ' + str(stage)
            for stage, reason in requests.items()
            if stage not in allowed or not isinstance(reason, str) or not reason.strip()]


def task_input(task, requirements='', messages=()):
    return '# Task\n\n' + task + '\n\n# Requirements\n\n' + requirements + (
        '\n\n# User updates\n\n' + '\n\n'.join(messages) if messages else '')


def requirements(root):
    return '\n\n'.join('## ' + name + '\n\n' + path.read_text()
                        for name in ('duo.md', 'AGENTS.md')
                        if (path := Path(root) / name).is_file() and not path.is_symlink())


REVIEW = ('Check the finished stage against every requirement and the saved check evidence; reuse passing evidence only while its inputs are unchanged. '
          'Include concrete security risks. Start with APPROVED or REVISE on its own line. For REVISE, list EVERY required fix now, in this one review, '
          'one per line as `file: problem -> fix`; do not hold any back for a later round. Tie each fix to an unmet user requirement or a regression. Optional ideas never require another round. '
          'Account for every required goal at first review; then report changes only. Use Outcome <stable item ID and name>: '
          'Achieved / Needs revision / Needs user input / Waiting on <ID> / Unverified, followed by evidence or the exact missing input. '
          'Approve the integrated build only when all required goals pass, including integration checks in the plan. '
          'Include regressions, useful discoveries, or no demonstrated gain. If work remains, add Next: <action and expected result, '
          'or owner and condition for retry>. Continue an independent goal whenever possible. Only if all remaining items need external action or depend on such items, use a separate '
          'Blocked: line naming the owner and retry condition. Use existing evidence. Never approve because a round limit was reached.')

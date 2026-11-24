"""Repeat focused contracts, syntax checks, and line coverage without extra dependencies.

Run from the source folder with Python 3.13. Local socket and tmux checks are separate.
"""
import ast
import json
import re
import subprocess
import sys
import trace
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / 'Validation Logs/Generic Agents'
MODULES = ['cli_adapters', 'agent_failures', 'model_catalog', 'roleflow', 'skill_inventory', 'model_discovery']
TESTS = ['tests.test_model_discovery', 'tests.test_antigravity', 'tests.test_cli_adapters', 'tests.test_generic_agents', 'tests.test_codex_worker',
         'tests.test_model_policy.RolePolicyTests', 'tests.test_agents.OtherAgentTests',
         'tests.test_settings', 'tests.test_helper']


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    tracer = trace.Trace(count=True, trace=False, ignoredirs=[sys.prefix, '/Library/Developer/CommandLineTools'])
    context = {'unittest': unittest, 'names': TESTS}
    tracer.runctx('result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))', context, context)
    counts = tracer.results().counts
    coverage = []
    for name in MODULES:
        path = ROOT / 'engine' / (name + '.py')
        lines = {n for n in trace._find_executable_linenos(str(path)) if isinstance(n, int) and n > 0}
        hit = {line for (file, line), n in counts.items() if Path(file).resolve() == path and n}
        missed = sorted(lines - hit)
        coverage.append({'module': name, 'executed': len(lines) - len(missed), 'executable_lines': len(lines),
                         'coverage_percent': round(100 * (len(lines) - len(missed)) / len(lines), 1), 'uncovered_lines': missed})
    (LOGS / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    syntax = []
    for path in sorted((ROOT / 'engine').rglob('*.py')):
        ast.parse(path.read_text())
    ast.parse((ROOT / 'bin/duo').read_text())
    syntax.append('All engine Python files and bin/duo: AST PASS')
    scripts = [('web/index.html script ' + str(i), text) for i, text in enumerate(
                re.findall(r'<script[^>]*>(.*?)</script>', (ROOT / 'web/index.html').read_text(), re.S)) if text.strip()]
    for name, text in scripts:
        result = subprocess.run(['node', '--input-type=module', '--check'], input=text, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(name + ': ' + result.stderr)
        syntax.append(name + ': PASS')
    (LOGS / 'syntax-checks.log').write_text('\n'.join(syntax) + '\n')
    print(json.dumps(coverage, indent=2))
    return 0 if context['result'].wasSuccessful() and all(row['coverage_percent'] >= 80 for row in coverage) else 1


if __name__ == '__main__':
    raise SystemExit(main())

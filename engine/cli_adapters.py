"""CLI transport metadata, independent of task roles and model selection.

Configured transports use argv lists. No shell interpolation or guessed flags.
Capabilities are declarations; detection confirms executable/version readiness only.
"""
import shlex
import json
from dataclasses import dataclass
from pathlib import Path
from . import config


@dataclass(frozen=True)
class Adapter:
    id: str
    executable: str
    label: str
    provider: str
    protocol: str
    args: tuple = ()
    model_flag: str = '--model'
    capabilities: tuple = ('tools', 'fresh')
    skill_paths: tuple = ()
    version_args: tuple = ('--version',)
    help_args: tuple = ('--help',)
    required_flags: tuple = ()
    prompt_mode: str = 'stdin'
    prompt_flag: str = ''
    model_list_args: tuple = ()
    model_list_format: str = 'json'

    def argv(self, command, model=None, prompt=None):
        args = list(command) + list(self.args)
        if model:
            if not self.model_flag:
                raise ValueError('Adapter does not support explicit model selection: ' + self.id)
            args += [self.model_flag, model]
        args += ['-'] if self.protocol == 'codex' else []
        if prompt is not None and self.prompt_mode == 'arg':
            args += ([self.prompt_flag] if self.prompt_flag else []) + [prompt]
        return args

    def launch(self):
        return shlex.join([self.executable])

    def encode_prompt(self, prompt):
        if self.protocol == 'antigravity':
            return json.dumps({'event': 'user', 'message': {'content': prompt}}) + '\n'
        return prompt

    def events(self, event):
        """Normalize transport output into worker operations."""
        kind = event.get('type')
        if self.protocol == 'antigravity':
            if event.get('event') == 'init':
                yield 'model', event.get('init', {}).get('model', '')
            elif event.get('event') == 'step_update':
                info = event.get('step_update', {}).get('tool_info', {})
                if info.get('error'):
                    yield 'diagnostic', info
            elif event.get('event') == 'result':
                result = event.get('result', {})
                if result.get('status') != 'SUCCESS':
                    yield 'failure', result
                else:
                    yield 'usage', result.get('usage', {})
                    yield 'text', result.get('response', '')
        elif self.protocol == 'claude':
            if kind == 'system' and event.get('subtype') == 'init':
                yield 'model', event.get('model', '')
            elif kind == 'assistant':
                for block in event.get('message', {}).get('content', []):
                    if block.get('type') == 'text':
                        yield 'text', block.get('text', '')
            elif kind == 'user':
                for block in event.get('message', {}).get('content', []):
                    if block.get('type') == 'tool_result' and block.get('is_error'):
                        yield 'diagnostic', block
            elif kind == 'result':
                for denial in event.get('permission_denials', []):
                    yield 'permission', denial
                if event.get('is_error'):
                    yield 'failure', event
                else:
                    yield 'usage', event.get('usage', {})
                    yield 'text', event.get('result', '')
        elif self.protocol == 'codex':
            if kind == 'item.completed':
                item = event.get('item', {})
                if item.get('type') == 'agent_message':
                    yield 'text', item.get('text', '')
                elif item.get('type') == 'command_execution':
                    if item.get('exit_code') not in (None, 0):
                        yield 'diagnostic', item
                    if item.get('aggregated_output'):
                        yield 'output', item['aggregated_output']
            elif kind == 'turn.completed':
                yield 'usage', event.get('usage', {})
            elif kind in ('error', 'turn.failed'):
                yield 'failure', event
        else:
            # Portable JSONL protocol: text, usage, error, and model events.
            if kind in ('text', 'message'):
                yield 'text', event.get('text', '')
            elif kind == 'usage':
                yield 'usage', event.get('usage', {})
            elif kind in ('error', 'failure'):
                yield 'failure', event
            elif kind == 'model':
                yield 'model', event.get('model', '')


BUILTINS = {
    'codex': Adapter('codex', 'codex', 'Codex', 'openai', 'codex',
                     ('exec', '--ephemeral', '--skip-git-repo-check', '--json'),
                     skill_paths=('~/.agents/skills', '~/.codex/skills'),
                     help_args=('exec', '--help'), required_flags=('--model', '--ephemeral', '--json')),
    'claude': Adapter('claude', 'claude', 'Claude', 'anthropic', 'claude',
                      ('--print', '--output-format', 'stream-json', '--verbose', '--no-session-persistence'),
                      skill_paths=('~/.claude/skills',), required_flags=('--model', '--print', '--no-session-persistence')),
    'agy': Adapter('agy', 'agy', 'Antigravity', 'google-antigravity', 'antigravity',
                   ('--input-format', 'stream-json', '--output-format', 'stream-json'),
                   skill_paths=('~/.gemini/config/skills', '~/.gemini/antigravity-cli/skills', '~/.gemini/skills', '~/.agents/skills'),
                   required_flags=('--model', '--input-format', '--output-format'),
                   model_list_args=('models',), model_list_format='text'),
}


def registry():
    result = dict(BUILTINS)
    for key, spec in config.CFG.get('cli_adapters', {}).items():
        if not isinstance(spec, dict) or not spec.get('executable'):
            raise ValueError('CLI adapter requires an executable: ' + key)
        for field in ('executable', 'label', 'provider', 'protocol', 'model_flag', 'prompt_mode', 'prompt_flag', 'model_list_format'):
            if field in spec and not isinstance(spec[field], str):
                raise ValueError('Adapter ' + field + ' must be a string')
        protocol = spec.get('protocol', 'text')
        if protocol not in ('text', 'jsonl', 'codex', 'claude', 'antigravity'):
            raise ValueError('Unsupported adapter protocol: ' + protocol)
        if spec.get('prompt_mode', 'stdin') not in ('stdin', 'arg'):
            raise ValueError('Adapter prompt_mode must be stdin or arg')
        if spec.get('model_list_format', 'json') not in ('json', 'text'):
            raise ValueError('Adapter model_list_format must be json or text')
        for field in ('args', 'capabilities', 'skill_paths', 'version_args', 'help_args', 'required_flags', 'model_list_args'):
            if field in spec and (not isinstance(spec[field], list) or not all(isinstance(v, str) for v in spec[field])):
                raise ValueError('Adapter ' + field + ' must be a list of strings')
        result[key] = Adapter(key, spec['executable'], spec.get('label', key), spec.get('provider', key), protocol,
                              tuple(spec.get('args', [])), spec.get('model_flag', '--model'),
                              tuple(spec.get('capabilities', [])), tuple(spec.get('skill_paths', [])),
                              tuple(spec.get('version_args', ['--version'])), tuple(spec.get('help_args', ['--help'])),
                              tuple(spec.get('required_flags', [spec.get('model_flag', '--model')])),
                              spec.get('prompt_mode', 'stdin'), spec.get('prompt_flag', ''),
                              tuple(spec.get('model_list_args', [])), spec.get('model_list_format', 'json'))
    return result


def get(identity):
    adapters = registry()
    if identity in adapters:
        return adapters[identity]
    matches = [a for a in adapters.values() if Path(a.executable).name == Path(identity).name]
    if len(matches) == 1:
        return matches[0]
    raise ValueError('No unambiguous CLI adapter configured for ' + identity)

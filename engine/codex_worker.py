"""Terminal adapter for ephemeral agent calls. Conversation context lives only in RAM."""
import json
import re
import signal
import subprocess
import sys
import threading
import io
from collections import deque
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import network_policy, broker, cli_adapters, agent_failures, cost

MAX_CONTEXT = 120_000


class Worker:
    def __init__(self, command, adapter_id=None):
        if not command:
            raise ValueError("Expected an agent executable")
        self.adapter = cli_adapters.get(adapter_id or command[0])
        self.command = command
        self.provider = self.adapter.id
        self.history = []
        self.model = None
        clean, index = [command[0]], 1
        while index < len(command):
            word = command[index]
            if word in (self.adapter.model_flag, "--model", "-m") and index + 1 < len(command):
                self.model = command[index + 1]; index += 2; continue
            if word.startswith("--model="):
                self.model = word.split("=", 1)[1]; index += 1; continue
            clean.append(word); index += 1
        self.command = clean
        self.usage = {}
        self.seen_model = None
        self.failed = False
        self.broker_task = None

    def configure_broker(self, prompt):
        """Read broker-owned routing metadata without interpreting agent output."""
        self.broker_task = None
        pointer = re.search(r"DUO assignment: read (.+) and complete the assigned task\.", prompt)
        assignment = Path(pointer.group(1)) if pointer else None
        text = assignment.read_text() if assignment and assignment.is_file() else prompt
        matches = list(re.finditer(r"(?m)^Broker task record: (.+)$", text))
        if not matches:
            return
        found = matches[-1]
        path = Path(found.group(1).strip())
        try:
            record = json.loads(path.read_text())
            self.broker_task = {"run_dir": str(path.parents[2]), "task_id": record["id"]}
            archive = broker.paths(self.broker_task["run_dir"], record["id"])[1].with_name("worker-context.jsonl")
            broker._append(archive, {"assignment": prompt, "previous_context": self.history})
        except (OSError, ValueError, KeyError) as failure:
            self.broker_task = {"run_dir": str(path.parents[2]), "task_id": path.stem}
            self.diagnostic(str(failure), operation="broker_metadata")

    def diagnostic(self, message, raw=None, operation=None):
        if self.broker_task:
            try:
                broker.error(**self.broker_task, message=message, raw=raw,
                             operation=operation, source="native_" + self.provider)
            except (OSError, ValueError) as failure:
                print("Broker diagnostic recording failed; native work continues: " + str(failure), file=sys.stderr, flush=True)

    def record_usage(self):
        """Attach this call's tokens and approximate cost to its task card. Never blocks the work."""
        if not self.broker_task or not self.usage:
            return
        model = self.seen_model or self.model
        priced = cost.usage_cost(model, self.usage)
        try:
            broker.event(self.broker_task["run_dir"], "usage", self.broker_task["task_id"], model=model,
                         usd=priced and priced["usd"], source=priced and priced["source"],
                         input_tokens=self.usage.get("input_tokens", 0), output_tokens=self.usage.get("output_tokens", 0))
        except (OSError, ValueError) as failure:
            print("Usage recording failed; native work continues: " + str(failure), file=sys.stderr, flush=True)

    def argv(self, prompt=None):
        return self.adapter.argv(self.command, self.model, prompt)

    def context(self, prompt):
        if self.broker_task:
            return ("Use the current assignment, broker task record, and saved project outcomes. "
                    "Read operational diagnostics from their separate files when relevant. "
                    "Previous worker context is preserved in diagnostics/worker-context.jsonl, "
                    "and is not replayed into this assignment.\nCurrent assignment:\n" + prompt)
        kept = ["Previous assignment:\n%s\nPrevious response:\n%s\n" % (user, reply)
                for user, reply in self.history]
        return ("Duo starts a fresh ephemeral call for each assignment. "
                "Use the saved project files as the current source of truth. "
                "Earlier conversation below is context, not a new assignment.\n\n" +
                "\n".join(kept) + "\nCurrent assignment:\n" + prompt)

    def failure(self, event):
        self.failed = True
        self.diagnostic("Native agent call failed", raw=event, operation="agent_call")
        print(agent_failures.MARKER + json.dumps(agent_failures.classify(event)), flush=True)
        print("Agent call failed before completing the assigned task.", flush=True)

    def event(self, event, replies):
        for operation, value in self.adapter.events(event):
            if operation == 'model' and value:
                self.seen_model = value
            if operation == 'model' and value and self.model:
                matches = value.startswith(self.model) or (self.model in ('opus', 'sonnet', 'haiku') and self.model in value)
                if not matches:
                    raise RuntimeError("The CLI selected a different model than the assigned model")
            elif operation == 'text' and value and value not in replies:
                replies.append(value)
                print(value, flush=True)
            elif operation == 'usage':
                self.usage = value
            elif operation == 'failure':
                self.failure(value)
            elif operation == 'permission':
                self.diagnostic('Native Claude permission refusal', raw=value, operation='permission')
                print('Native Claude permission refusal: ' + json.dumps(value), flush=True)
            elif operation == 'diagnostic':
                self.diagnostic('Native tool reported an error', raw=value, operation='tool')
            elif operation == 'output':
                print(value, flush=True)

    def turn(self, prompt):
        print("Working on assignment...", flush=True)
        self.failed = False
        self.usage = {}
        self.configure_broker(prompt)
        replies = []
        payload = self.context(prompt)
        child = subprocess.Popen(self.argv(payload), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=network_policy.task_environment(Path.cwd()))
        errors = deque(maxlen=128)
        def drain_errors():
            for line in child.stderr:
                errors.append(line[-2048:])
                print(line.rstrip(), file=sys.stderr, flush=True)
        reader = None
        if isinstance(child.stderr, io.TextIOBase):
            reader = threading.Thread(target=drain_errors, daemon=True)
            reader.start()
        try:
            if self.adapter.prompt_mode == 'stdin':
                child.stdin.write(self.adapter.encode_prompt(payload))
            child.stdin.close()
            for line in child.stdout:
                if self.adapter.protocol == 'text':
                    value = line.rstrip()
                    if value:
                        replies.append(value)
                        print(value, flush=True)
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    print(line.rstrip(), flush=True)
                    continue
                if not isinstance(event, dict):
                    self.failure({'message': 'Malformed JSON event from agent CLI'})
                    continue
                self.event(event, replies)
            code = child.wait()
            if reader:
                reader.join(timeout=1)
            self.record_usage()
            if code == 0 and not replies:
                self.failure({'message': 'The agent returned no task response'})
            if self.failed:
                code = code or 1
            if code == 0 and replies:
                self.history.append((prompt, "\n".join(replies)))
            elif code != 0:
                if not self.failed:
                    self.failure({'exit_code': code, 'message': ''.join(errors)})
                print("Agent call exited with status %d." % code, flush=True)
            print("DUO_WORKER_RESULT:" + ("success" if code == 0 and replies else "failed"), flush=True)
            return code
        except BaseException:
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            raise
        finally:
            child.stdout.close()
            if reader and not reader.is_alive():
                child.stderr.close()
            if not child.stdin.closed:
                child.stdin.close()

    def run(self):
        print("Duo agentic worker: tool execution enabled; conversation context stays in memory.", flush=True)
        while True:
            try:
                prompt = input("codex> " if self.provider == "codex" else "❯ ").strip()
                if not prompt:
                    continue
                if prompt in ("/exit", "/quit"):
                    return
                if prompt.startswith("/model "):
                    self.model = prompt.split(None, 1)[1]
                    print("Model selection saved for the next call.", flush=True)
                elif prompt in ("/status", "/usage", "/cost"):
                    total = self.usage.get("input_tokens", 0) + self.usage.get("output_tokens", 0)
                    print("Last call: %d tokens. %s" % (total, json.dumps(self.usage)), flush=True)
                elif prompt in ("/compact", "/new", "/clear"):
                    self.history.clear()
                    print("Memory context cleared. Project files are unchanged.", flush=True)
                elif prompt.startswith("/"):
                    print("Unsupported worker command. Use /model, /status, /compact, or /exit.", flush=True)
                else:
                    self.turn(prompt)
            except EOFError:
                return
            except KeyboardInterrupt:
                print("\nWorker interrupted. Partial project files remain available.", flush=True)
            except (OSError, RuntimeError) as ex:
                self.failure({'message': str(ex)})
                print("Agent worker failed: %s" % ex, flush=True)
                print("DUO_WORKER_RESULT:failed", flush=True)


if __name__ == "__main__":
    command = sys.argv[1:]
    adapter_id = None
    if command[:1] == ['--adapter']:
        adapter_id = command[1]
        command = command[2:]
    if command and command[0] == "--":
        command.pop(0)
    Worker(command, adapter_id).run()

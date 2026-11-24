"""Stand-in agent CLI for tests. Reads `DUO turn` lines typed by tmux and writes protocol replies.

Usage: fake_agent.py <agent-id> [--bad-json] [--login]
"""
import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.research_fixture import make_report, make_review

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
name = sys.argv[1]
if "--crash" in sys.argv:
    sys.exit(1)
bad_json = "--bad-json" in sys.argv
rubber = "--rubber-stamp" in sys.argv
if "--login" in sys.argv:
    print("Welcome. Please log in to continue.", flush=True)
    for _ in sys.stdin:
        print("Please log in to continue.", flush=True)
    sys.exit(0)

if sys.stdin.isatty():
    # Real agent CLIs read raw keys. In canonical mode macOS drops a typed line past 1024 bytes
    # (MAX_CANON), newline included, so a long turn prompt would never arrive.
    import termios
    _attrs = termios.tcgetattr(sys.stdin.fileno())
    _attrs[3] &= ~termios.ICANON
    _attrs[6][termios.VMIN], _attrs[6][termios.VTIME] = 1, 0
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _attrs)

print("[%s] ready\n> " % name, flush=True)
ask = sys.argv[sys.argv.index("--ask") + 1] if "--ask" in sys.argv else None
hang = "--hang" in sys.argv


def approval(command):
    """Ask like a real agent CLI: one keypress, y/Enter approves, n/Escape denies."""
    import termios
    import tty
    print("\nDo you want to run this command?\n  %s\n> 1. Yes\n  2. No  (y/n)" % command, flush=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    ok = ch in ("y", "\r", "\n")
    print("Approved." if ok else "Denied.", flush=True)
    print("\n" * 60, flush=True)                     # clear the prompt off the visible screen
    return ok
critiques = 0


def block(d):
    return "```duo\n%s\n```\n<!-- END -->\n" % json.dumps(d)


for line in sys.stdin:
    line = line.strip()
    if line.startswith("DUO assignment:"):
        line = " ".join(Path(re.search(r"read (.+) and complete the assigned task\.", line).group(1)).read_text().splitlines())
    if line.startswith("/model"):
        print("Model selected.\n> ", flush=True)
        continue
    if "DUO turn" not in line:
        continue
    line = line[line.index("DUO turn"):]
    reply_path = re.search(r"Write your reply to `([^`]+)`", line) or re.search(r"Write your reply to (\S+)", line)
    out = Path(reply_path.group(1))
    phase = re.search(r"phase (\w+)", line).group(1)
    kind = re.search(r"kind (\w+)", line).group(1)
    ids = re.findall(r"\bO\d+\b", (re.search(r"Options: ([O\d, ]+)\.", line) or re.match("", "")).group(0) or "")
    run_folder = re.search(r"(\.duo/\d{4}-\d{6}-[0-9a-f]{8})", line) or re.search(r"(\.duo/[^/\s`]+)", line)
    duo_dir = Path(run_folder.group(1)) if run_folder else out.parent.parent
    if hang:
        hang = False
        continue
    # A blind review starts a fresh process. The approval fixture belongs to
    # the original task, not an artificial repeated request in every reviewer.
    if ask and kind not in ("critique", "secure"):
        approved = approval(ask)
        ask = None
        prose_note = "The command ran." if approved else "The command was denied."
    d = {"summary": "%s finished %s." % (name, kind), "phase": phase, "options": [], "decisions": [],
         "handoffs": [], "verdict": None}
    assigned = re.search(r"Assigned task_id: (\S+)\.", line)
    if assigned:
        d["task_id"] = assigned.group(1)
        ack = re.search(r"to (\S+/acks/\S+) to acknowledge", line)
        if ack:
            path = Path(ack.group(1))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(d["task_id"])
    prose = "The %s step is complete." % kind
    if kind == 'cycle_summary':
        from tests.goal_fixture import write_summary
        write_summary(line)
        d.update(outcome='progress')
    elif kind == 'goal_assess':
        prose = 'Independent assessment by ' + name + ': compare a different method with the saved baseline.'
        d.update(outcome='progress')
    elif kind == 'goal_assign':
        prose = 'Build the missing result and compare the alternative with the baseline. Preserve earlier accepted requirements.'
        d.update(outcome='progress')
    elif kind == 'goal_check':
        from tests.goal_fixture import write_report
        prior = json.loads(Path(re.search(r'Read the saved goal input at (.*?)\. Read referenced', line).group(1)).read_text())
        goal_status = 'continue' if '--goal-continue' in sys.argv and not prior['previous_cycles'] else 'achieved'
        write_report(Path.cwd(), line, goal_status)
        d.update(outcome='progress')
    elif kind == 'assess':
        prose = 'Independent fixture assessment by ' + name + '. Build the requested tool and inspect its output.'
        d.update(outcome='progress')
    elif kind == 'plan_review':
        marker = duo_dir / 'plan-rejected-once'
        if '--reject-plan-once' in sys.argv and not marker.exists():
            # Every role runs in a fresh process, so the first rejection is remembered on disk.
            marker.write_text('rejected')
            prose = 'REVISE: the plan has no observable check for the output. Add one.'
            d.update(outcome='progress', verdict='REVISE')
        else:
            prose = 'APPROVED. The plan covers the task and its acceptance checks.'
            d.update(outcome='progress', verdict='APPROVED')
    elif kind == 'exchange':
        (duo_dir / 'plan.md').write_text('# Agreed plan\nBuild work.txt and inspect its saved content.\n')
        d.update(outcome='progress', task_type='programming', stage_requests={})
    elif kind == "feasibility":
        report_path = Path(re.search(r"Write the feasibility JSON to (\S+)\.", line).group(1))
        report = {"checks": [{"id": c, "category": c, "status": "not_applicable", "detail": "Local fixture has no external prerequisite.", "evidence": []}
                            for c in ("inputs", "metadata", "runtime")],
                  "packages": [{"id": "P1", "title": "Complete fixture output", "depends": [], "requires": ["inputs", "metadata", "runtime"],
                                "kind": "implementation" if "--selfedit" in sys.argv else "document",
                                "deliverables": ["engine/a.py"] if "--selfedit" in sys.argv else ["work.txt"],
                                "behavior": "The requested fixture output is usable", "acceptance": "Inspect fixture output"}]}
        if any(flag in sys.argv for flag in ("--workflow-split", "--workflow-runtime-block", "--workflow-both")):
            report["packages"] = [{"id": "P1", "title": "Implement the runnable tool", "depends": [], "requires": ["runtime"],
                                   "kind": "implementation", "deliverables": ["src/runner.py"], "behavior": "Run the requested local tool",
                                   "acceptance": "python3 src/runner.py"},
                                  {"id": "P2", "title": "Implement an independent component", "depends": [], "requires": ["metadata"],
                                   "kind": "implementation", "deliverables": ["src/other.py"], "behavior": "Run the independent local component",
                                   "acceptance": "python3 src/other.py"}]
            if "--workflow-split" in sys.argv:
                probe = Path(".duo/logs/metadata-probe.log")
                probe.parent.mkdir(parents=True, exist_ok=True)
                probe.write_text("The external fixture does not document model cutoff dates")
                report["checks"][1].update(status="blocked", detail="Model cutoff dates are undocumented", evidence=[probe.as_posix()])
        report_path.write_text(json.dumps(report))
    elif phase == "refs":
        refs = duo_dir / "refs"
        refs.mkdir(exist_ok=True)
        thin = "--thin" in sys.argv
        if kind == "refs":
            names = ["%02d-shot.png" % i for i in range(2 if thin else 6)]
            for n in names:
                (refs / n).write_bytes(PNG)
            (refs / "index.md").write_text("\n".join("%s | https://x | a scene" % n for n in names))
            heads = ["Core loop", "Controls", "Combat", "Enemies", "Camera", "Art direction"]
            (refs / "features.md").write_text("\n\n".join("## %s\n- a\n- b\n- c" % h for h in heads))
        else:
            (refs / "review.md").write_text("Gaps found\n- none\n\nChanges made\n- none\n")
        prose = "- I opened every image."
    elif phase == "frame":
        # The task text is in the assignment itself. Older layouts also saved it beside the reply.
        human = out.parent / "0001-human.md"
        task_text = line + (human.read_text() if human.exists() else "")
        d["task_type"] = "research" if "Research" in task_text else "programming"
    elif phase == "diverge":
        d["options"] = [{"id": "O%d" % i, "title": "%s idea %d" % (name, i), "pros": ["Fast to build.", "Easy to read."], "cons": ["Less flexible.", "Needs care."]} for i in range(1, 5)]
    elif kind == "research_check":
        make_review(Path.cwd())
        d["verdict"] = "APPROVED"
    elif kind == "research_expand":
        make_report(Path.cwd())
    elif phase == "scout":
        report = make_report(Path.cwd())
        if "--reading-batches" in sys.argv:
            report["sources"][0]["read_scope"] = "abstract"
            Path("research/search.log").write_text("Local fixture query and result record.\n")
            Path("research/access-log-0058.md").write_text("Local fixture source access record.\n")
            report["sources"][0]["evidence"] += "; research/access-log-0058.md"
            report["searches"][0]["evidence"] += "; research/search.log"
            Path("research/coverage.json").write_text(json.dumps(report))
            d["complete"] = False
        if "--scout-notes" in sys.argv:
            Path("research").mkdir(exist_ok=True)
            Path("research/source-notes.md").write_text("Local test fixture source observation.\n")
            Path("research/access-log.md").write_text("Local test fixture access observation.\n")
        d["options"] = [{"id": o, "title": "x", "probe": "%s ran a quick check on %s." % (name, o)} for o in ids]
    elif phase == "score":
        rub = json.loads((duo_dir / "rubric.json").read_text())
        keys = [g["id"] for g in rub["gates"]] + [c["id"] for c in rub["criteria"]]
        d["options"] = [{"id": o, "title": "x", "scores": {k: [(1 if k in [g["id"] for g in rub["gates"]] else (3 if o == "O1" else 1)), "Reason given."] for k in keys}} for o in ids]
    elif phase == "setup":
        sb = duo_dir / "sandbox"
        sb.mkdir(exist_ok=True)
        if kind == "setup":
            broken = "--brokenfile" in sys.argv
            (sb / "Dockerfile").write_text("FROM python:3.12-slim\n" + ("RUN BROKEN\n" if broken else "RUN pip install flask\n"))
            (sb / "sandbox.json").write_text(json.dumps({"tools": ["python3 --version"], "run": "python app.py",
                                                         "smoke": "true", "ports": [8000]}))
            (sb / "prereqs.md").write_text("- python3: runs the app\n- flask: web server\n- pip: installs packages\n")
        elif kind == "setup_fix":
            (sb / "Dockerfile").write_text("FROM python:3.12-slim\nRUN pip install flask\n")
        else:
            (sb / "review.md").write_text("Gaps found\n- none\n\nChanges made\n- none\n")
        prose = "- I wrote the sandbox files."
    elif kind in ("build", "revise") and any(flag in sys.argv for flag in ("--workflow-split", "--workflow-runtime-block", "--workflow-both")):
        package, _ = json.JSONDecoder().raw_decode(line.split("BUILD PACKAGE: ", 1)[1])
        log = Path(".duo/logs") / (out.stem + "-focused.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        if "--workflow-runtime-block" in sys.argv and package["id"] == "P1":
            log.write_text("A required remote fixture source is unavailable after two attempted routes")
            d.update(outcome="blocked", blocker="evidence_missing", complete=False, outputs=[], remaining=["Acquire required source"],
                     blocker_detail={"missing": "Required source evidence", "attempts": [log.as_posix()], "affected": ["P1"],
                                     "alternatives": ["Supply a verified source"], "retry_when": "A verified source becomes available"})
        else:
            target = Path(package["deliverables"][0])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("print('usable complete component') # " + out.name + "\n")
            import subprocess
            result = subprocess.run([sys.executable, str(target)], capture_output=True, text=True)
            log.write_text(result.stdout + "exit=" + str(result.returncode))
            d.update(outcome="progress", complete=True, outputs=[target.as_posix()], remaining=[],
                     build_evidence={"behavior": "The requested component runs successfully", "checks": [log.as_posix()]})
    elif kind in ("build", "revise"):
        if "--selfedit" in sys.argv:
            Path("engine").mkdir(exist_ok=True)
            Path("tests").mkdir(exist_ok=True)
            Path("engine/a.py").write_text("X = 2 # " + out.name + "\n")
            Path("tests/test_b.py").write_text("pass\n")
        else:
            revised = Path("work.txt").exists()
            Path("work.txt").write_text((("revised\n" if revised else "built\n") + out.name + "\n") + ("password = 'hunter2hunter2'\n" if "--leak" in sys.argv else ""))
        d.update(complete=True, outputs=["engine/a.py"] if "--selfedit" in sys.argv else ["work.txt"], remaining=[])
        plan_file = duo_dir / "plan.md"
        if "--unfinished-until-planned" in sys.argv and plan_file.exists() and plan_file.read_text().startswith("# Quick task"):
            # Bigger than it looked: only a real plan lets this agent finish.
            d.update(complete=False, remaining=["The parser is not wired"])
            prose = "Built part of it.\n\nUnfinished work: the parser is not wired."
        check = Path(".duo/logs") / (out.stem + "-build.log")
        check.parent.mkdir(parents=True, exist_ok=True)
        check.write_text("Fixture output inspected: " + out.name)
        d["build_evidence"] = {"behavior": "The complete fixture output is usable.", "checks": [check.as_posix()]}

    elif kind == "critique":
        d['outcome'] = 'progress'
        report = re.search(r'Save the requirement-to-evidence mapping, check commands, results, and required fixes to (\S+)\.', line)
        if report:
            p = Path(report.group(1)); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('Fixture review: inspected saved work.txt and build evidence.\n')
        critiques += 1
        content = Path("work.txt").read_text() if Path("work.txt").exists() else "revised"
        d["verdict"] = "REVISE" if "revised" not in content and not rubber else "APPROVED"
        prose = "Looks fine." if rubber else "- I read work.txt.\n- I ran the check."
    elif kind == "secure":
        secure_calls = globals().get("secure_calls", 0) + 1
        globals()["secure_calls"] = secure_calls
        if "--insecure" in sys.argv and "revised" not in Path("work.txt").read_text():
            d["verdict"] = "REVISE"
            d["security"] = [{"severity": "high", "file": "work.txt", "issue": "Writes a weak default.", "fix": "Remove it."}]
            prose = "- I read the diff."
        else:
            d["verdict"] = "APPROVED"
            d["security"] = []
            prose = "- I read the diff."
    elif kind == "verify":
        d["verdict"] = "APPROVED"
        prose = "- I ran the success test."
    if "start your reply with a line 'answer:'" in line.lower() or "start your reply with a line 'Answer:'" in line:
        prose = "Answer: I read your message.\n\n" + prose
    if bad_json:
        bad_json = False
        out.write_text("Broken reply.\n```duo\n{not json\n```\n<!-- END -->\n")
        continue
    out.write_text(prose + "\n\n" + block(d))
    print("> ", flush=True)

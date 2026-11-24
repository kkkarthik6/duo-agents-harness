"""Deny-first command catalog. Native sandboxes enforce execution boundaries.

The catalog records provenance. An entry never overrides a deny or an argument check.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

POLICY = Path(__file__).resolve().parent.parent / "rubrics/command-policy.json"
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
SECRET = re.compile(r"(?:^|[/\\])(?:\.ssh|\.aws|\.gnupg|\.kube|\.netrc)(?:[/\\]|$)|"
                    r"\.codex[/\\]auth\.json|\.claude[/\\]\.credentials|\.docker[/\\]config\.json|"
                    r"(?:^|[/\\])\.env(?:\.[^/\\]*)?$|(?:id_rsa|id_ed25519|master\.passwd|shadow|/etc/passwd)(?:\b|$)|"
                    r"\.(?:pem|key|p12|pfx|keystore)$|(?:^|[/\\])(?:credentials|secrets)(?:\.[^/\\]*)?$", re.I)
SYSTEM = ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/private/etc", "/opt", "/Applications")
GUARD_FILES = {"engine/security.py", "engine/command_policy.py", "engine/guard_hook.py",
               "rubrics/security.json", "rubrics/command-policy.json"}
SHELL_OPS = {";", "&&", "||", "|", "|&", "&", "(", ")"}
REDIRS = {">", ">>", "<", "<<", "<<<", "2>", "&>"}


@lru_cache(maxsize=8)
def _load(path, stamp):
    data = json.loads(Path(path).read_text())
    return data, {tuple(r["prefix"]): r["scope"] for r in data["allow"]}


def catalog():
    return _load(str(POLICY), POLICY.stat().st_mtime_ns)


def tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>();")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _parts(command, depth=0):
    if depth > 6:
        raise ValueError("Shell nesting exceeds policy limit")
    words = tokens(command)
    out, current = [], []
    for script in re.findall(r"\$\(([^()]*)\)|`([^`]*)`", command):
        out.extend(_parts(script[0] or script[1], depth + 1))
    for word in words + [";"]:
        if word in SHELL_OPS:
            if current:
                while current and (re.match(r"^[A-Za-z_]\w*=", current[0]) or current[0] in ("command", "builtin", "exec", "nohup")):
                    current.pop(0)
                if current and Path(current[0]).name == "env" and len(current) > 1:
                    current.pop(0)
                    while current and re.match(r"^[A-Za-z_]\w*=", current[0]):
                        current.pop(0)
                if current and Path(current[0]).name in ("timeout", "gtimeout", "time", "nice", "stdbuf"):
                    current.pop(0)
                    while current and (current[0].startswith("-") or re.fullmatch(r"\d+(?:\.\d+)?[smhd]?", current[0])):
                        current.pop(0)
                if current and Path(current[0]).name in SHELLS and any(x in ("-c", "-lc", "-ic") for x in current[1:]):
                    i = next(i for i, x in enumerate(current) if x in ("-c", "-lc", "-ic"))
                    if i + 1 >= len(current):
                        raise ValueError("Shell command is missing")
                    out.extend(_parts(current[i + 1], depth + 1))
                elif current:
                    out.append(current)
                    if current[0] in ("./.duo/sbx", ".duo/sbx"):
                        out.extend(_parts(" ".join(current[1:]), depth + 1))
                    if Path(current[0]).name in ("docker", "podman") and "exec" in current:
                        for i, value in enumerate(current[:-2]):
                            if Path(value).name in SHELLS and current[i + 1] in ("-c", "-lc", "-ic"):
                                out.extend(_parts(current[i + 2], depth + 1))
            current = []
        else:
            current.append(word)
    return out


def inside(path, workspace):
    p = Path(path).expanduser()
    p = p if p.is_absolute() else Path(workspace) / p
    base, target = Path(workspace).resolve(), p.resolve()
    return target == base or base in target.parents


def path_denial(path, workspace, write=False):
    if SECRET.search(str(path)):
        return "Secret or credential path"
    if not write:
        return None
    if str(path) == "/dev/null":
        return None
    if not inside(path, workspace):
        return "Write outside the assigned workspace"
    target = (Path(workspace) / path).resolve()
    if any((parent / ".duo-core.json").is_file() for parent in target.parents):
        return "Installed Duo core is immutable"
    base = Path(workspace).resolve()
    rel = target.relative_to(base).as_posix()
    if rel in GUARD_FILES or rel.startswith((".codex/", ".claude/", ".git/")) or rel in (".duo/sbx", ".codex", ".claude", ".git"):
        return "Protected guard or agent settings"
    if rel.endswith("/state.json") and (rel.startswith(".duo/") or rel.startswith("duo/runs/")):
        return "Harness control state"
    if any(target == Path(d) or Path(d) in target.parents for d in SYSTEM):
        # A project under /opt is valid; writes outside it already failed above.
        if not any(base == Path(d) or Path(d) in base.parents for d in SYSTEM):
            return "Protected system files"
    return None


def deny_reasons(command, workspace=None):
    """Concrete denials, including nested shells and absolute executable paths."""
    data, _ = catalog()
    reasons = []
    try:
        parts = _parts(command)
    except ValueError:
        parts = []
    for words in parts:
        name = Path(words[0]).name.lower()
        if name in data["deny_executables"]:
            reasons.append("Blocked command: " + name)
        if name == "env" and len(words) == 1:
            reasons.append("Environment dump exposes credentials")
        if "$" in words[0] or "`" in words[0]:
            reasons.append("Dynamic executable name")
        if name == "find" and any(x in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for x in words):
            reasons.append("File deletion or command execution through find")
        if name in ("xargs", "parallel") and any(Path(x).name in data["deny_executables"] for x in words[1:]):
            reasons.append("Blocked command through an execution wrapper")
        if name in ("docker", "podman") and any(x in ("git", "rm", "unlink", "shred") for x in words[1:]):
            reasons.append("Blocked command inside a container")
        for word in words[1:]:
            if SECRET.search(word):
                reasons.append("Secret or credential access")
        if workspace:
            for i, word in enumerate(words):
                if word in (">", ">>", "&>") and i + 1 < len(words):
                    reason = path_denial(words[i + 1], workspace, write=True)
                    if reason:
                        reasons.append(reason)
            if name in ("mkdir", "touch", "cp", "mv", "tee", "install", "ln"):
                for word in words[1:]:
                    if not word.startswith("-") and word not in REDIRS:
                        reason = path_denial(word, workspace, write=True)
                        if reason:
                            reasons.append(reason)
    if re.search(r"\b(?:os\.(?:remove|unlink|rmdir)|shutil\.rmtree|Path\([^\n]+\)\.(?:unlink|rmdir)|child_process|subprocess[^\n]+[\"']git\b)", command):
        reasons.append("Deletion or blocked command through inline code")
    if re.search(r"\b(?:BASH_ENV|ENV|PATH|IFS|LD_PRELOAD|DYLD_INSERT_LIBRARIES)\s*=", command):
        reasons.append("Shell or loader injection")
    if re.search(r"\$(?:\{)?[A-Z_]*(?:SECRET|TOKEN|PASSWORD|PRIVATE_KEY|ACCESS_KEY)[A-Z_]*", command):
        reasons.append("Secret environment variable access")
    if re.search(r"(?:getenv|environ)[^\n]*(?:SECRET|TOKEN|PASSWORD|PRIVATE_KEY|ACCESS_KEY)", command):
        reasons.append("Secret environment variable access")
    if re.search(r"(?:^|[/\"'])\.env(?:\.[\w.-]+)?(?=[\s\"'/);]|$)|\.(?:pem|key|p12|pfx|keystore)(?=[\s\"'/);]|$)|Keychains/|keychain-db", command, re.I):
        reasons.append("Secret file reference")
    return list(dict.fromkeys(reasons))


def _read_url(words):
    args = words[1:]
    if any(x in ("--config", "-K", "--netrc", "--netrc-file", "--netrc-optional", "--header", "-H", "--user", "-u") for x in args):
        return False
    if re.search(r"(?:^|\s)(?:-d|-F|-T|--data\S*|--form\S*|--upload-file|--post\S*|--request\s+(?!GET\b|HEAD\b)|-X\s+(?!GET\b|HEAD\b))", " ".join(args), re.I):
        return False
    urls = [urlparse(x) for x in args if x.startswith(("https://", "http://"))]
    hosts = catalog()[0]["read_hosts"]
    return bool(urls) and all(u.scheme == "https" and not u.username and
                            any(u.hostname == h or (u.hostname or "").endswith("." + h) for h in hosts) for u in urls)


def _compose(words, workspace):
    if any(x in ("--volumes", "-v", "--remove-orphans") for x in words):
        return "deny", "Destructive Compose cleanup is blocked"
    if len(words) > 2 and words[2] in ("version", "--version", "--help", "config", "ps", "logs", "images", "top", "ls"):
        return "allow", "Compose inspection"
    # Render interpolation and YAML aliases before approving a runtime configuration.
    base = words[:2]
    args = words[2:]
    for i, arg in enumerate(args):
        if arg in ("-f", "--file", "--project-directory") and i + 1 < len(args):
            if not inside(args[i + 1], workspace):
                return "deny", "Compose configuration is outside the workspace"
            base += [arg, args[i + 1]]
    try:
        result = subprocess.run(base + ["config", "--format", "json"], cwd=workspace,
                                capture_output=True, text=True, timeout=2)
        if result.returncode:
            return "review", "Compose configuration did not render"
        data = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "review", "Compose configuration requires inspection"
    for service in data.get("services", {}).values():
        if (service.get("privileged") or service.get("devices") or service.get("cap_add") or
                any(service.get(k) == "host" for k in ("pid", "ipc", "network_mode", "userns_mode")) or
                any("unconfined" in x for x in service.get("security_opt", []))):
            return "deny", "Compose requests host powers"
        for volume in service.get("volumes", []):
            if not isinstance(volume, dict):
                return "review", "Unresolved Compose volume"
            if volume.get("type") == "bind" and not inside(volume.get("source", "/"), workspace):
                return "deny", "Compose bind mount is outside the workspace"
        build = service.get("build", {})
        if isinstance(build, dict) and not inside(build.get("context", "."), workspace):
            return "deny", "Compose build context is outside the workspace"
    if any(v.get("external") or v.get("driver_opts") for v in data.get("volumes", {}).values() if isinstance(v, dict)):
        return "deny", "External volumes and volume driver options are blocked"
    return "allow", "Compose configuration passed project checks"


def _venv_python(value, workspace):
    path = Path(value).expanduser()
    path = path if path.is_absolute() else Path(workspace) / path
    root = path.parent.parent
    return inside(root, workspace) and (root / "pyvenv.cfg").is_file() and path.is_file()


def _local_install(words, workspace):
    """Allow dependency installation only into a verified project virtual environment."""
    name = Path(words[0]).name
    args = words[1:]
    if name == "uv" and len(args) >= 2 and args[:2] in (["pip", "install"], ["pip", "sync"]):
        if "/" in words[0] and Path(words[0]).resolve() != Path(shutil.which("uv") or "/unavailable").resolve():
            return "review", "Package manager is not the installed uv executable"
        if sum(arg == "--python" or arg.startswith("--python=") for arg in args) != 1:
            return "review", "Select exactly one project virtual environment"
        target = next((arg.split("=", 1)[1] for arg in args if arg.startswith("--python=")), None)
        if "--python" in args and args.index("--python") + 1 < len(args):
            target = args[args.index("--python") + 1]
        if not target or not _venv_python(target, workspace):
            return "review", "Select a Python executable in a verified project virtual environment"
    elif name.startswith("python") and len(args) >= 3 and args[:3] == ["-m", "pip", "install"]:
        if not _venv_python(words[0], workspace):
            return "review", "Host package installation requires permission"
    elif name in ("pip", "pip3") and args[:1] == ["install"]:
        if not _venv_python(words[0], workspace):
            return "review", "Host package installation requires permission"
    else:
        return None
    if name != "uv" and any(arg == "--python" or arg.startswith("--python=") for arg in args):
        return "review", "Interpreter override requires a separate scope check"
    for i, arg in enumerate(args):
        if arg in ("--system", "--user", "--break-system-packages", "-t") or arg.startswith("-t") or arg.split("=", 1)[0] in ("--prefix", "--root", "--target"):
            return "review", "Installation outside the selected virtual environment requires permission"
        if arg.startswith(("git+", "hg+", "svn+", "bzr+")):
            return "deny", "Version-control package sources are blocked"
        if arg in ("--cache-dir", "-r", "--requirement", "-c", "--constraint") and i + 1 < len(args):
            reason = path_denial(args[i + 1], workspace, write=arg == "--cache-dir")
            if reason:
                return "deny", reason
        if arg.startswith(("http://", "https://")) and not _read_url(["curl", arg]):
            return "review", "Package URL is outside the public read catalog"
    return "allow", "Dependency install targets a verified project virtual environment"


def classify(command, workspace, container=None):
    from . import security
    denied = deny_reasons(command, workspace)
    findings = security.check_command(command, workspace)
    denied += [f.reason for f in findings if f.action == "deny"]
    if denied:
        return "deny", "; ".join(dict.fromkeys(denied))
    if "`" in command or re.search(r"\$[({]", command):
        return "review", "Shell expansion requires the native permission check"
    try:
        parts = _parts(command)
    except ValueError:
        return "review", "Unparsed shell syntax"
    if not parts:
        return "review", "No command to classify"
    _, allow = catalog()
    for words in parts:
        local_install = _local_install(words, workspace)
        if local_install:
            if local_install[0] != "allow":
                return local_install
            continue
        executable = Path(words[0])
        name = executable.name
        if "/" in words[0] and str(executable.parent) not in ("/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"):
            if words[0] not in ("./.duo/sbx", ".duo/sbx"):
                return "review", "Project executables use the native sandbox"
        if name == "docker-compose":
            words = ["docker", "compose"] + words[1:]
            name = "docker"
        args = words[1:]
        if name in ("curl", "wget"):
            if not _read_url(words):
                return "review", "Network request is outside the public read catalog"
            output_flags = ("-o", "--output", "-P", "--directory-prefix", "--output-document", "--output-dir", "--dump-header", "--cookie-jar")
            for flag in output_flags + (("-O",) if name == "wget" else ()):
                if flag in args and args.index(flag) + 1 < len(args):
                    reason = path_denial(args[args.index(flag) + 1], workspace, write=True)
                    if reason:
                        return "deny", reason
                for arg in args:
                    if arg.startswith(flag + "=") or (len(flag) == 2 and arg.startswith(flag) and len(arg) > 2):
                        target = arg.split("=", 1)[1] if "=" in arg else arg[2:]
                        reason = path_denial(target, workspace, write=True)
                        if reason:
                            return "deny", reason
            continue
        if words[0] in ("./.duo/sbx", ".duo/sbx"):
            inner = security._contained(shlex.join(words))
            if inner is None:
                return "review", "Container wrapper requires quoted arguments"
            if not container:
                return "review", "Container ownership is not registered"
            continue
        if name in ("docker", "podman"):
            if args in (["--version"], ["--help"]):
                continue
            denied = security.docker_findings(shlex.join(words).replace("podman", "docker", 1), workspace)
            if denied:
                return "deny", "; ".join(f.reason for f in denied)
            if args and args[0] in ("plugin", "swarm", "secret", "config", "trust"):
                return "deny", "Docker host extensions, cluster changes, and secret operations are blocked"
            if any(x in ("--context", "--host", "-H", "--url", "--connection", "--volumes-from", "--volume-driver", "--ssh", "--secret") or x.startswith("-H") or
                   x.startswith(("--context=", "--host=", "--volumes-from=", "--volume-driver=")) for x in args):
                return "deny", "Remote daemon or unscoped container access"
            for i, arg in enumerate(args):
                volume = None
                if arg in ("-v", "--volume", "--mount") and i + 1 < len(args):
                    volume = args[i + 1]
                elif arg.startswith(("--volume=", "--mount=")):
                    volume = arg.split("=", 1)[1]
                elif arg.startswith("-v") and len(arg) > 2:
                    volume = arg[2:]
                if volume:
                    source = re.search(r"(?:^|,)(?:src|source)=([^,]+)", volume)
                    source = source.group(1) if source else volume.split(":", 1)[0]
                    if source.startswith(("/", ".", "~")) and not inside(source, workspace):
                        return "deny", "Container bind mount is outside the assigned workspace"
                    if source.startswith("$"):
                        return "review", "Resolve the container bind path before approval"
            if args and args[0] in ("build", "buildx") and args[-1] != "-" and not args[-1].startswith("-"):
                if not inside(args[-1], workspace):
                    return "deny", "Docker build context is outside the workspace"
            if args and args[0] in ("exec", "stop", "restart", "kill", "rm", "cp", "start"):
                if not container or container not in args:
                    return "review", "Container operation requires the assigned container"
            if args and args[0] in ("compose", "buildx", "volume", "network", "image", "container"):
                if args[0] == "compose":
                    decision, reason = _compose(words, workspace)
                    if decision != "allow":
                        return decision, reason
                    continue
                return "review", "Docker resource configuration requires a guard check"
            if args and (name, args[0]) not in allow:
                return "review", "Docker operation requires a scope check"
            continue
        if name in ("find", "rg"):
            if any(x in ("--pre", "--hostname-bin", "--search-zip", "-z", "-fprint", "-fprint0", "-fprintf", "-fls") or
                   x.startswith(("--pre=", "--hostname-bin=")) for x in args):
                return "review", "Search command executes code or writes files"
        if name == "file" and any(x in ("-m", "--magic-file", "-f", "--files-from") or x.startswith(("--magic-file=", "--files-from=")) for x in args):
            return "review", "File command opens paths from a supplied file"
        if name == "which" and any(x in ("-i", "--read-alias", "--read-functions") for x in args):
            return "review", "Which command reads shell definitions"
        if name == "sed" and not (len(args) in (2, 3) and args[0] == "-n" and re.fullmatch(r"(?:\d+,)?\d+p", args[1])):
            return "review", "Sed program uses the native sandbox"
        if name in ("awk", "jq", "yq", "sort", "base64", "tar", "unzip", "gzip", "bzip2", "xz", "zstd", "mutool", "ffprobe", "identify"):
            return "review", "Data-processing command uses the native sandbox"
        if any(f.rule == "package_install" and f.action == "review" for f in findings):
            return "review", "Host package installation requires permission"
        scope = next((allow[tuple(words[:n])] for n in range(min(len(words), 3), 0, -1) if tuple(words[:n]) in allow), None)
        if scope is None:
            return "review", "Command uses the native sandbox and permission check"
        if scope == "version" and len(words) != 2:
            return "review", "Version query has extra arguments"
        if any(w in REDIRS or w.startswith("--output=") for w in args):
            return "review", "File output requires a path check"
        if scope in ("project", "checked", "container"):
            return "review", "Project execution uses the native sandbox"
    return "allow", "Catalog and argument checks passed"

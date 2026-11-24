"""The security guard: fixed rules, no model. Nothing in Duo can override a deny.

Rules live in rubrics/security.json. Feedback layers, auto-approve and Duo's
self-changes can never edit that file or this one.
"""
import hashlib
import json
import re
import shlex
import subprocess
import time
from functools import lru_cache
from dataclasses import dataclass, asdict
from pathlib import Path

from . import config

RULES_FILE = config.APP_DIR / "rubrics" / "security.json"
PROTECTED = ("engine/security.py", "engine/command_policy.py", "engine/guard_hook.py", "rubrics/security.json", "rubrics/command-policy.json", "tests/test_security.py")
ALLOWED = ("engine/", "web/", "rubrics/", "tests/")


@dataclass
class Finding:
    rule: str
    action: str           # deny | review
    reason: str
    detail: str

    def to_dict(self):
        return asdict(self)


@lru_cache(maxsize=8)
def _read_rules(path, stamp):
    return json.loads(Path(path).read_text())


def _rules():
    return _read_rules(str(RULES_FILE), RULES_FILE.stat().st_mtime_ns)


def _norm(text):
    """Join wrapped terminal lines so a command split across lines still matches."""
    return re.sub(r"\s+", " ", re.sub(r"\\\n", " ", str(text))).strip()


def verdict(findings):
    actions = {f.action for f in findings}
    return "deny" if "deny" in actions else "review" if "review" in actions else "allow"


def _rm_outside(cmd, ws):
    out = []
    for m in re.finditer(r"\brm\s+((?:-\w+\s+)+)(.+?)(?=$|[;&|])", cmd):
        if not re.search(r"r", m.group(1), re.I):
            continue
        for target in m.group(2).split():
            t = target.strip("'\"")
            home = t in ("~", "$home", "/", "/*", "..") or t.startswith(("~/", "$home", ".."))
            outside = t.startswith("/") and not (ws and t.startswith(str(ws).lower().rstrip("/") + "/"))
            if home or outside:
                out.append(t)
    return out


# Public datasets used widely in ML and AI research. A plain download (GET, https, no upload flags) from one of
# these hosts is allowed without a question. Add hosts here; a host matches itself and its subdomains only.
DATASET_HOSTS = (
    "huggingface.co", "cdn-lfs.huggingface.co",            # Hugging Face datasets
    "zenodo.org",                                          # Zenodo research archives
    "grouplens.org",                                       # MovieLens
    "archive.ics.uci.edu",                                 # UCI Machine Learning Repository
    "yann.lecun.com",                                      # MNIST
    "cs.toronto.edu",                                      # CIFAR-10 / CIFAR-100
    "ossci-datasets.s3.amazonaws.com",                     # torchvision dataset mirrors
    "rajpurkar.github.io",                                 # SQuAD
    "ai.stanford.edu",                                     # IMDB reviews, Stanford AI datasets
    "snap.stanford.edu",                                   # Stanford SNAP graph datasets
    "openml.org",                                          # OpenML
    "statmt.org",                                          # WMT translation corpora
    "dumps.wikimedia.org",                                 # Wikipedia dumps
    "commoncrawl.org",                                     # Common Crawl
    "dl.fbaipublicfiles.com",                              # Meta AI public datasets
    "packages.debian.org",                                 # Debian package lookups (which package ships a file)
    "api.github.com",                                      # GitHub release and tag lookups (read-only GET)
)
UPLOAD_FLAGS = re.compile(r"(^|\s)(-d|--data\S*|-f|--form\S*|-t|--upload-file|--post\S*|-x\s*(post|put|patch|delete)|--request\s*(post|put|patch|delete))(\s|=|$)")


def dataset_hosts(cmd):
    """The dataset hosts a plain download command fetches from, or None when it is anything else.

    None when the command has no https URL, any URL is not https or not on DATASET_HOSTS, or it sends data.
    """
    if re.search(r"http://", cmd) or UPLOAD_FLAGS.search(cmd):
        return None
    urls = re.findall(r"https://[^\s'\"<>|;&)`$]+", cmd)
    if not urls:
        return None
    hosts = set()
    for u in urls:
        host = re.match(r"https://([^/:?#]+)", u).group(1)
        if not any(host == d or host.endswith("." + d) for d in DATASET_HOSTS):
            return None
        hosts.add(host)
    return sorted(hosts)


def check_command(text, ws=None):
    from . import command_policy
    cmd = _norm(text).lower()
    found, seen = [], set()
    found.extend(Finding("deny_catalog", "deny", reason, "") for reason in command_policy.deny_reasons(text, ws))
    bad = _rm_outside(cmd, ws)
    if bad:
        found.append(Finding("rm_outside", "deny", "Deletes files outside this project.", ", ".join(bad)))
    for r in _rules()["commands"]:
        m = re.search(r["pattern"], cmd)
        if m and r["id"] not in seen:
            if r["id"] == "download" and any(f.rule == "pipe_to_shell" for f in found):
                continue
            seen.add(r["id"])
            if r["id"] == "download":
                hosts = dataset_hosts(cmd)
                if hosts:
                    found.append(Finding("dataset_download", "allow",
                                         "Downloads a standard public dataset from %s." % ", ".join(hosts), m.group(0)[:200]))
                    continue
            found.append(Finding(r["id"], r["action"], r["reason"], m.group(0)[:200]))
    return found


WRAP = re.compile(r"^(?:\./)?\.duo/sbx\s+(.+?)\s*$", re.S)


def _contained(line):
    """The inner command when the whole line is one call to the container wrapper with quoted arguments.

    Anything the host shell would act on outside quotes (; & | < > ` $ ( ) or a backslash) means the line
    is not contained. Inside double quotes the host would expand $ and backticks, so those count too.
    """
    m = WRAP.match(line)
    if not m:
        return None
    rest, quote = m.group(1), None
    for c in rest:
        if quote is None:
            if c in "'\"":
                quote = c
            elif c in ";&|<>`$()\\\n":
                return None
        elif quote == "'":
            quote = None if c == "'" else quote
        elif c == '"':
            quote = None
        elif c in "$`\\":
            return None
    if quote:
        return None
    try:
        return " ".join(shlex.split(rest))
    except ValueError:
        return None


DOCKER = re.compile(r"(^|[\s;&|(`])docker(-compose)?(\s|$)", re.I)
DOCKER_UNSAFE = [
    (re.compile(r"--privileged\b"), "runs a privileged container"),
    (re.compile(r"--(pid|ipc|userns|uts)[=\s]+host\b"), "shares the Mac's processes or users with the container"),
    (re.compile(r"--cap-add[=\s]+(all|sys_admin|sys_module|sys_ptrace|dac_read_search)\b", re.I), "gives the container system powers"),
    (re.compile(r"--device[=\s]"), "gives the container a host device"),
    (re.compile(r"--(?:net|network)[=\s]+host\b"), "shares the host network"),
    (re.compile(r"--cap-add[=\s]+\S+", re.I), "adds container capabilities"),
    (re.compile(r"--device-cgroup-rule|volume-opt=|\bvolume-opt\b"), "grants host device or volume driver access"),
    (re.compile(r"--security-opt[=\s]+\S*unconfined", re.I), "turns off the container's security profile"),
    (re.compile(r"docker\.sock"), "gives the container control of Docker"),
    (re.compile(r"\b(system|volume|container|image|network|builder)\s+prune\b"), "deletes Docker data that is not this run's"),
    (re.compile(r"\b(rm|rmi|kill|stop)\b[^;&|]*\$\(\s*docker\s+(ps|images)\b"), "removes every container or image"),
    (re.compile(r"\bdocker(-compose)?\s+(push|login)\b"), "publishes or signs in to a registry"),
    (re.compile(r"(\s-H\s*|--host[=\s]+|docker_host=)\s*(tcp|ssh)://", re.I), "uses a remote Docker host"),
]
SYSTEM_DIRS = ("/etc", "/var", "/usr", "/bin", "/sbin", "/system", "/library", "/private", "/opt", "/applications", "/dev", "/root")


def _mount_sources(cmd):
    out = [m.group(1) for m in re.finditer(r"(?:\s-v|--volume)[=\s]+[\"']?([^:\s\"']+):", cmd)]
    out += [m.group(1) for m in re.finditer(r"--mount[=\s]+\S*?(?:source|src)=([^,\s]+)", cmd)]
    return out


def docker_findings(text, ws=None):
    """Unsafe Docker use. Plain docker and docker compose commands are allowed; these options are not."""
    low = (text or "").lower()
    if not DOCKER.search(low):
        return []
    found = [Finding("docker_unsafe", "deny", "This Docker command %s." % why, m.group(0)[:80])
             for pat, why in DOCKER_UNSAFE for m in [pat.search(low)] if m]
    ws = (ws or "").rstrip("/").lower()
    for src in _mount_sources(low):
        s = src.strip("\"'")
        if not (s.startswith("/") or s.startswith("~") or s.startswith("$home") or s.startswith("${home}")):
            continue                     # a named volume, or a path relative to the project
        if ws and (s == ws or s.startswith(ws + "/")):
            continue                     # inside the project
        home_root = re.match(r"^/users/[^/]+/?$", s) or s in ("~", "~/", "$home", "${home}")
        if s == "/" or home_root or any(s == d or s.startswith(d + "/") for d in SYSTEM_DIRS) \
                or s.startswith("~/") or s.startswith("$home/") or re.match(r"^/users/[^/]+/", s):
            found.append(Finding("docker_unsafe", "deny",
                                 "This Docker command mounts a host folder outside the project: %s." % src, src))
    return found


def analyze(text, ws=None, sandbox=None):
    """Findings for an approval question, and where it runs: True when it runs only inside the run's container
    (./.duo/sbx), "docker" when it is a direct docker or docker compose command, else False.
    Direct Docker is allowed; docker_findings() refuses the unsafe options."""
    sandbox = sandbox or {}
    for line in (text or "").splitlines():
        line = re.sub(r"^[^\w./]+", "", line)
        if re.match(r"(?:\./)?\.duo/sbx\s", line):
            inner = _contained(line)
            if inner is not None:
                # Contained: installs and downloads are fine. Everything else is still checked.
                return [f for f in check_command(inner, ws) if f.rule not in ("package_install", "download")], True
            break
    found = check_command(text, ws)
    is_docker = any(DOCKER.search(re.sub(r"^[^\w./]+", "", l).lower()) for l in (text or "").splitlines())
    if is_docker:
        found += docker_findings(text, ws)
        # docker pull/build fetch images: that is what Docker is for, so they are not "downloads" to review.
        found = [f for f in found if f.rule != "download"]
    if sandbox.get("on"):
        if sandbox.get("ready"):
            found = [Finding(f.rule, "deny", "Install it in the container instead: run it with docker exec or ./.duo/sbx.", f.detail)
                     if f.rule == "package_install" and not is_docker else f for f in found]
    if ws and verdict(found) == "allow":
        from . import command_policy
        # Scan the full question for denials above. Scope-check the command only
        # for this exact CLI approval format; unknown formats remain review-only.
        match = re.search(r"(?m)^Do you want to run this command\?\s*\n([^\n]+)\n> 1\. Yes\n  2\. No  \(y/n\)\s*$", text)
        scoped = match.group(1).strip() if match else text
        decision, reason = command_policy.classify(scoped, ws)
        if decision == "review":
            found.append(Finding("scope_check", "review", reason, ""))
        elif decision == "deny":
            found.append(Finding("scope_check", "deny", reason, ""))
    return found, ("docker" if is_docker else False)


def _added_lines(ws, path):
    ws = Path(ws)
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ws, capture_output=True).returncode == 0
    if tracked:
        diff = subprocess.run(["git", "diff", "-U0", "HEAD", "--", path], cwd=ws, capture_output=True, text=True).stdout
        return [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    p = ws / path
    if not p.is_file() or p.stat().st_size > 1_000_000:
        return []
    try:
        return p.read_text(errors="strict").splitlines()
    except (UnicodeDecodeError, OSError):
        return []


def check_changes(ws, changed):
    rules = _rules()
    found = []
    for path in changed:
        f = Path(ws) / path
        for r in rules["paths"]:
            if re.search(r["pattern"], path):
                found.append(Finding(r["id"], r["action"], r["reason"], path))
        lines = _added_lines(ws, path)
        for r in rules["changes"]:
            for i, line in enumerate(lines):
                if re.search(r["pattern"], line):
                    found.append(Finding(r["id"], r["action"], r["reason"], "%s: %s" % (path, line.strip()[:120])))
                    break
    return found


def check_proposal(ws, changed, base):
    """Checks for a change Duo makes to its own code. Any deny means it cannot be approved."""
    found = []
    if not changed:
        return [Finding("no_change", "deny", "Nothing changed.", "")]
    for path in changed:
        if path in PROTECTED:
            found.append(Finding("protected_file", "deny", "Changes the security guard or its tests.", path))
        elif not path.startswith(ALLOWED):
            found.append(Finding("outside_scope", "deny", "Changes a file outside engine, web, rubrics and tests.", path))
    if not any(p.startswith("tests/") for p in changed):
        found.append(Finding("no_test", "deny", "The change has no test.", ""))
    diff = subprocess.run(["git", "diff", "--cached", "-U0", base], cwd=ws, capture_output=True, text=True).stdout
    gone = [l[1:].strip() for l in diff.splitlines() if l.startswith("-") and not l.startswith("---") and "security." in l]
    if gone:
        found.append(Finding("removes_security_call", "deny", "Removes a call to the security guard.", gone[0][:120]))
    return found + check_changes(ws, changed)


def check_layer(change):
    rules = _rules()["layers"]
    path = str(change.get("path", "")).lower()
    value = json.dumps(change.get("value"))
    found = []
    if re.search(rules[0]["pattern"], path) or re.search(rules[0]["pattern"], str(change.get("kind", ""))):
        found.append(Finding(rules[0]["id"], "deny", rules[0]["reason"], path))
    if re.search(rules[1]["pattern"], value):
        found.append(Finding(rules[1]["id"], "deny", rules[1]["reason"], value[:120]))
    return found


def category(findings, contained, sandbox):
    """A plain-language category and sentence for an approval question. Pure: no I/O."""
    rules = {f.rule for f in findings}
    covered = ("package_install", "download")
    if any(f.action == "deny" and f.rule not in covered for f in findings):
        return "danger", "This could change system security."
    if contained == "docker":
        return "docker", "This runs Docker directly. Duo checked it for unsafe options."
    if "package_install" in rules:
        if sandbox and sandbox.get("ready"):
            return "install", "This installs a package on your Mac, not in the container."
        return "install", "This installs a package on your Mac."
    if "download" in rules:
        return "network", "This downloads from the internet."
    if contained:
        return "container", "This runs inside the project container."
    return "other", "This needs your approval."


def log_path():
    return config.root() / ".duo-security.jsonl"


_LOG_ACTORS = {"agents": ["claude", "codex", "agy"]}


def log(finding, run=None, agent=None, source="command", action="denied"):
    from . import orchestration as orch
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        row = dict(finding.to_dict(), time=time.strftime("%Y-%m-%dT%H:%M:%S"), run=run,
                   agent=orch.alias(_LOG_ACTORS, agent), source=source, action=action)
        f.write(json.dumps(orch.redact(_LOG_ACTORS, row)) + "\n")


def read_log(limit=500):
    p = log_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return list(reversed(out))

"""Evidence-backed feasibility and build work packages. No provider permission policy."""
import hashlib
import json
import re
import math
from pathlib import Path

INTEGRATION_ID = "duo-integration"
PACKAGE_FIELDS = {"id", "title", "depends", "requires", "kind", "deliverables", "behavior", "acceptance"}
CATEGORIES = {"inputs", "metadata", "runtime"}
BLOCKERS = {"data_missing", "evidence_missing", "network_access", "tool_missing", "credentials", "approval_needed"}


def local_file(root, rel):
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return None
    base = Path(root).resolve()
    path = (base / rel).resolve()
    return path if base in path.parents and path.is_file() and path.stat().st_size else None


def product_path(rel):
    parts = Path(rel).parts if isinstance(rel, str) else ()
    return bool(parts and not Path(rel).is_absolute() and ".." not in parts and
                parts[0] not in ("duo", ".duo", ".git") and
                not any(p.lower() in ("logs", "acks", "checkpoints") for p in parts))


def signature(text):
    return hashlib.sha256(text.encode()).hexdigest()


def prompt(report, task_text):
    return ("You are the Feasibility checker before build. Inspect the accepted task and existing artifacts. "
            "Do not redesign the plan or claim an experiment is complete. Run small real probes, not the full build. "
            "Check inputs, required metadata, model/tool availability, and expected full-run runtime. "
            "Include stable observed input identities or hashes in each detail. A new log timestamp is not a changed prerequisite. "
            "Distinguish downloaded files from evidence needed for a claim. Record observed throughput and a workload estimate when relevant. "
            "If irrelevant, explain not_applicable. Do not infer undocumented model cutoffs. "
            "Divide only the accepted build into substantial work packages. Use exact product file paths, dependencies, "
            "a concrete delivered behavior, and an acceptance command or inspection. For empirical research, include an executable "
            "experiment runner when requested, separate from dated evidence acquisition and scored experiments. "
            "Keep required registration before scored experiments. Paper polish must not substitute for an experimental deliverable. "
            "A missing input blocks only packages that require it. Do not weaken acceptance requirements. "
            "Write the feasibility JSON to %s. Schema: " % report +
            '{"checks":[{"id":"runtime","category":"runtime","status":"passed|blocked|not_applicable",'
            '"detail":"observed result and estimate or reason","evidence":["relative/probe.log"]}],'
            '"packages":[{"id":"P1","title":"Build usable component","depends":[],"requires":["runtime"],'
            '"kind":"implementation|document|data","deliverables":["src/runner.py"],'
            '"behavior":"concrete capability delivered","acceptance":"focused command or inspection"}]}.'
            " Include all three check categories: inputs, metadata, runtime. Passed and blocked checks require nonempty saved evidence. "
            "For a passed runtime check, include estimated_seconds (positive number) and basis (measurement and workload calculation). "
            "Each package must list its relevant prerequisite checks. Use document packages only for requested documents. "
            "Do not declare logs, status reports, or planning files as product deliverables. "
            "Preserve completed product files. Task context:\n" + task_text)


def read_report(root, rel):
    path = local_file(root, rel)
    if path is None:
        return None, ["Feasibility report is missing or empty: " + rel]
    try:
        report = json.loads(path.read_text())
    except (ValueError, OSError):
        return None, ["Feasibility report must contain JSON"]
    if not isinstance(report, dict):
        return None, ["Feasibility report must be an object"]
    errors = []
    checks, packages = report.get("checks"), report.get("packages")
    if not isinstance(checks, list) or not checks or not isinstance(packages, list) or not packages:
        return None, ["Feasibility needs checks and substantial build packages"]
    seen, categories = {}, set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not check["id"] or check["id"] in seen:
            return None, ["Feasibility check IDs must be unique strings"]
        seen[check["id"]] = check
        if not isinstance(check.get("category"), str):
            return None, ["Feasibility check category must be a string"]
        categories.add(check["category"])
        if check.get("status") not in ("passed", "blocked", "not_applicable") or not str(check.get("detail", "")).strip():
            errors.append("Check %s needs an observed status and explanation" % check["id"])
        evidence = check.get("evidence", [])
        if not isinstance(evidence, list) or any(local_file(root, x) is None for x in evidence):
            errors.append("Check %s cites missing evidence" % check["id"])
        if check.get("status") in ("passed", "blocked") and not evidence:
            errors.append("Observed check %s needs saved probe evidence" % check["id"])
        if check.get("category") == "runtime" and check.get("status") == "passed":
            seconds = check.get("estimated_seconds")
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0 or not str(check.get("basis", "")).strip():
                errors.append("Passed runtime check needs positive estimated_seconds and a measurement basis")
    if not CATEGORIES <= categories:
        errors.append("Feasibility must check inputs, metadata, and runtime")
    ids = []
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]+", package["id"]):
            return None, ["Build package IDs must be nonempty strings"]
        if package["id"] == INTEGRATION_ID or set(package) - PACKAGE_FIELDS:
            errors.append("Feasibility package contains reserved harness fields or ID")
        ids.append(package["id"])
        for field in ("title", "behavior", "acceptance"):
            if not isinstance(package.get(field), str) or not package[field].strip():
                errors.append("Package %s needs %s" % (package["id"], field))
        if package.get("kind") not in ("implementation", "document", "data"):
            errors.append("Package %s needs a deliverable kind" % package["id"])
        outputs = package.get("deliverables")
        if not isinstance(outputs, list) or not outputs or not all(product_path(p) for p in outputs):
            errors.append("Package %s needs product deliverable paths, not logs or harness files" % package["id"])
        if (not isinstance(package.get("requires"), list) or not package["requires"] or
                any(not isinstance(x, str) or x not in seen for x in package["requires"])):
            errors.append("Package %s needs known prerequisite checks" % package["id"])
        if not isinstance(package.get("depends"), list) or any(not isinstance(x, str) for x in package["depends"]):
            errors.append("Package %s needs a dependency list" % package["id"])
    if len(ids) != len(set(ids)):
        errors.append("Build package IDs must be unique")
    if errors:
        return None, errors
    graph = {p["id"]: p["depends"] for p in packages}
    if any(d not in graph for ds in graph.values() for d in ds):
        return None, ["Unknown build package dependency"]
    done = set()
    while len(done) < len(graph):
        ready = {k for k, ds in graph.items() if k not in done and set(ds) <= done}
        if not ready:
            return None, ["Build packages contain a dependency cycle"]
        done |= ready
    return report, []


def receipt_matches(root, receipt):
    for item in receipt.get("outputs", []):
        path = local_file(root, item["path"])
        if path is None:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            return False
    return bool(receipt.get("outputs"))


def install(state, report, key, root=None):
    old = state.get("build_workflow", {})
    same = old.get("key") == key
    receipts = old.get("receipts", {}) if same else {}
    previous = {p["id"]: p for p in old.get("packages", [])} if same else {}
    # Log paths and timestamps can change during a retry without resolving its cause.
    # Compare the observed condition, not a fresh log file's identity.
    fingerprints = {c["id"]: signature(json.dumps({k: c.get(k) for k in
                    ("category", "status", "detail", "condition", "estimated_seconds", "basis")}, sort_keys=True))
                    for c in report["checks"]}
    packages = []
    for spec in report["packages"]:
        row = dict(spec, status="pending")
        before = previous.get(spec["id"], {})
        # Do not reopen an unchanged blocked package after a mere resume.
        unchanged_conditions = all(old.get("check_fingerprints", {}).get(c) == fingerprints[c] for c in spec["requires"])
        if before.get("status") == "done" and root and spec["id"] in receipts and not receipt_matches(root, receipts[spec["id"]]):
            before = {}
        if (before.get("status") in ("done", "blocked") and all(before.get(k) == spec.get(k) for k in spec)
                and (before["status"] == "done" or unchanged_conditions)):
            row.update(status=before["status"])
            if before.get("blocker_detail"):
                row["blocker_detail"] = before["blocker_detail"]
        if before and all(before.get(k) == spec.get(k) for k in spec) and before.get("artifact_baseline") is not None:
            row["artifact_baseline"] = before["artifact_baseline"]
        packages.append(row)
    if same:
        integration = previous.get(INTEGRATION_ID)
        if integration and integration.get("status") != "done":
            integration = dict(integration)
            integration["requires"] = list(fingerprints)
            if any(old.get("check_fingerprints", {}).get(c) != fingerprints[c] for c in fingerprints):
                integration.update(status="pending")
                integration.pop("blocker_detail", None)
            packages.append(integration)
    receipts = {k: v for k, v in receipts.items() if any(p["id"] == k and p.get("status") == "done" for p in packages)}
    state["build_workflow"] = {"key": key, "checks": report["checks"], "packages": packages, "receipts": receipts, "check_fingerprints": fingerprints}
    if same:
        for field in ("review_baseline", "build_changed"):
            if field in old:
                state["build_workflow"][field] = old[field]
    return state["build_workflow"]


def ready(workflow):
    done = {p["id"] for p in workflow["packages"] if p.get("status") == "done"}
    checks = {c["id"]: c["status"] for c in workflow["checks"]}
    return [p for p in workflow["packages"] if p.get("status") == "pending" and set(p["depends"]) <= done
            and all(checks[c] in ("passed", "not_applicable") for c in p["requires"])]


def complete(workflow):
    return bool(workflow["packages"]) and all(p.get("status") == "done" for p in workflow["packages"])


def package_prompt(package):
    return ("\nBUILD PACKAGE: " + json.dumps(package) + "\n"
            "Deliver the entire assigned capability and integration in this build. "
            "Feasibility is finished for this package. Do not spend build on repeated planning, status reports, "
            "or unrelated paper edits. Preserve registration and evidence requirements. "
            "Return build_evidence with behavior (what now works). Include supporting evidence when relevant. "
            "Every useful build must change an assigned product deliverable. "
            "Set complete true only when all package deliverables and acceptance requirements are met. "
            "If blocked, return blocker_detail with missing, attempts, affected, alternatives, and retry_when. "
            "attempts must list saved evidence paths. Save partial implementation before returning. "
            "Duo will continue independent packages and retain this package's exact blocker.")


def artifact_state(root, paths):
    """Track explicit deliverables even when routine scans exclude nested dataset folders."""
    result = {}
    for rel in paths:
        path = local_file(root, rel)
        if path:
            st = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if st.st_size <= 8 * 1024 * 1024 else None
            result[rel] = [st.st_size, digest or st.st_mtime_ns]
    return result


def artifact_changes(root, package, before):
    after = artifact_state(root, package["deliverables"])
    return [p for p in set(before) | set(after) if before.get(p) != after.get(p)]


def build_errors(root, package, data, changed):
    if data.get("outcome") in ("blocked", "needs_human"):
        if data.get("freeform"):
            return []
        detail = data.get("blocker_detail")
        if not isinstance(detail, dict):
            return ["Blocked build needs blocker_detail: missing, attempts, affected, alternatives, retry_when"]
        errors = []
        for field in ("missing", "retry_when"):
            if not isinstance(detail.get(field), str) or not detail[field].strip():
                errors.append("Blocker needs " + field)
        for field in ("attempts", "affected", "alternatives"):
            if not isinstance(detail.get(field), list) or not detail[field]:
                errors.append("Blocker needs a nonempty " + field + " list")
        if any(local_file(root, x) is None for x in (detail.get("attempts") if isinstance(detail.get("attempts"), list) else [])):
            errors.append("Blocker attempts need saved evidence files")
        if package["id"] not in (detail.get("affected") if isinstance(detail.get("affected"), list) else []):
            errors.append("Blocker must identify the assigned package")
        return errors
    if data.get("outcome") == "answered":
        return []
    evidence = data.get("build_evidence")
    if not isinstance(evidence, dict) or not isinstance(evidence.get("behavior"), str) or not evidence["behavior"].strip():
        return ["Build needs build_evidence.behavior describing the implemented capability"]
    changed = set(changed)
    delivered = [p for p in package["deliverables"] if p in changed and local_file(root, p)]
    if package["kind"] == "implementation":
        documents = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".toml", ".log"}
        delivered = [p for p in delivered if Path(p).suffix.lower() not in documents]
    if not delivered:
        return ["Build changed no assigned product deliverable; planning and logs are not substantial build work"]
    checks = evidence.get("checks")
    if checks is not None and (not isinstance(checks, list) or any(not isinstance(p, str) or local_file(root, p) is None for p in checks)):
        return ["Supplied build_evidence.checks must name existing evidence files"]
    if data.get("complete") is True:
        missing = [p for p in package["deliverables"] if local_file(root, p) is None or p not in (data.get("outputs") if isinstance(data.get("outputs"), list) else [])]
        if missing:
            return ["Completed package is missing deliverables: " + ", ".join(missing)]
    return []


def blocked_text(workflow):
    lines = []
    for check in workflow["checks"]:
        if check["status"] == "blocked":
            affected = [p["id"] for p in workflow["packages"] if check["id"] in p["requires"] and p.get("status") != "done"]
            if affected:
                lines.append("%s: %s. Affected: %s." % (check["id"], check["detail"], ", ".join(affected)))
    for package in workflow["packages"]:
        if package.get("status") == "blocked":
            d = package["blocker_detail"]
            lines.append("%s: %s. Retry when: %s. Alternatives: %s. Evidence: %s." %
                         (package["id"], d["missing"], d["retry_when"], "; ".join(map(str, d["alternatives"])), ", ".join(d["attempts"])))
    return "\n".join(lines) or "No build package is ready. Check its dependencies."

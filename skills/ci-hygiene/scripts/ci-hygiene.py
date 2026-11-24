#!/usr/bin/env python3
"""CI hygiene checker.

Audits a repo's GitHub Actions workflows for three agent-era anti-patterns:

  C1  full clones: actions/checkout with fetch-depth: 0, or a raw
      `git clone` with no --depth. Most jobs only need the latest commit.
  C2  polling loops: a run step that sleeps while asking GitHub whether
      something is done, a while/until loop around a sleep, or
      `gh run watch`. Use native job dependencies (needs:), completion
      events, or webhooks instead.
  C3  required-check inventory: with --repo owner/name and a token in
      GITHUB_TOKEN, lists the default branch's required status checks and
      flags any with no matching job in the repo (orphaned).

Stdlib only. Heuristic line scan of workflow YAML: it finds patterns,
it does not parse YAML, so read a flagged line in context before acting.

Usage:
  ci-hygiene.py <repo-path> [--repo owner/name] [--format text|json]

Exit code 0 means clean, 1 means findings, 2 means a usage error.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

SEVERITY = {"C1": "error", "C2": "warning", "C3": "info"}

FETCH_DEPTH_ZERO = re.compile(r"fetch-depth\s*:\s*0\s*(?:#.*)?$")
GIT_CLONE = re.compile(r"\bgit\s+clone\b(.*)$")
DEPTH_FLAG = re.compile(r"--depth[= ]\d+")
GH_RUN_WATCH = re.compile(r"\bgh\s+run\s+watch\b")
WHILE_LOOP = re.compile(r"^\s*(while|until)\b", re.MULTILINE)
SLEEP = re.compile(r"\bsleep\s+\d+")
STATUS_POLL = re.compile(
    r"(gh\s+api\b|gh\s+run\s+view\b|check-runs|/status\b|/statuses\b|"
    r"commits/[^ ]*/status)"
)
JOB_KEY = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*)\s*:\s*(?:#.*)?$")
JOBS_KEY = re.compile(r"^jobs\s*:\s*(?:#.*)?$")


def _finding(check, path, line, message, fix):
    return {
        "check": check,
        "severity": SEVERITY[check],
        "file": path,
        "line": line,
        "message": message,
        "fix": fix,
    }


def _split_run_blocks(lines):
    """Yield (start_line, text) for each multi-line `run: |` block."""
    blocks = []
    i = 0
    while i < len(lines):
        if re.match(r"^\s*run\s*:\s*[|>]", lines[i]):
            indent = len(lines[i]) - len(lines[i].lstrip())
            j = i + 1
            body = []
            while j < len(lines):
                raw = lines[j]
                if raw.strip() == "":
                    body.append(raw)
                    j += 1
                    continue
                cur = len(raw) - len(raw.lstrip())
                if cur <= indent:
                    break
                body.append(raw)
                j += 1
            blocks.append((i + 1, "\n".join(body)))
            i = j
        else:
            i += 1
    return blocks


def check_file(path, rel):
    """Scan one workflow file. Returns a list of findings."""
    findings = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    for n, line in enumerate(lines, start=1):
        if FETCH_DEPTH_ZERO.search(line):
            findings.append(_finding(
                "C1", rel, n,
                "full clone: checkout with fetch-depth: 0",
                "set fetch-depth: 1, or fetch only the history the job needs",
            ))
        m = GIT_CLONE.search(line)
        if m and not DEPTH_FLAG.search(m.group(1)):
            findings.append(_finding(
                "C1", rel, n,
                "full clone: raw `git clone` with no --depth",
                "use `git clone --depth=1 <repo>`, or fetch more only when needed",
            ))
        if GH_RUN_WATCH.search(line):
            findings.append(_finding(
                "C2", rel, n,
                "polling: `gh run watch` polls GitHub for completion",
                "use a `needs:` dependency or a workflow_run event instead",
            ))
    for start, body in _split_run_blocks(lines):
        has_sleep = bool(SLEEP.search(body))
        has_poll = bool(STATUS_POLL.search(body))
        has_loop = bool(WHILE_LOOP.search(body))
        if has_sleep and has_poll:
            findings.append(_finding(
                "C2", rel, start,
                "polling: run block sleeps while asking GitHub for status",
                "use a `needs:` dependency, a completion event, or a webhook",
            ))
        elif has_loop and has_sleep:
            findings.append(_finding(
                "C2", rel, start,
                "polling: loop around a sleep waits on something else",
                "use a `needs:` dependency, a completion event, or a webhook",
            ))
    return findings


def collect_job_names(workflow_dir):
    """Job ids and names declared across the repo's workflows."""
    names = set()
    if not os.path.isdir(workflow_dir):
        return names
    for fname in sorted(os.listdir(workflow_dir)):
        if not (fname.endswith(".yml") or fname.endswith(".yaml")):
            continue
        with open(os.path.join(workflow_dir, fname),
                  encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        in_jobs = False
        for line in lines:
            if JOBS_KEY.match(line):
                in_jobs = True
                continue
            if in_jobs:
                if re.match(r"^\S", line):
                    break
                m = JOB_KEY.match(line)
                if m:
                    names.add(m.group(1))
    return names


def _github_get(path, token):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "User-Agent": "ci-hygiene",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def check_required(repo, workflow_dir):
    """Inventory required checks via the GitHub API. Returns findings."""
    findings = []
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("C3 skipped: set GITHUB_TOKEN to inventory required checks",
              file=sys.stderr)
        return findings
    try:
        info = _github_get("/repos/" + repo, token)
        branch = info.get("default_branch", "main")
        prot = _github_get(
            "/repos/%s/branches/%s/protection" % (repo, branch), token)
    except urllib.error.HTTPError as exc:
        print("C3 skipped: GitHub API returned %s" % exc.code, file=sys.stderr)
        return findings
    contexts = (prot.get("required_status_checks") or {}).get("contexts") or []
    if not contexts:
        return findings
    jobs = collect_job_names(workflow_dir)
    for ctx in contexts:
        if ctx not in jobs:
            findings.append(_finding(
                "C3", "branch protection (%s)" % branch, 0,
                "required check %r has no matching job in this repo" % ctx,
                "remove it, or point it at a job that exists",
            ))
    return findings


def scan(repo_path, repo):
    workflow_dir = os.path.join(repo_path, ".github", "workflows")
    if not os.path.isdir(workflow_dir):
        return None, []
    findings = []
    for fname in sorted(os.listdir(workflow_dir)):
        if not (fname.endswith(".yml") or fname.endswith(".yaml")):
            continue
        rel = ".github/workflows/" + fname
        findings.extend(check_file(os.path.join(workflow_dir, fname), rel))
    if repo:
        findings.extend(check_required(repo, workflow_dir))
    return workflow_dir, findings


def render_text(findings):
    counts = {}
    for f in findings:
        counts[f["check"]] = counts.get(f["check"], 0) + 1
    out = ["CI hygiene: %d finding(s) (%s)" % (
        len(findings),
        ", ".join("%s: %d" % kv for kv in sorted(counts.items())),
    )]
    for f in findings:
        out.append("%s:%s [%s/%s] %s | fix: %s" % (
            f["file"], f["line"] or "-", f["check"], f["severity"],
            f["message"], f["fix"],
        ))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit GitHub Actions workflows for CI hygiene issues.")
    parser.add_argument("repo_path", help="path to the repo to audit")
    parser.add_argument("--repo", default=None,
                        help="owner/name for the C3 required-check inventory")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.repo_path):
        print("error: not a directory: %s" % args.repo_path, file=sys.stderr)
        return 2
    workflow_dir, findings = scan(args.repo_path, args.repo)
    if workflow_dir is None:
        print("no .github/workflows found; nothing to audit")
        return 0
    if args.format == "json":
        print(json.dumps({"findings": findings}, indent=2))
    else:
        print(render_text(findings) if findings else "CI hygiene: clean")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

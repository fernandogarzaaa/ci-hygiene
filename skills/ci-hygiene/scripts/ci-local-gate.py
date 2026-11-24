#!/usr/bin/env python3
"""Local CI gate: the hygiene audit plus the repo's own checks, before push.

Runs, in order:
  1. The ci-hygiene audit (C1 full clones, C2 polling loops) on the repo.
  2. The repo's local checks: either the explicit list in
     .ci-hygiene.json, or auto-detected commands (pytest, npm test/lint,
     cargo test, go test, make test).

Exit code 0 means everything that ran passed, 1 means a finding or a
failed check, 2 means a usage error. A check whose tool is missing is
skipped with a note, never silently passed.

Install as a pre-push hook:
  ci-local-gate.py --install-hook [repo-path]
The hook runs the gate on every push. It refuses to overwrite an existing
hook unless --force is given.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER_PATH = HERE / "ci-hygiene.py"
CONFIG_NAME = ".ci-hygiene.json"

PYTEST_MARKERS = ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini")
SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
     ".eggs", "dist", "build"}
)


def _has_pytest_layout(root):
    """True when the repo looks like it has pytest tests.

    Root markers or a tests/ dir count directly. Otherwise walk two levels
    deep for a tests/ dir or test_*.py files, skipping hidden dirs and
    dependency trees, so skill-style layouts like skills/<name>/tests/
    are found too.
    """
    if any((root / m).exists() for m in PYTEST_MARKERS):
        return True
    if (root / "tests").is_dir():
        return True
    for dirpath, dirnames, filenames in os.walk(root):
        depth = Path(dirpath).relative_to(root).parts
        if len(depth) > 2:
            dirnames[:] = []
            continue
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
        ]
        if "tests" in dirnames:
            return True
        if any(f.startswith("test_") and f.endswith(".py") for f in filenames):
            return True
    return False


def _load_checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ci_hygiene", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detect_checks(repo_path):
    """Auto-detect the repo's local checks. Returns [(name, argv)]."""
    root = Path(repo_path)
    checks = []
    if _has_pytest_layout(root):
        checks.append(("pytest", [sys.executable, "-m", "pytest", "-q"]))
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts") or {}
        except (ValueError, OSError):
            scripts = {}
        if "test" in scripts:
            checks.append(("npm test", ["npm", "test"]))
        if "lint" in scripts:
            checks.append(("npm lint", ["npm", "run", "lint"]))
    if (root / "Cargo.toml").is_file():
        checks.append(("cargo test", ["cargo", "test"]))
    if (root / "go.mod").is_file():
        checks.append(("go test", ["go", "test", "./..."]))
    makefile = root / "Makefile"
    if makefile.is_file():
        try:
            has_test = any(
                line.startswith("test:") for line in makefile.read_text().splitlines()
            )
        except OSError:
            has_test = False
        if has_test:
            checks.append(("make test", ["make", "test"]))
    return checks


def load_config_checks(repo_path):
    """Read .ci-hygiene.json. Returns None when absent, else [(name, argv)].

    The file replaces auto-detection: when it exists, only its checks run.
    Raises ValueError on a malformed file.
    """
    path = Path(repo_path) / CONFIG_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        raw = data["checks"]
        checks = [(c["name"], shlex.split(c["run"])) for c in raw]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            "%s is malformed: %s (expected {\"checks\": "
            "[{\"name\": ..., \"run\": ...}]})" % (CONFIG_NAME, exc)
        )
    return checks


def run_check(name, argv, cwd):
    """Run one check. Returns (status, detail) with status in pass/fail/skip."""
    if shutil.which(argv[0]) is None:
        return "skip", "%s not found" % argv[0]
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, timeout=1800
    )
    if result.returncode == 0:
        return "pass", ""
    tail = "\n".join(
        (result.stdout + result.stderr).strip().splitlines()[-30:]
    )
    return "fail", tail


def install_hook(repo_path, force):
    """Write the gate as the repo's pre-push hook."""
    root = Path(repo_path).resolve()
    hooks_dir = root / ".git" / "hooks"
    if not hooks_dir.is_dir():
        print("error: %s is not a git repo (no .git/hooks)" % root, file=sys.stderr)
        return 2
    hook = hooks_dir / "pre-push"
    if hook.exists() and not force:
        print(
            "error: %s already exists; re-run with --force to replace it"
            % hook,
            file=sys.stderr,
        )
        return 2
    hook.write_text(
        "#!/bin/sh\n"
        "# Installed by ci-hygiene local gate. Runs the hygiene audit and\n"
        "# local checks before every push.\n"
        'exec %s %s %s\n'
        % (
            shlex.quote(sys.executable),
            shlex.quote(str(HERE / "ci-local-gate.py")),
            shlex.quote(str(root)),
        )
    )
    hook.chmod(0o755)
    print("installed pre-push hook: %s" % hook)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the CI hygiene audit and local checks before push."
    )
    parser.add_argument("repo_path", nargs="?", default=".",
                        help="path to the repo (default: current directory)")
    parser.add_argument("--install-hook", action="store_true",
                        help="install the gate as the repo's pre-push hook")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing pre-push hook")
    parser.add_argument("--skip-tests", action="store_true",
                        help="run the hygiene audit only, skip local checks")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.repo_path):
        print("error: not a directory: %s" % args.repo_path, file=sys.stderr)
        return 2
    if args.install_hook:
        return install_hook(args.repo_path, args.force)

    checker = _load_checker()
    workflow_dir, findings = checker.scan(args.repo_path, None)
    failed = False
    if workflow_dir is None:
        print("hygiene: no .github/workflows found; nothing to audit")
    elif findings:
        print("hygiene: FAIL %d finding(s)" % len(findings))
        print(checker.render_text(findings))
        failed = True
    else:
        print("hygiene: PASS")

    if args.skip_tests:
        return 1 if failed else 0
    try:
        checks = load_config_checks(args.repo_path)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    if checks is None:
        checks = detect_checks(args.repo_path)
    if not checks:
        print("checks: none detected (add .ci-hygiene.json to declare them)")
        return 1 if failed else 0
    for name, cmd in checks:
        status, detail = run_check(name, cmd, args.repo_path)
        if status == "pass":
            print("check %s: PASS" % name)
        elif status == "skip":
            print("check %s: SKIP (%s)" % (name, detail))
        else:
            print("check %s: FAIL" % name)
            if detail:
                print(detail)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

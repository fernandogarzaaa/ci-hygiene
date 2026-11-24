#!/usr/bin/env python3
"""Run a repo's GitHub Actions workflows locally with nektos/act.

Checks that `act` and Docker are available, then runs `act <event>` in
the repo, streaming output. Use it when the workflow itself must be
proven locally before it ever runs on GitHub, for example a new workflow
or a repo with no Actions history at all.

Exit code is act's exit code. 0 means the run passed, 2 means a usage
error or a missing prerequisite (act or Docker).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ACT_INSTALL = (
    "act is not installed. Install it, then re-run:\n"
    "  macOS:  brew install act\n"
    "  Linux:  curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash\n"
    "  Windows: choco install act-cli  (or: scoop install act)"
)
DOCKER_INSTALL = (
    "Docker is not running or not installed. act runs workflows in "
    "containers, so install Docker Desktop and start it, then re-run."
)


def check_prereqs():
    """Return True when act and Docker are usable, else print guidance."""
    if shutil.which("act") is None:
        print(ACT_INSTALL, file=sys.stderr)
        return False
    docker = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, timeout=60
    )
    if docker.returncode != 0:
        print(DOCKER_INSTALL, file=sys.stderr)
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run GitHub Actions workflows locally with act."
    )
    parser.add_argument("repo_path", nargs="?", default=".",
                        help="path to the repo (default: current directory)")
    parser.add_argument("--event", default="pull_request",
                        help="workflow event to simulate (default: pull_request)")
    parser.add_argument("--job", default=None,
                        help="run only this job")
    parser.add_argument("--list", action="store_true",
                        help="list the workflow jobs instead of running them")
    args, extra = parser.parse_known_args(argv)
    if not os.path.isdir(args.repo_path):
        print("error: not a directory: %s" % args.repo_path, file=sys.stderr)
        return 2
    if not Path(args.repo_path, ".github", "workflows").is_dir():
        print("no .github/workflows found; nothing to run")
        return 0
    if not check_prereqs():
        return 2
    cmd = ["act"]
    if args.list:
        cmd.append("--list")
    else:
        cmd.append(args.event)
        if args.job:
            cmd += ["--job", args.job]
    cmd += [a for a in extra if a != "--"]
    result = subprocess.run(cmd, cwd=args.repo_path)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

# ci-hygiene

A reusable skill that audits GitHub Actions workflows for three agent-era CI anti-patterns, then tells you how to fix them.

Public repo, MIT licensed, so you can reuse it across AI platforms. Copy `skills/ci-hygiene/` into your platform's skills directory and it loads as a skill.

## The three rules

1. **Shallow clones.** Most CI jobs only need the latest commit. Use `git clone --depth=1` or `fetch-depth: 1`. Fetch more history only when a job genuinely needs tags or ancestry.
2. **No polling.** Replace polling loops with native job dependencies (`needs:` chains), completion events (`workflow_run`), or webhooks. Cap concurrency and back off on rate limits.
3. **Careful required checks.** One flaky required check blocks everybody. Quarantine flaky tests with an owner and a deadline, and vet a test before it becomes required: run it repeatedly, run it in parallel, check shared data, hardcoded sleeps, and real services.

Inspired by [helpmeexitvi's reel](https://www.instagram.com/reel/DfckK-CguMf/) on what a CI lead would fix before giving everyone twenty agents.

## Layout

```text
skills/ci-hygiene/
  SKILL.md            the playbook: what to look for, the fixes, the vetting checklist
  skill.yaml          skill metadata
  scripts/ci-hygiene.py   stdlib-only checker (C1 and C2 mechanically, C3 via the GitHub API)
  tests/test_ci_hygiene.py
  fixtures/dirty/     workflow with all three anti-patterns
  fixtures/clean/     workflow that passes
```

## Install

The skill is one folder. Copy `skills/ci-hygiene/` into your platform's skills directory, then start a new session.

```sh
# OpenCode, global (every project)
cp -r skills/ci-hygiene ~/.config/opencode/skills/ci-hygiene

# OpenCode, project only
cp -r skills/ci-hygiene .opencode/skills/ci-hygiene

# Claude Code, global
cp -r skills/ci-hygiene ~/.claude/skills/ci-hygiene

# Claude Code, project only
cp -r skills/ci-hygiene .claude/skills/ci-hygiene
```

Other platforms that read the Agent Skills layout (`<name>/SKILL.md`) work the same way: copy the folder into their skills directory.

## Use

```sh
# Audit a repo's workflows (C1 and C2)
python3 skills/ci-hygiene/scripts/ci-hygiene.py <repo-path>

# Also inventory required checks from the GitHub API (C3)
GITHUB_TOKEN=<token> python3 skills/ci-hygiene/scripts/ci-hygiene.py <repo-path> --repo owner/name

# Machine-readable output
python3 skills/ci-hygiene/scripts/ci-hygiene.py <repo-path> --format json
```

Exit code 0 is clean, 1 is findings, 2 is a usage error. The checker is stdlib-only and scans workflow YAML heuristically, so read a flagged line in context before acting.

## Tests

```sh
python3 -m pytest skills/ci-hygiene/tests/ -q
```

## What it does not do

It never changes branch protection, required checks, or any repo setting. Those stay with the repo owner. It audits and proposes; a human applies the fix.

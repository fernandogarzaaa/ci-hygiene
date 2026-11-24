# CI hygiene

Audit a repo's GitHub Actions workflows for three agent-era CI anti-patterns, then fix what is found. Use this when CI is slow, the merge queue backs up, a tiny fix is blocked by an unrelated check, or before you wire agents to open PRs at volume.

The three rules come from a CI lead's field notes (helpmeexitvi, Nov 2026): shallow clones, no polling, and careful required checks. The checker script finds the first two mechanically. The third needs judgment plus the GitHub API, so the skill pairs a mechanical inventory with a human vetting checklist.

## The three checks

### C1. Full clones

- Most CI jobs only need the latest commit. A full clone downloads the whole history, including every agent-written commit message, on every run.
- Flag: `actions/checkout` with `fetch-depth: 0`, or a raw `git clone` with no `--depth`.
- Fix: `git clone --depth=1 <repo>`, or `fetch-depth: 1` on the checkout step. When a job genuinely needs more history, tags, or branch ancestry, fetch exactly what it needs instead of the whole history.
- Not a finding: `fetch-depth: 1` (the checkout default), or a full clone a job justifies in a comment, such as a release job that needs tags.

### C2. Polling loops

- A runner polling for another job is a computer paid to wait for a computer while a person waits for both. It also hammers the API and trips rate limits at agent volume.
- Flag: a run step that sleeps while asking GitHub whether something is done (`gh api` status checks, `gh run view`, `curl` against check-runs or commit status), a `while`/`until` loop around a sleep, or `gh run watch`.
- Fix: use native job dependencies (`needs:` chains in GitHub Actions). When the work happens outside CI, trigger on a completion event or webhook (`workflow_run`, `repository_dispatch`, `check_run`). Cap concurrency and back off when rate limits hit.
- Not a finding: a sleep that waits on an external service with no event available, when the step says so. Note it and move on.

### C3. Required checks

- One flaky required check blocks everybody. A tiny fix in one service must never wait on a failing integration suite in a service it never touched.
- The checker inventories the branch's required checks from the GitHub API (`--repo owner/name`, token from `GITHUB_TOKEN`). It flags required checks with no matching job in the repo (orphaned) and lists the rest for vetting.
- Quarantine a confirmed flaky test instead of leaving it required: keep it running separately, with an owner and a deadline. "We'll come back to it" is not a plan.
- Before a test becomes required, vet it against this list. Every item must pass:
  - Run it repeatedly. Flakes show up on repetition.
  - Run it in parallel with the other tests. A test that only passes alone stays alone.
  - Check for shared test data with other tests.
  - Check for hardcoded sleeps.
  - Check for real services it depends on.
- Changing branch protection is a human decision. The skill reports and proposes; it never changes required checks itself.

## Run the checker

```sh
python3 skills/ci-hygiene/scripts/ci-hygiene.py <repo-path>
python3 skills/ci-hygiene/scripts/ci-hygiene.py <repo-path> --repo owner/name
python3 skills/ci-hygiene/scripts/ci-hygiene.py <repo-path> --format json
```

Exit code 0 means clean, 1 means findings, 2 means a usage error. The script is stdlib-only and does a heuristic line scan of workflow YAML, so it finds patterns rather than parsing YAML. Read a flagged line in context before acting on it.

## Report the result

- Group findings by file and check. One line per finding: file, line, check, what is wrong, the fix.
- Lead with the count per check, then the findings. Name the files that are clean.
- Propose the fix as a diff or an exact edit, not a vague direction. Apply it only when the user asks, or when the task already authorized fixing.
- When a required check is flaky, name the test, its owner if known, and the quarantine proposal. Do not silently un-require it.

## What this skill does not do

- It does not change branch protection, required checks, or any repo setting. Those stay with the repo owner.
- It does not rewrite workflows on its own. It audits and proposes.
- It does not judge test quality beyond flakiness and scope. A slow but stable required check is a cost conversation, not a hygiene finding.

## Available connectors

- [GitHub](/opt/hatch/connectors/github/CONNECTOR.md) for reaching the repositories, runs and pull requests the findings belong to.

- [custom.legacy-github](~/workspace/custom-connectors/custom.legacy-github/CONNECTOR.md) for GitHub API operations the MCP server does not cover.

- Use `muse.connector_search` to find other connectors and `muse.skill_search` to find related skills.

- If nothing here fits, look on the web for a maintained API or MCP server and set it up with [Custom Connectors and MCPs](/opt/muse/skills/custom-connector-setup/SKILL.md), or build a custom connector.

## Alternatives for better results

- Pair this with the CI failure digest skill when a watcher reports failures: hygiene finds the structural cause, the digest names the incident.
- Run the checker in CI on itself. A workflow that audits workflows is the cheapest way to keep the three rules true.
- When a repo has no CI at all, say so in one line and stop. There is nothing to audit, and adding CI is a separate proposal, not a hygiene fix.

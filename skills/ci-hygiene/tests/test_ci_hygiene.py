"""Tests for the CI hygiene checker (skills/ci-hygiene/scripts/ci-hygiene.py).

Two layers: subprocess tests exercise the real CLI end to end, and
in-process tests import the module so coverage and mutation testing can
see the code under test.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-hygiene.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load_module():
    spec = importlib.util.spec_from_file_location("ci_hygiene", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(path, *extra):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *extra],
        capture_output=True, text=True, timeout=60,
    )
    return result


def checks_of(result):
    data = json.loads(result.stdout)
    return [f["check"] for f in data["findings"]]


# --- CLI end to end (subprocess) -------------------------------------------


def test_dirty_fixture_flags_all_three_categories():
    result = run_cli(FIXTURES / "dirty", "--format", "json")
    assert result.returncode == 1
    checks = checks_of(result)
    assert checks.count("C1") >= 2
    assert checks.count("C2") >= 2


def test_clean_fixture_passes():
    result = run_cli(FIXTURES / "clean")
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_missing_path_is_usage_error():
    result = run_cli(FIXTURES / "does-not-exist")
    assert result.returncode == 2


def test_repo_without_workflows_is_clean():
    result = run_cli(FIXTURES)
    assert result.returncode == 0
    assert "nothing to audit" in result.stdout


def test_shallow_clone_is_not_flagged(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n        with:\n"
        "          fetch-depth: 1\n"
        "      - run: git clone --depth=1 https://github.com/x/y.git\n"
    )
    result = run_cli(tmp_path)
    assert result.returncode == 0


def test_json_output_matches_text_count(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n        with:\n"
        "          fetch-depth: 0\n"
    )
    text = run_cli(tmp_path)
    js = run_cli(tmp_path, "--format", "json")
    assert text.returncode == 1
    assert len(json.loads(js.stdout)["findings"]) == 1
    assert json.loads(js.stdout)["findings"][0]["check"] == "C1"


# --- In-process (coverage-visible) ------------------------------------------


def test_check_file_dirty_returns_c1_and_c2():
    mod = _load_module()
    findings = mod.check_file(
        str(FIXTURES / "dirty" / ".github" / "workflows" / "ci.yml"),
        ".github/workflows/ci.yml",
    )
    checks = [f["check"] for f in findings]
    assert checks.count("C1") == 2
    assert checks.count("C2") == 2
    assert all(f["severity"] for f in findings)
    assert all(f["fix"] for f in findings)


def test_check_file_clean_returns_empty():
    mod = _load_module()
    findings = mod.check_file(
        str(FIXTURES / "clean" / ".github" / "workflows" / "ci.yml"),
        ".github/workflows/ci.yml",
    )
    assert findings == []


def test_check_file_flags_fetch_depth_zero_line():
    mod = _load_module()
    findings = mod.check_file(
        str(FIXTURES / "dirty" / ".github" / "workflows" / "ci.yml"),
        ".github/workflows/ci.yml",
    )
    c1 = [f for f in findings if f["check"] == "C1"]
    assert c1[0]["line"] == 9
    assert "fetch-depth" in c1[0]["message"]


def test_render_text_counts_and_formats():
    mod = _load_module()
    findings = mod.check_file(
        str(FIXTURES / "dirty" / ".github" / "workflows" / "ci.yml"),
        ".github/workflows/ci.yml",
    )
    text = mod.render_text(findings)
    assert "4 finding(s)" in text
    assert "C1: 2" in text
    assert "C2: 2" in text
    assert ".github/workflows/ci.yml:9" in text


def test_main_returns_expected_codes(capsys):
    mod = _load_module()
    assert mod.main([str(FIXTURES / "dirty")]) == 1
    assert mod.main([str(FIXTURES / "clean")]) == 0
    assert mod.main([str(FIXTURES / "does-not-exist")]) == 2
    assert mod.main([str(FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert "clean" in out
    assert "nothing to audit" in out


def test_main_json_format(capsys):
    mod = _load_module()
    assert mod.main([str(FIXTURES / "dirty"), "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert len(data["findings"]) == 4


def test_scan_without_workflows_returns_none(tmp_path):
    mod = _load_module()
    workflow_dir, findings = mod.scan(str(tmp_path), None)
    assert workflow_dir is None
    assert findings == []


def test_split_run_blocks_finds_block():
    mod = _load_module()
    lines = [
        "      - name: Wait",
        "        run: |",
        "          while true; do",
        "            sleep 30",
        "          done",
        "      - run: echo done",
    ]
    blocks = mod._split_run_blocks(lines)
    assert len(blocks) == 1
    assert "while true" in blocks[0][1]
    assert "sleep 30" in blocks[0][1]


def test_check_required_skips_without_token(monkeypatch, capsys):
    mod = _load_module()
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    findings = mod.check_required("owner/repo", "/nonexistent")
    assert findings == []
    assert "C3 skipped" in capsys.readouterr().err


def test_collect_job_names(tmp_path):
    mod = _load_module()
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n"
        "  test:\n    needs: build\n    runs-on: ubuntu-latest\n"
    )
    assert mod.collect_job_names(str(wf)) == {"build", "test"}
    assert mod.collect_job_names(str(tmp_path / "missing")) == set()


# --- Boundary and failure-path tests (mutation gate) ------------------------


def test_split_run_blocks_first_line_start_is_one():
    mod = _load_module()
    lines = ["run: |", "  sleep 30", "  echo done"]
    blocks = mod._split_run_blocks(lines)
    assert blocks == [(1, "  sleep 30\n  echo done")]


def test_split_run_blocks_start_line_is_exact():
    mod = _load_module()
    lines = ["jobs:", "  build:", "    run: |", "      echo hi"]
    blocks = mod._split_run_blocks(lines)
    assert blocks == [(3, "      echo hi")]


def test_split_run_blocks_at_end_of_file():
    mod = _load_module()
    lines = ["  run: |", "    echo hi"]
    blocks = mod._split_run_blocks(lines)
    assert blocks == [(1, "    echo hi")]


def test_split_run_blocks_blank_line_stays_in_body():
    mod = _load_module()
    lines = ["  run: |", "    echo a", "", "    echo b"]
    blocks = mod._split_run_blocks(lines)
    assert blocks == [(1, "    echo a\n\n    echo b")]


def test_split_run_blocks_line_at_same_indent_ends_block():
    mod = _load_module()
    lines = ["  run: |", "    echo a", "  next: x"]
    blocks = mod._split_run_blocks(lines)
    assert blocks == [(1, "    echo a")]
    assert "next: x" not in blocks[0][1]


def test_split_run_blocks_deeper_line_continues_block():
    mod = _load_module()
    lines = ["  run: |", "    echo a", "      echo deep"]
    blocks = mod._split_run_blocks(lines)
    assert blocks == [(1, "    echo a\n      echo deep")]


def test_check_file_sleep_only_is_not_polling(tmp_path):
    mod = _load_module()
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: Wait a little\n        run: |\n"
        "          sleep 30\n"
        "          echo done\n"
    )
    findings = mod.check_file(str(wf / "ci.yml"), ".github/workflows/ci.yml")
    assert [f for f in findings if f["check"] == "C2"] == []


def test_check_file_poll_only_is_not_polling(tmp_path):
    mod = _load_module()
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: One status read\n        run: |\n"
        "          gh api repos/x/y/commits/main/status --jq .state\n"
    )
    findings = mod.check_file(str(wf / "ci.yml"), ".github/workflows/ci.yml")
    assert [f for f in findings if f["check"] == "C2"] == []


def test_check_file_loop_plus_sleep_is_polling(tmp_path):
    mod = _load_module()
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: Wait loop\n        run: |\n"
        "          while true; do sleep 5; done\n"
    )
    findings = mod.check_file(str(wf / "ci.yml"), ".github/workflows/ci.yml")
    c2 = [f for f in findings if f["check"] == "C2"]
    assert len(c2) == 1
    assert "loop around a sleep" in c2[0]["message"]


def test_check_required_flags_orphan_and_keeps_matching(monkeypatch, tmp_path):
    mod = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    def fake_get(path, token):
        assert token == "fake-token"
        if path == "/repos/o/r":
            return {"default_branch": "main"}
        return {"required_status_checks": {"contexts": ["build", "ghost-check"]}}

    monkeypatch.setattr(mod, "_github_get", fake_get)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n"
    )
    findings = mod.check_required("o/r", str(wf))
    assert len(findings) == 1
    assert findings[0]["check"] == "C3"
    assert "ghost-check" in findings[0]["message"]
    assert "no matching job" in findings[0]["message"]


def test_check_required_api_error_returns_empty_and_reports(
    monkeypatch, capsys
):
    import urllib.error

    mod = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    def fake_get(path, token):
        raise urllib.error.HTTPError(path, 404, "Not Found", None, None)

    monkeypatch.setattr(mod, "_github_get", fake_get)
    findings = mod.check_required("o/r", "/nonexistent")
    assert findings == []
    assert "C3 skipped: GitHub API returned 404" in capsys.readouterr().err


def test_scan_with_repo_runs_c3_inventory(monkeypatch, tmp_path):
    mod = _load_module()
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    def fake_get(path, token):
        if path == "/repos/o/r":
            return {"default_branch": "main"}
        return {"required_status_checks": {"contexts": ["ghost-check"]}}

    monkeypatch.setattr(mod, "_github_get", fake_get)
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "name: CI\non: [push]\njobs:\n"
        "  build:\n    runs-on: ubuntu-latest\n"
    )
    _, findings = mod.scan(str(tmp_path), "o/r")
    assert [f for f in findings if f["check"] == "C3"] != []


def test_main_json_uses_two_space_indent(capsys):
    mod = _load_module()
    assert mod.main([str(FIXTURES / "dirty"), "--format", "json"]) == 1
    out = capsys.readouterr().out
    assert '\n  "findings"' in out
    assert '\n   "findings"' not in out

"""Tests for the CI hygiene checker (skills/ci-hygiene/scripts/ci-hygiene.py)."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-hygiene.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def run_checker(path, *extra):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), *extra],
        capture_output=True, text=True, timeout=60,
    )
    return result


def checks_of(result):
    data = json.loads(result.stdout)
    return [f["check"] for f in data["findings"]]


def test_dirty_fixture_flags_all_three_categories():
    result = run_checker(FIXTURES / "dirty", "--format", "json")
    assert result.returncode == 1
    checks = checks_of(result)
    assert checks.count("C1") >= 2
    assert checks.count("C2") >= 2


def test_clean_fixture_passes():
    result = run_checker(FIXTURES / "clean")
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_missing_path_is_usage_error():
    result = run_checker(FIXTURES / "does-not-exist")
    assert result.returncode == 2


def test_repo_without_workflows_is_clean():
    result = run_checker(FIXTURES)
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
    result = run_checker(tmp_path)
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
    text = run_checker(tmp_path)
    js = run_checker(tmp_path, "--format", "json")
    assert text.returncode == 1
    assert len(json.loads(js.stdout)["findings"]) == 1
    assert json.loads(js.stdout)["findings"][0]["check"] == "C1"

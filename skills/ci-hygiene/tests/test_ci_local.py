"""Tests for the local CI scripts (scripts/ci-local-gate.py, ci-local-act.py)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GATE = SCRIPTS / "ci-local-gate.py"
ACT = SCRIPTS / "ci-local-act.py"

CLEAN_WF = (
    "name: CI\non: [push]\njobs:\n"
    "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@v4\n"
    "      - run: npm run build\n"
)
DIRTY_WF = (
    "name: CI\non: [push]\njobs:\n"
    "  build:\n    runs-on: ubuntu-latest\n    steps:\n"
    "      - uses: actions/checkout@v4\n        with:\n"
    "          fetch-depth: 0\n"
)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path, workflow=CLEAN_WF, test_body="def test_ok():\n    assert True\n"):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(workflow)
    if test_body is not None:
        tests = tmp_path / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "test_ok.py").write_text(test_body)
    return tmp_path


# --- Gate -------------------------------------------------------------------


def test_gate_clean_repo_passes(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path)
    assert mod.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "hygiene: PASS" in out
    assert "check pytest: PASS" in out


def test_gate_dirty_workflow_fails(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path, workflow=DIRTY_WF)
    assert mod.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "hygiene: FAIL" in out
    assert "fetch-depth: 0" in out


def test_gate_failing_test_fails(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path, test_body="def test_ok():\n    assert False\n")
    assert mod.main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "check pytest: FAIL" in out


def test_gate_skip_tests_runs_hygiene_only(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path, workflow=DIRTY_WF)
    assert mod.main([str(tmp_path), "--skip-tests"]) == 1
    out = capsys.readouterr().out
    assert "hygiene: FAIL" in out
    assert "check pytest" not in out


def test_gate_no_checks_detected(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    assert mod.main([str(FIXTURES / "clean")]) == 0
    assert "none detected" in capsys.readouterr().out


def test_gate_config_replaces_detection(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path)
    (tmp_path / ".ci-hygiene.json").write_text(json.dumps(
        {"checks": [{"name": "echo", "run": "echo hello"}]}
    ))
    assert mod.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "check echo: PASS" in out
    assert "check pytest" not in out


def test_gate_malformed_config_is_usage_error(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path)
    (tmp_path / ".ci-hygiene.json").write_text("{not json")
    assert mod.main([str(tmp_path)]) == 2
    assert ".ci-hygiene.json is malformed" in capsys.readouterr().err


def test_gate_missing_tool_skips(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    _repo(tmp_path)
    (tmp_path / ".ci-hygiene.json").write_text(json.dumps(
        {"checks": [{"name": "nope", "run": "definitely-not-a-binary --x"}]}
    ))
    assert mod.main([str(tmp_path)]) == 0
    assert "check nope: SKIP" in capsys.readouterr().out


def test_gate_missing_path_is_usage_error(capsys):
    mod = _load(GATE, "ci_local_gate")
    assert mod.main([str(FIXTURES / "does-not-exist")]) == 2


def test_detect_checks_node_and_make(tmp_path):
    mod = _load(GATE, "ci_local_gate")
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": "jest", "lint": "eslint ."}}
    ))
    (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n")
    names = [n for n, _ in mod.detect_checks(str(tmp_path))]
    assert names == ["npm test", "npm lint", "make test"]


def test_install_hook_writes_executable_hook_and_runs(tmp_path, capsys):
    mod = _load(GATE, "ci_local_gate")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    hook = tmp_path / ".git" / "hooks" / "pre-push"
    assert mod.main([str(tmp_path), "--install-hook"]) == 0
    assert hook.is_file()
    assert os.access(hook, os.X_OK)
    assert "ci-local-gate.py" in hook.read_text()
    # Second install without --force refuses; with --force it replaces.
    assert mod.main([str(tmp_path), "--install-hook"]) == 2
    assert mod.main([str(tmp_path), "--install-hook", "--force"]) == 0
    # The installed hook runs the gate on this repo (no workflows, no checks).
    result = subprocess.run([str(hook)], capture_output=True, text=True)
    assert result.returncode == 0


def test_install_hook_without_git_is_usage_error(tmp_path):
    mod = _load(GATE, "ci_local_gate")
    assert mod.main([str(tmp_path), "--install-hook"]) == 2


# --- Act wrapper ------------------------------------------------------------


class _Proc:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def _fake_run_factory(calls, docker_rc=0, act_rc=0):
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "docker":
            return _Proc(docker_rc)
        return _Proc(act_rc)

    return fake_run


def test_act_missing_act_reports_install(tmp_path, monkeypatch, capsys):
    mod = _load(ACT, "ci_local_act")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    assert mod.main([str(tmp_path)]) == 2
    assert "brew install act" in capsys.readouterr().err


def test_act_missing_docker_reports_it(tmp_path, monkeypatch, capsys):
    mod = _load(ACT, "ci_local_act")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/local/bin/act")
    calls = []
    monkeypatch.setattr(
        mod.subprocess, "run", _fake_run_factory(calls, docker_rc=1)
    )
    assert mod.main([str(tmp_path)]) == 2
    assert "Docker is not running" in capsys.readouterr().err


def test_act_runs_event_and_job(tmp_path, monkeypatch):
    mod = _load(ACT, "ci_local_act")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/local/bin/act")
    calls = []
    monkeypatch.setattr(
        mod.subprocess, "run", _fake_run_factory(calls, docker_rc=0, act_rc=0)
    )
    assert mod.main([str(tmp_path), "--job", "test"]) == 0
    assert calls[-1] == ["act", "pull_request", "--job", "test"]


def test_act_failure_passes_through(tmp_path, monkeypatch):
    mod = _load(ACT, "ci_local_act")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/local/bin/act")
    calls = []
    monkeypatch.setattr(
        mod.subprocess, "run", _fake_run_factory(calls, docker_rc=0, act_rc=1)
    )
    assert mod.main([str(tmp_path)]) == 1


def test_act_list_passthrough(tmp_path, monkeypatch):
    mod = _load(ACT, "ci_local_act")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/local/bin/act")
    calls = []
    monkeypatch.setattr(
        mod.subprocess, "run", _fake_run_factory(calls, docker_rc=0, act_rc=0)
    )
    assert mod.main([str(tmp_path), "--list"]) == 0
    assert calls[-1] == ["act", "--list"]


def test_act_no_workflows_is_clean(tmp_path, capsys):
    mod = _load(ACT, "ci_local_act")
    assert mod.main([str(tmp_path)]) == 0
    assert "nothing to run" in capsys.readouterr().out


def test_act_missing_path_is_usage_error():
    mod = _load(ACT, "ci_local_act")
    assert mod.main([str(FIXTURES / "does-not-exist")]) == 2


def test_act_extra_args_pass_through(tmp_path, monkeypatch):
    mod = _load(ACT, "ci_local_act")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "/usr/local/bin/act")
    calls = []
    monkeypatch.setattr(
        mod.subprocess, "run", _fake_run_factory(calls, docker_rc=0, act_rc=0)
    )
    assert mod.main([
        str(tmp_path), "--", "--container-architecture", "linux/amd64",
    ]) == 0
    assert calls[-1] == [
        "act", "pull_request", "--container-architecture", "linux/amd64",
    ]


def test_detect_checks_finds_nested_tests_dir(tmp_path):
    mod = _load(GATE, "ci_local_gate")
    nested = tmp_path / "skills" / "foo" / "tests"
    nested.mkdir(parents=True)
    (nested / "test_x.py").write_text("def test_x():\n    assert True\n")
    names = [n for n, _ in mod.detect_checks(str(tmp_path))]
    assert names == ["pytest"]


def test_detect_checks_skips_venv(tmp_path):
    mod = _load(GATE, "ci_local_gate")
    venv_tests = tmp_path / ".venv" / "lib" / "tests"
    venv_tests.mkdir(parents=True)
    (venv_tests / "test_x.py").write_text("def test_x():\n    assert True\n")
    assert mod.detect_checks(str(tmp_path)) == []


def test_detect_checks_malformed_package_json(tmp_path):
    mod = _load(GATE, "ci_local_gate")
    (tmp_path / "package.json").write_text("{not json")
    names = [n for n, _ in mod.detect_checks(str(tmp_path))]
    assert names == []


def test_detect_checks_unreadable_makefile(tmp_path, monkeypatch):
    mod = _load(GATE, "ci_local_gate")
    (tmp_path / "Makefile").write_text("test:\n\tpytest -q\n")
    real_read = Path.read_text

    def raising(self, *args, **kwargs):
        if self.name == "Makefile":
            raise OSError("unreadable")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", raising)
    names = [n for n, _ in mod.detect_checks(str(tmp_path))]
    assert "make test" not in names


def test_detect_checks_pytest_marker(tmp_path):
    mod = _load(GATE, "ci_local_gate")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    names = [n for n, _ in mod.detect_checks(str(tmp_path))]
    assert names == ["pytest"]


def test_check_prereqs_returns_false_when_act_missing(monkeypatch):
    mod = _load(ACT, "ci_local_act")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    assert mod.check_prereqs() is False

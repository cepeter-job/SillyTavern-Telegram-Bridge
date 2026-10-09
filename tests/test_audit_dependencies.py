"""Dependency audit failures must leave complete evidence for every locked toolchain."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from source_test_support import workflow_documents

ROOT = Path(__file__).parents[1]
TARGETS = ["requirements.lock", "requirements-dev.lock", "tests/miniapp-ui", "tests/miniapp-browser"]
SCAN_IDS = ["python-runtime", "python-development", "npm-dom", "npm-browser"]
SCANNER = """
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
target = args[args.index('-r') + 1] if '-r' in args else args[args.index('--prefix') + 1]
expected = (['--strict', '--require-hashes', '--disable-pip', '-r', target] if '-r' in args
            else ['audit', '--prefix', target, '--include=dev', '--json'])
if args != expected:
    raise SystemExit(f'Unexpected scanner arguments: {args}')
with Path(os.environ['AUDIT_TEST_CALLS']).open('a') as stream:
    stream.write(json.dumps(target) + '\\n')
mode = os.environ.get('AUDIT_TEST_MODE', '') if target == 'requirements.lock' else ''
if mode == 'empty':
    raise SystemExit(0)
print(f'Scanner evidence for {target}', flush=True)
if mode == 'timeout':
    time.sleep(10)
if mode == 'findings':
    print('runtime advisory evidence ' * 1000)
    raise SystemExit(1)
if mode == 'error':
    print('advisory service unavailable', file=sys.stderr)
    raise SystemExit(2)
"""


@pytest.fixture
def audit_project(tmp_path):
    """Replace only the network-facing executables; run the real coordinator CLI."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python").symlink_to(sys.executable)
    npm = binaries / "npm"
    npm.write_text(f"#!{sys.executable}\n{SCANNER}", encoding="utf-8")
    npm.chmod(0o755)
    (tmp_path / "pip_audit.py").write_text(SCANNER, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        PATH=str(binaries),
        PYTHONPATH=str(tmp_path),
        AUDIT_TEST_CALLS=str(tmp_path / "calls.jsonl"),
    )
    return tmp_path, environment


def copy_coordinator(root):
    coordinator = ROOT / "tools/audit_dependencies.py"
    assert coordinator.is_file(), "the shared dependency audit coordinator is missing"
    (root / "tools").mkdir()
    shutil.copyfile(coordinator, root / "tools/audit_dependencies.py")


def run_audit(project, mode="", timeout="1"):
    root, environment = project
    copy_coordinator(root)
    environment = {**environment, "AUDIT_TEST_MODE": mode}
    replay = subprocess.run(
        [sys.executable, "tools/audit_dependencies.py", "--output-dir", "reports", "--timeout", timeout],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return replay, json.loads((root / "reports/summary.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("mode", "status", "exit_code"),
    [("findings", "failure", 1), ("error", "failure", 2), ("timeout", "timeout", None), ("empty", "error", 0)],
)
def test_first_scan_failure_still_audits_every_lockfile(audit_project, mode, status, exit_code):
    replay, summary = run_audit(audit_project, mode)
    root, _ = audit_project
    assert replay.returncode == 1, replay.stderr
    assert summary["success"] is False
    assert [scan["id"] for scan in summary["scans"]] == SCAN_IDS
    assert [scan["status"] for scan in summary["scans"]] == [status, "success", "success", "success"]
    assert summary["scans"][0]["exit_code"] == exit_code
    assert [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()] == TARGETS
    for scan, target in zip(summary["scans"], TARGETS, strict=True):
        assert (root / "reports" / scan["report"]).is_file()
        assert target in (root / "reports/summary.md").read_text(encoding="utf-8")
    if mode != "empty":
        assert "Scanner evidence for requirements.lock" in (root / "reports/python-runtime.txt").read_text()


def test_success_requires_results_from_all_four_scans(audit_project):
    replay, summary = run_audit(audit_project)
    assert replay.returncode == 0, replay.stderr
    assert summary["success"] is True
    assert [scan["status"] for scan in summary["scans"]] == ["success"] * 4
    assert [scan["exit_code"] for scan in summary["scans"]] == [0] * 4
    assert all(target in replay.stdout for target in TARGETS)


def test_missing_npm_records_both_errors_after_python_results(audit_project):
    root, _ = audit_project
    (root / "bin/npm").unlink()
    replay, summary = run_audit(audit_project)
    assert replay.returncode == 1, replay.stderr
    assert summary["success"] is False
    assert [scan["status"] for scan in summary["scans"]] == ["success", "success", "error", "error"]
    assert all(scan["error"] for scan in summary["scans"][2:])
    assert (root / "reports/npm-browser.txt").is_file()


def test_skipped_dependency_cannot_produce_a_clean_python_audit(audit_project):
    """Use the pinned pip-audit CLI/collector; replace only its advisory service query."""
    root, _ = audit_project
    (root / "pip_audit.py").unlink()
    for lockfile in ("requirements.lock", "requirements-dev.lock"):
        (root / lockfile).write_text(f"aiohttp==3.14.3 --hash=sha256:{'0' * 64}\n", encoding="utf-8")
    (root / "sitecustomize.py").write_text(
        "import sys\n"
        "if sys.orig_argv[1:3] == ['-m', 'pip_audit']:\n"
        "    from pip_audit._service import PyPIService, SkippedDependency\n"
        "    def query(self, spec):\n"
        "        return SkippedDependency(spec.name, 'fixture: package could not be audited'), []\n"
        "    PyPIService.query = query\n",
        encoding="utf-8",
    )
    replay, summary = run_audit(audit_project, timeout="5")
    evidence = (root / "reports/python-runtime.txt").read_text(encoding="utf-8")
    assert "fixture: package could not be audited" in evidence
    assert replay.returncode == 1, replay.stdout
    assert summary["success"] is False
    assert [scan["status"] for scan in summary["scans"]] == ["failure", "failure", "success", "success"]


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_cannot_disable_the_scan_bound(audit_project, timeout):
    root, environment = audit_project
    copy_coordinator(root)
    replay = subprocess.run(
        [sys.executable, "tools/audit_dependencies.py", "--timeout", timeout],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert replay.returncode == 2
    assert not (root / "calls.jsonl").exists()


@pytest.mark.parametrize(
    ("workflow", "job", "step_name", "report_directory"),
    [
        ("ci.yml", "dependency-audit", "Audit locked dependencies", "dependency-audit-reports"),
        ("scheduled-audit.yml", "advisory-audit", "Advisory dependency audit", "dependency-audit"),
    ],
)
def test_workflow_commands_audit_all_locks_and_publish_their_reports(
    audit_project, workflow, job, step_name, report_directory
):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("workflow shell replay requires Bash")
    root, environment = audit_project
    copy_coordinator(root)
    steps = workflow_documents()[workflow]["jobs"][job]["steps"]
    audit_step = next(step for step in steps if step.get("name") == step_name)
    replay = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", audit_step["run"]],
        cwd=root,
        env={**environment, "RUNNER_TEMP": str(root), "AUDIT_TEST_MODE": "findings"},
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert replay.returncode == 1, replay.stderr
    summary = json.loads((root / report_directory / "summary.json").read_text(encoding="utf-8"))
    assert [scan["target"] for scan in summary["scans"]] == TARGETS
    assert [scan["status"] for scan in summary["scans"]] == ["failure", "success", "success", "success"]
    artifact = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    upload_paths = artifact["with"]["path"].replace("${{ runner.temp }}", str(root)).splitlines()
    assert root / report_directory in [(root / path).resolve() for path in upload_paths]
    assert artifact["if"] == "${{ always() }}"
    assert artifact["with"]["retention-days"] == 14

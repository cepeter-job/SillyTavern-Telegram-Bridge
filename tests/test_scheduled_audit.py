"""Replay the actual scheduled workflow's issue routing without reaching GitHub."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest
from source_test_support import workflow_documents

SUMMARY = """# Dependency audit

| Scan | Target | Result | Exit code | Report |
|---|---|---|---|---|
| python-runtime | `requirements.lock` | failure | 1 | `python-runtime.txt` |
| python-development | `requirements-dev.lock` | success | 0 | `python-development.txt` |
| npm-dom | `tests/miniapp-ui` | success | 0 | `npm-dom.txt` |
| npm-browser | `tests/miniapp-browser` | success | 0 | `npm-browser.txt` |
"""


def replay_report(tmp_path, ref, *, dependency_outcome="success", summary=SUMMARY):
    bash = shutil.which("bash")
    node = shutil.which("node")
    if bash is None or node is None:
        pytest.skip("workflow predicate and shell replay requires Bash and Node")
    job = workflow_documents()["scheduled-audit.yml"]["jobs"]["advisory-audit"]
    predicate = job.get("if", "true").removeprefix("${{").removesuffix("}}").strip()
    evaluate = (
        "const vm = require('node:vm');"
        "const [condition, ref] = JSON.parse(process.argv[1]);"
        "console.log(vm.runInNewContext(condition, {github: {ref}}, {timeout: 1000}));"
    )
    allowed = (
        subprocess.run(
            [node, "-e", evaluate, json.dumps([predicate, ref])],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        == "true"
    )
    if not allowed:
        return False, None, []
    report_step = next(step for step in job["steps"] if step.get("name") == "Record findings in the advisory issue")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    gh = binaries / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "record = {'args': args}\n"
        "if '--body-file' in args:\n"
        "    record['body'] = Path(args[args.index('--body-file') + 1]).read_text()\n"
        "with Path(os.environ['AUDIT_GH_CALLS']).open('a') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "if args[0] == 'api': print('true')\n"
        "elif args[:2] == ['issue', 'list']: print('314')\n"
        "elif args[:2] not in (['issue', 'comment'], ['issue', 'close'], ['label', 'create']):\n"
        "    raise SystemExit('unexpected gh invocation')\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    reports = tmp_path / "dependency-audit"
    reports.mkdir()
    if summary is not None:
        (reports / "summary.md").write_text(summary, encoding="utf-8")
    (reports / "python-runtime.txt").write_text("first scanner evidence " * 1000, encoding="utf-8")
    (reports / "npm-browser.txt").write_text("last scanner evidence", encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        PATH=str(binaries) + os.pathsep + os.environ["PATH"],
        AUDIT_GH_CALLS=str(tmp_path / "gh.jsonl"),
        GH_TOKEN="test-placeholder",
        GITHUB_REF=ref,
        GITHUB_SHA="1" * 40,
        GITHUB_REPOSITORY="audit/example",
        RUNNER_TEMP=str(tmp_path),
        ADVISORY_LABEL="ci-advisory",
        DEPENDENCY_OUTCOME=dependency_outcome,
        LEAK_OUTCOME="success",
        SIZE_OUTCOME="success",
        RUN_URL="https://example.invalid/audit-run",
    )
    replay = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", report_step["run"]],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    records = [json.loads(line) for line in (tmp_path / "gh.jsonl").read_text().splitlines()]
    return True, replay, records


@pytest.mark.parametrize("ref", ["refs/heads/dependency-fix", "refs/tags/test-audit"])
def test_non_main_dispatch_cannot_close_the_main_issue(tmp_path, ref):
    allowed, replay, records = replay_report(tmp_path, ref)
    assert allowed is False
    assert replay is None
    assert records == []


def test_clean_main_run_can_resolve_the_existing_issue(tmp_path):
    allowed, replay, records = replay_report(
        tmp_path, "refs/heads/main", summary=SUMMARY.replace("| failure | 1 |", "| success | 0 |")
    )
    assert allowed is True
    assert replay.returncode == 0, replay.stderr
    assert any(record["args"][:3] == ["issue", "close", "314"] for record in records)


def test_issue_summary_retains_all_scan_results_after_long_first_failure(tmp_path):
    _, replay, records = replay_report(tmp_path, "refs/heads/main", dependency_outcome="failure")
    assert replay.returncode == 0, replay.stderr
    comment = next(record["body"] for record in records if record["args"][:2] == ["issue", "comment"])
    assert SUMMARY in comment
    assert "last scanner evidence" in comment
    assert not any(record["args"][:2] == ["issue", "close"] for record in records)


def test_missing_dependency_summary_cannot_resolve_the_issue(tmp_path):
    _, replay, records = replay_report(tmp_path, "refs/heads/main", summary=None)
    assert replay.returncode == 0, replay.stderr
    assert not any(record["args"][:2] == ["issue", "close"] for record in records)
    comment = next(record["body"] for record in records if record["args"][:2] == ["issue", "comment"])
    assert "summary is missing" in comment.lower()

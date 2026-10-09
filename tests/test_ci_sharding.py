"""CI partitions work without duplicating required checks or weakening coverage/browser scope."""

from source_test_support import workflow_documents, workflow_jobs


def test_ci_has_one_gate_and_four_real_pytest_shards():
    jobs = workflow_jobs()
    assert jobs["python-tests"][0] == "ci.yml"
    shard = jobs["python-test-shards"][1]
    assert shard["strategy"]["matrix"]["shard"] == [1, 2, 3, 4]
    assert shard["strategy"]["fail-fast"] is False
    command = next(step["run"] for step in shard["steps"] if step.get("name") == "Run pytest shard")
    before_pipe, after_pipe = command.split("| tee", 1)
    assert "python -X dev -W error::ResourceWarning -m pytest" in before_pipe
    assert "-p tools.pytest_shard" in before_pipe
    assert '--shard-index="$SHARD_INDEX" --shard-count=4' in before_pipe
    assert "--shard-manifest=shard-manifest.json" in before_pipe
    assert after_pipe.strip() == "pytest.log"
    upload = next(step for step in shard["steps"] if step.get("name") == "Publish pytest shard evidence")
    assert upload["if"] == "${{ always() }}"
    assert upload["with"]["include-hidden-files"] is True
    assert ".coverage.shard-" in upload["with"]["path"]


def test_combined_coverage_and_manifest_checks_gate_all_shards():
    job = workflow_documents()["ci.yml"]["jobs"]["python-tests"]
    assert job["needs"] == ["python-test-shards"]
    assert job["if"] == "${{ always() }}"
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "SHARD_RESULT" in scripts and "exit 1" in scripts
    assert "python tools/check_pytest_shards.py shard-reports --shards=4" in scripts
    assert "python -m coverage combine --keep shard-reports/pytest-shard-*" in scripts
    assert "python -m coverage report" in scripts and "--fail-under=0" not in scripts
    assert "python tools/check_security_coverage.py coverage.json" in scripts


def test_browser_engines_run_independently_without_dropping_dom_smoke():
    job = workflow_documents()["ci.yml"]["jobs"]["miniapp-smoke"]
    assert job["strategy"]["matrix"]["browser"] == ["chromium", "webkit"]
    assert job["strategy"]["fail-fast"] is False
    steps = job["steps"]
    dom = next(step for step in steps if step.get("name") == "Exercise Mini App pages and session safety")
    assert "npm test --prefix tests/miniapp-ui" in dom["run"]
    assert "tools/miniapp_ui_smoke.mjs" in dom["run"]
    browser = next(
        step for step in steps if step.get("name") == "Exercise real browser recovery and mobile interaction"
    )
    assert '--project="$BROWSER_ENGINE"' in browser["run"]
    assert browser["env"]["BROWSER_ENGINE"] == "${{ matrix.browser }}"

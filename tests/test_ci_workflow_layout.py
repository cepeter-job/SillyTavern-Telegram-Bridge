"""The CI entry files must keep the protected gate, its inputs and the registry.

`.github/workflows/README.md` records the check contract; these guards keep the
executable workflows, the required job set and that document in agreement so a
split, rename or added advisory file cannot silently change what protects `main`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from source_test_support import WORKFLOWS, job_text, workflow_documents, workflow_jobs, workflow_text

ROOT = Path(__file__).parents[1]
REGISTRY = WORKFLOWS / "README.md"
TOOLS = ROOT / "tools"
# The protected aggregate and the independent jobs it must wait for.
AGGREGATE = "test"
REQUIRED_JOBS = ("python-tests", "miniapp-smoke", "secret-scan", "dependency-audit", "static-analysis")
ADVISORY_JOBS = ("pr-title", "size-label", "advisory-audit")


def triggers(document: dict) -> dict:
    """Return the parsed ``on:`` block; PyYAML resolves the bare key as ``True``."""
    return document[True]


def test_every_declared_job_name_is_unique_across_entry_files():
    jobs = workflow_jobs()
    for job in (*REQUIRED_JOBS, AGGREGATE, *ADVISORY_JOBS):
        assert job in jobs, f"missing CI job: {job}"


def test_the_protected_aggregate_waits_for_every_required_job():
    aggregate = workflow_jobs()[AGGREGATE][1]
    assert sorted(aggregate["needs"]) == sorted(REQUIRED_JOBS)
    assert "tools/ci_gate.py" in job_text(AGGREGATE)
    # The checker rejects an incomplete dependency set, so its list cannot drift
    # away from the workflow's `needs`.
    import ast

    checker = ast.parse((TOOLS / "ci_gate.py").read_text(encoding="utf-8"))
    declared = [
        element.value
        for node in ast.walk(checker)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "REQUIRED_JOBS"
        for element in node.value.elts
    ]
    assert sorted(declared) == sorted(REQUIRED_JOBS)


def test_the_aggregate_evaluates_even_when_a_dependency_fails():
    aggregate = workflow_jobs()[AGGREGATE][1]
    assert aggregate["if"] == "${{ always() }}"


def test_summary_and_concurrency_infrastructure_is_shared_by_all_entry_files():
    documents = workflow_documents()
    for name, document in documents.items():
        assert document["permissions"].get("contents") == "read", name
        assert "concurrency" in document, name
        assert document["defaults"]["run"]["shell"] == "bash", name
        for job_name, job in document["jobs"].items():
            assert job.get("timeout-minutes"), (name, job_name)


def test_required_gate_workflow_reports_on_every_pull_request_without_path_filters():
    for name, document in workflow_documents().items():
        if not set(document["jobs"]) & set(REQUIRED_JOBS):
            continue
        declared = triggers(document)
        assert "pull_request" in declared, name
        assert declared.get("push", {}).get("branches") == ["main"], name
        for event in ("pull_request", "push"):
            event_config = declared.get(event) or {}
            assert isinstance(event_config, dict), (name, event)
            assert "paths" not in event_config and "paths-ignore" not in event_config, (name, event)


def test_privileged_pull_request_target_workflows_never_checkout_pull_request_code():
    privileged = [
        name for name, document in workflow_documents().items() if "pull_request_target" in triggers(document)
    ]
    assert privileged, "the size labeler is expected to be the privileged PR metadata workflow"
    for name in privileged:
        assert "actions/checkout" not in workflow_text(name), name
        assert "tools/pr_size_label.py" in workflow_text(name), name


def test_pr_size_labeler_can_write_labels_on_pull_requests():
    permissions = workflow_documents()["pr-size-labeler.yml"]["jobs"]["size-label"]["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["issues"] == "write"
    assert permissions["pull-requests"] == "write"


def test_module_size_ratchet_prefers_a_base_revision_that_carries_the_baseline():
    workflow = job_text("static-analysis")
    assert "github.event.pull_request.base.sha" in workflow
    assert "github.event.before" in workflow
    # A revision that predates tools/module_size_baseline.json would be measured
    # as today's caps, so the step checks the file before selecting the candidate.
    assert "tools/module_size_baseline.json" in workflow
    assert "rev-parse --verify --quiet" in workflow
    # Both branches of the step must call the ratchet: with a base revision, and
    # with the absolute caps alone when no usable revision exists.
    assert "check_module_sizes.py --base-ref" in workflow
    assert "No reviewed base revision is available" in workflow


def test_module_size_ratchet_uses_absolute_caps_when_no_candidate_has_baseline(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("behavioral workflow replay requires bash")

    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "check_module_sizes.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path('ratchet-args.txt').write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    git = shutil.which("git") or "git"

    def run_git(*args: str) -> str:
        result = subprocess.run([git, *args], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()

    run_git("init")
    run_git("config", "user.email", "ci-test@example.invalid")
    run_git("config", "user.name", "CI Test")
    run_git("add", ".")
    run_git("commit", "-m", "pre-baseline")
    base = run_git("rev-parse", "HEAD")
    (tmp_path / "head-marker.txt").write_text("head\n", encoding="utf-8")
    run_git("add", ".")
    run_git("commit", "-m", "head")
    head = run_git("rev-parse", "HEAD")

    static_analysis = workflow_documents()["ci.yml"]["jobs"]["static-analysis"]
    ratchet_step = next(
        step
        for step in static_analysis["steps"]
        if step.get("name") == "Ratchet Python module sizes against the reviewed base"
    )
    environment = dict(os.environ)
    environment.update({"PR_BASE_SHA": base, "PUSH_BEFORE_SHA": "", "GITHUB_SHA": head})
    replay = subprocess.run(
        [bash, "-c", ratchet_step["run"]],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert replay.returncode == 0, replay.stderr
    assert "No reviewed base revision is available" in replay.stdout
    assert (tmp_path / "ratchet-args.txt").read_text(encoding="utf-8") == ""


def test_miniapp_smoke_stays_a_required_job_with_its_locked_harness():
    smoke = job_text("miniapp-smoke")
    assert "node --experimental-vm-modules tools/miniapp_ui_smoke.mjs" in smoke
    assert "npm ci --prefix tests/miniapp-ui" in smoke
    assert "MINIAPP_JSDOM_ROOT" in smoke


@pytest.mark.parametrize(
    ("event_name", "actor", "head_repository", "licensed_expected"),
    [
        ("push", "cepeter", None, True),
        ("push", "dependabot[bot]", None, False),
        ("pull_request", "cepeter", "cepeter-job/SillyTavern-Telegram-Bridge", True),
        ("pull_request", "contributor", "contributor/SillyTavern-Telegram-Bridge", False),
        ("pull_request", "cepeter", "cepeter/SillyTavern-Telegram-Bridge", False),
        ("pull_request", "dependabot[bot]", "cepeter-job/SillyTavern-Telegram-Bridge", False),
    ],
)
def test_secret_scanning_routes_events_without_requiring_unavailable_secrets(
    event_name, actor, head_repository, licensed_expected
):
    """Fork/Dependabot checks must reach the secret-free full-history scan."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("workflow boolean predicate replay requires Node")
    github = {
        "event_name": event_name,
        "actor": actor,
        "repository": "cepeter-job/SillyTavern-Telegram-Bridge",
        "event": {"pull_request": {"head": {"repo": {"full_name": head_repository}}}},
    }
    steps = workflow_documents()["ci.yml"]["jobs"]["secret-scan"]["steps"]
    selected = [
        next(step for step in steps if step.get("uses", "").startswith("gitleaks/gitleaks-action@")),
        next(step for step in steps if step.get("name") == "Scan repository history for secrets"),
    ]
    # The workflow uses only boolean/string operators shared by GitHub and JS.
    # Evaluate its actual predicates against independent event fixtures.
    predicates = [step.get("if", "true").removeprefix("${{").removesuffix("}}").strip() for step in selected]
    program = (
        "const vm = require('node:vm');"
        "const [conditions, github] = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(conditions.map(condition => "
        "vm.runInNewContext(condition, {github}, {timeout:1000}))));"
    )
    replay = subprocess.run(
        [node, "-e", program, json.dumps([predicates, github])],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(replay.stdout) == [licensed_expected, True]


def test_playwright_cache_is_lock_scoped_without_skipping_browser_dependencies():
    jobs = workflow_documents()["ci.yml"]["jobs"]
    smoke_steps = jobs["miniapp-smoke"]["steps"]
    cache = next(step for step in smoke_steps if step.get("id") == "playwright-browsers-cache")
    assert cache["uses"] == "actions/cache@caa296126883cff596d87d8935842f9db880ef25"
    assert cache["with"]["path"] == "~/.cache/ms-playwright"
    key = cache["with"]["key"]
    assert "runner.os" in key and "runner.arch" in key
    assert "hashFiles('tests/miniapp-browser/package-lock.json')" in key
    assert "restore-keys" not in cache["with"]
    install = next(step for step in smoke_steps if step.get("name", "").startswith("Install pinned Chromium"))
    assert smoke_steps.index(cache) < smoke_steps.index(install)
    assert "if" not in install
    assert 'install --with-deps "$BROWSER_ENGINE"' in install["run"]
    assert install["env"]["BROWSER_ENGINE"] == "${{ matrix.browser }}"
    assert jobs["miniapp-smoke"]["strategy"]["matrix"]["browser"] == ["chromium", "webkit"]
    report = next(step for step in smoke_steps if step.get("name") == "Report browser cache hit")
    assert "steps.playwright-browsers-cache.outputs.cache-hit" in report["env"]["PLAYWRIGHT_CACHE_HIT"]
    node = next(step for step in smoke_steps if step.get("uses", "").startswith("actions/setup-node@"))
    assert node["with"]["cache"] == "npm"
    for name in ("python-tests", "miniapp-smoke", "dependency-audit", "static-analysis"):
        uv = next(step for step in jobs[name]["steps"] if step.get("uses", "").startswith("astral-sh/setup-uv@"))
        assert uv["with"]["enable-cache"] is True


def test_advisory_workflows_are_not_part_of_the_protected_gate():
    aggregate_needs = set(workflow_jobs()[AGGREGATE][1]["needs"])
    for job in ADVISORY_JOBS:
        assert job not in aggregate_needs, job
    for name in ("pr-title.yml", "pr-size-labeler.yml", "scheduled-audit.yml"):
        assert name in workflow_documents(), name


def test_workflow_registry_documents_every_entry_file_and_required_job():
    registry = REGISTRY.read_text(encoding="utf-8")
    for name in workflow_documents():
        assert f"`{name}`" in registry, name
    for job in (*REQUIRED_JOBS, AGGREGATE, *ADVISORY_JOBS):
        assert f"`{job}`" in registry, job


def test_workflow_registry_records_provenance_and_retirement_conditions():
    registry = REGISTRY.read_text(encoding="utf-8")
    assert "| Bespoke gate | Protected invariant | Origin | Retirement condition |" in registry
    for gate in (*REQUIRED_JOBS, *ADVISORY_JOBS):
        assert registry.count(f"`{gate}`") >= 2, gate
    assert "docs/audits/2026-10-07-ci-workflow-audit.md" in registry

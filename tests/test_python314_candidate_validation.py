"""PR500 candidate provenance stays narrow and independent of metadata hash quirks."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import warnings
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tools/python314_candidate_validation.py"
HEAD = "a" * 40
BASE = "b" * 40


@pytest.fixture
def candidate():
    assert HELPER.exists(), "Candidate validation helper is missing"
    spec = importlib.util.spec_from_file_location("candidate_validation", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def environment():
    return {
        "GITHUB_REPOSITORY": "cepeter-job/SillyTavern-Telegram-Bridge",
        "GITHUB_REPOSITORY_ID": "1369500317",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_ACTOR": "cepeter",
        "PR_NUMBER": "500",
        "EXPECTED_SHA": HEAD,
        "BASE_SHA": BASE,
    }


def pr_metadata():
    repository = {"id": 1369500317, "full_name": "cepeter-job/SillyTavern-Telegram-Bridge"}
    return {
        "number": 500,
        "state": "open",
        "mergeable": True,
        "head": {"sha": HEAD, "ref": "feat/python314-migration-20261010", "repo": repository.copy()},
        "base": {"sha": BASE, "ref": "main", "repo": repository.copy()},
    }


def reader(candidate, pr=None, base=None):
    values = {
        candidate.PR_URL: pr or pr_metadata(),
        candidate.BASE_URL: base or {"ref": "refs/heads/main", "object": {"sha": BASE, "type": "commit"}},
    }
    return lambda url: copy.deepcopy(values[url])


def test_exact_current_pr500_head_and_main_base_are_accepted(candidate):
    assert candidate.verify_pr(environment(), reader(candidate)) == (HEAD, BASE)


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_REPOSITORY", "fork/bridge"),
        ("GITHUB_REPOSITORY_ID", "42"),
        ("GITHUB_EVENT_NAME", "pull_request_target"),
        ("GITHUB_ACTOR", "someone-else"),
        ("PR_NUMBER", "503"),
        ("EXPECTED_SHA", "a" * 39),
        ("EXPECTED_SHA", "A" * 40),
        ("EXPECTED_SHA", HEAD + "\n"),
        ("BASE_SHA", "g" * 40),
    ],
)
def test_bad_event_fails_before_network(candidate, key, value):
    env = environment()
    env[key] = value
    with pytest.raises(ValueError):
        candidate.verify_pr(env, lambda url: pytest.fail("Unexpected network read: " + url))


@pytest.mark.parametrize(
    "side,key,value",
    [
        ("head", "sha", "c" * 40),
        ("head", "ref", "other"),
        ("base", "sha", "c" * 40),
        ("base", "ref", "staging"),
        ("head", "repo", {"id": 42, "full_name": "cepeter-job/SillyTavern-Telegram-Bridge"}),
        ("base", "repo", {"id": 1369500317, "full_name": "fork/bridge"}),
    ],
)
def test_changed_refs_or_untrusted_repository_fail(candidate, side, key, value):
    pr = pr_metadata()
    pr[side][key] = value
    with pytest.raises(ValueError):
        candidate.verify_pr(environment(), reader(candidate, pr=pr))


@pytest.mark.parametrize("key,value", [("number", 503), ("state", "closed"), ("mergeable", False)])
def test_wrong_closed_or_conflicting_pr_fails(candidate, key, value):
    pr = pr_metadata()
    pr[key] = value
    with pytest.raises(ValueError):
        candidate.verify_pr(environment(), reader(candidate, pr=pr))


def test_live_main_movement_fails_final_freshness(candidate):
    base = {"ref": "refs/heads/main", "object": {"sha": "c" * 40, "type": "commit"}}
    with pytest.raises(ValueError):
        candidate.verify_pr(environment(), reader(candidate, base=base))


def artifact(candidate):
    return {
        "id": candidate.ARTIFACT_ID,
        "name": "python-wheels-Linux-auto64",
        "expired": False,
        "digest": "sha256:" + candidate.ZIP_SHA256,
        "workflow_run": {
            "id": candidate.RUN_ID,
            "repository_id": 210299376,
            "head_repository_id": 1412742961,
            "head_sha": candidate.SOURCE_SHA,
            "head_branch": "fix/py314-pybind11-type-doc-allocator",
        },
    }


def test_exact_artifact_metadata_passes(candidate):
    candidate.verify_artifact(artifact(candidate))


@pytest.mark.parametrize(
    "key,value",
    [
        ("id", 1),
        ("name", "other"),
        ("expired", True),
        ("expired", None),
        ("digest", "sha256:" + "0" * 64),
    ],
)
def test_wrong_artifact_identity_fails(candidate, key, value):
    metadata = artifact(candidate)
    metadata[key] = value
    with pytest.raises(ValueError):
        candidate.verify_artifact(metadata)


@pytest.mark.parametrize("key", ["id", "repository_id", "head_repository_id", "head_sha", "head_branch"])
def test_wrong_upstream_provenance_fails(candidate, key):
    metadata = artifact(candidate)
    metadata["workflow_run"][key] = "wrong"
    with pytest.raises(ValueError):
        candidate.verify_artifact(metadata)


def archive(path, entries):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as zipped:
            for name, payload in entries:
                zipped.writestr(name, payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_extracts_only_exact_regular_wheel_without_path_traversal(candidate, tmp_path, monkeypatch):
    zipped = tmp_path / "artifact.zip"
    output = tmp_path / "verified"
    payload = b"inert regular wheel"
    digest = archive(
        zipped,
        [
            (candidate.WHEEL_NAME, payload),
            (candidate.WHEEL_NAME.replace("-cp314-cp314-", "-cp314-cp314t-"), b"threaded"),
            ("../unexpected", b"do not extract"),
        ],
    )
    monkeypatch.setattr(candidate, "ZIP_SHA256", digest)
    monkeypatch.setattr(candidate, "WHEEL_SHA256", hashlib.sha256(payload).hexdigest())
    wheel = candidate.extract_wheel(zipped, output)
    assert wheel.read_bytes() == payload
    assert list(output.iterdir()) == [wheel]
    assert not (tmp_path / "unexpected").exists()


@pytest.mark.parametrize("reason", ["zip-hash", "wheel-hash", "absent", "duplicate"])
def test_bad_archive_or_wheel_never_writes_output(candidate, tmp_path, monkeypatch, reason):
    zipped = tmp_path / "artifact.zip"
    output = tmp_path / "verified"
    entries = [(candidate.WHEEL_NAME, b"wrong")]
    if reason == "absent":
        entries = []
    if reason == "duplicate":
        entries *= 2
    digest = archive(zipped, entries)
    if reason != "zip-hash":
        monkeypatch.setattr(candidate, "ZIP_SHA256", digest)
    with pytest.raises(ValueError):
        candidate.extract_wheel(zipped, output)
    assert not output.exists()


def test_uv_empty_archive_info_still_requires_exact_bytes_and_origin(candidate, tmp_path, monkeypatch):
    wheel = tmp_path / "fixture.whl"
    wheel.write_bytes(b"inert fixture")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    monkeypatch.setattr(candidate, "WHEEL_SHA256", digest)
    metadata = {"url": wheel.as_uri(), "archive_info": {}}
    candidate.verify_installed_origin(metadata, wheel)
    for bad in [
        dict(metadata, url="https://elsewhere/fixture.whl"),
        dict(metadata, archive_info={"hashes": {"sha256": "0" * 64}}),
        dict(metadata, archive_info={"hashes": {"sha256": digest, "sha512": "wrong"}}),
        dict(metadata, archive_info={"hash": "sha256=" + "0" * 64}),
    ]:
        with pytest.raises(ValueError):
            candidate.verify_installed_origin(bad, wheel)
    wheel.write_bytes(b"changed")
    with pytest.raises(ValueError):
        candidate.verify_installed_origin(metadata, wheel)


@pytest.mark.parametrize(
    "payload",
    ["", "Fatal Python error: Aborted", "_PyMem_DebugRawFree: bad ID", "Segmentation fault", "Aborted (core dumped)"],
)
def test_native_crash_or_empty_log_is_rejected(candidate, tmp_path, payload):
    log = tmp_path / "native.log"
    log.write_text(payload)
    with pytest.raises(ValueError):
        candidate.check_logs([log])


def test_clean_log_passes_and_missing_log_fails(candidate, tmp_path):
    log = tmp_path / "native.log"
    log.write_text("3 passed\n")
    candidate.check_logs([log])
    with pytest.raises((ValueError, FileNotFoundError)):
        candidate.check_logs([tmp_path / "missing.log"])

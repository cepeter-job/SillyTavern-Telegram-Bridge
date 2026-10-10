"""Narrow provenance and evidence gates for PR500's unreleased CT2 experiment.

The official published-wheel workflow and production dependency locks stay separate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.request import Request, urlopen

REPOSITORY = "cepeter-job/SillyTavern-Telegram-Bridge"
REPOSITORY_ID = 1369500317
PR_NUMBER = 500
HEAD_BRANCH = "feat/python314-migration-20261010"
PR_URL = f"https://api.github.com/repos/{REPOSITORY}/pulls/{PR_NUMBER}"
BASE_URL = f"https://api.github.com/repos/{REPOSITORY}/git/ref/heads/main"
ARTIFACT_ID = 11660198517
RUN_ID = 38023794309
SOURCE_SHA = "3bce75d1523180128b6c6e23836baffa85211bcb"
ZIP_SHA256 = "100ce0d26c048742726cb2ceb5325d3ccc438190e7e92d3e67101a81fbbf428d"
WHEEL_SHA256 = "186f18a7204361767d5158d75f98d6b2a750d3fbebda44ca72432be6ceff9da6"
WHEEL_NAME = "ctranslate2-4.8.2-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
ARTIFACT_URL = f"https://api.github.com/repos/OpenNMT/CTranslate2/actions/artifacts/{ARTIFACT_ID}"
CRASH = re.compile(r"Fatal Python error:|_PyMem_DebugRawFree|Segmentation fault|Aborted \(core dumped\)")


def fetch_metadata(url: str) -> dict:
    """Read only the three fixed public metadata endpoints, without credentials."""
    if url not in {PR_URL, BASE_URL, ARTIFACT_URL}:
        raise ValueError("Unapproved metadata destination")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "pr500-pinned-candidate-validation"}
    request = Request(url, headers=headers)  # noqa: S310
    # The fixed allowlist above contains HTTPS GitHub API URLs only.
    with urlopen(request, timeout=25) as response:  # noqa: S310
        data = json.loads(response.read(1024 * 1024))
    if not isinstance(data, dict):
        raise ValueError("Unexpected metadata response")
    return data


def verify_pr(env: Mapping[str, str], read_json: Callable[[str], dict]) -> tuple[str, str]:
    """Reject other events, repositories, PRs, refs, or movement during a run."""
    expected = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REPOSITORY_ID": str(REPOSITORY_ID),
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_ACTOR": "cepeter",
        "PR_NUMBER": str(PR_NUMBER),
    }
    for key, value in expected.items():
        if env.get(key) != value:
            raise ValueError(f"Unapproved candidate event: {key}")
    head, base = env.get("EXPECTED_SHA", ""), env.get("BASE_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Head and base must be immutable full lowercase SHAs")
    pr = read_json(PR_URL)
    if type(pr.get("number")) is not int or pr["number"] != PR_NUMBER or pr.get("state") != "open":
        raise ValueError("Only open PR500 is in scope")
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        raise ValueError("PR has merge conflicts")
    for side, ref, sha in (("head", HEAD_BRANCH, head), ("base", "main", base)):
        data = pr.get(side) or {}
        repository = data.get("repo") or {}
        if (
            repository.get("full_name") != REPOSITORY
            or type(repository.get("id")) is not int
            or repository["id"] != REPOSITORY_ID
            or data.get("ref") != ref
            or data.get("sha") != sha
        ):
            raise ValueError(f"Untrusted or changed PR {side}")
    current = read_json(BASE_URL)
    if (
        current.get("ref") != "refs/heads/main"
        or (current.get("object") or {}).get("type") != "commit"
        or current["object"].get("sha") != base
    ):
        raise ValueError("Main moved since the reviewed event")
    return head, base


def verify_artifact(artifact: dict) -> None:
    """Require the exact approved upstream build, even after newer builds exist."""
    expected = {
        "id": ARTIFACT_ID,
        "name": "python-wheels-Linux-auto64",
        "expired": False,
        "digest": "sha256:" + ZIP_SHA256,
    }
    for key, value in expected.items():
        if type(artifact.get(key)) is not type(value) or artifact[key] != value:
            raise ValueError(f"Unapproved candidate artifact: {key}")
    expected_run = {
        "id": RUN_ID,
        "repository_id": 210299376,
        "head_repository_id": 1412742961,
        "head_sha": SOURCE_SHA,
        "head_branch": "fix/py314-pybind11-type-doc-allocator",
    }
    run = artifact.get("workflow_run") or {}
    for key, value in expected_run.items():
        if type(run.get(key)) is not type(value) or run[key] != value:
            raise ValueError(f"Unapproved build provenance: {key}")


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_wheel(archive: Path, destination: Path) -> Path:
    """Read one bounded root member only; never extract arbitrary archive paths."""
    if file_digest(archive) != ZIP_SHA256:
        raise ValueError("Candidate ZIP SHA-256 mismatch")
    with zipfile.ZipFile(archive) as zipped:
        entries = [entry for entry in zipped.infolist() if entry.filename == WHEEL_NAME]
        if len(entries) != 1 or entries[0].file_size > 100 * 1024 * 1024:
            raise ValueError("Expected exactly one bounded regular CPython3.14 wheel")
        wheel = zipped.read(entries[0])
    if hashlib.sha256(wheel).hexdigest() != WHEEL_SHA256:
        raise ValueError("Candidate wheel SHA-256 mismatch")
    destination.mkdir(parents=True, exist_ok=False)
    result = destination / WHEEL_NAME
    result.write_bytes(wheel)
    return result


def verify_installed_origin(provenance: dict, wheel: Path) -> None:
    """uv may omit optional metadata hashes; exact origin and actual bytes cannot be omitted."""
    if file_digest(wheel) != WHEEL_SHA256 or provenance.get("url") != wheel.resolve().as_uri():
        raise ValueError("Installed-source wheel digest or origin mismatch")
    info = provenance.get("archive_info")
    if not isinstance(info, dict):
        raise ValueError("Missing archive provenance")
    if "hashes" in info and info["hashes"] != {"sha256": WHEEL_SHA256}:
        raise ValueError("Unexpected optional archive hashes")
    if "hash" in info and info["hash"] != "sha256=" + WHEEL_SHA256:
        raise ValueError("Conflicting optional archive hash")


def native_probe(directory: Path) -> None:
    """Execute only in the isolated approved-candidate environment, under strict flags."""
    import ctranslate2
    import ctranslate2._ext
    import faster_whisper
    import numpy

    extension = Path(ctranslate2._ext.__file__).resolve()
    if not extension.is_relative_to(Path(sys.prefix).resolve()) or numpy.__version__ != "2.3.5":
        raise ValueError("Unexpected native extension location or NumPy version")
    if not callable(faster_whisper.WhisperModel):
        raise ValueError("Missing faster-whisper integration")
    raw = importlib.metadata.distribution("ctranslate2").read_text("direct_url.json")
    if raw is None:
        raise ValueError("Missing installed candidate origin")
    provenance = json.loads(raw)
    verify_installed_origin(provenance, directory / WHEEL_NAME)
    print(
        json.dumps(
            {
                "python": sys.version,
                "ctranslate2": ctranslate2.__version__,
                "numpy": numpy.__version__,
                "extension": str(extension),
                "direct_url": provenance,
                "wheel_sha256": WHEEL_SHA256,
            },
            indent=2,
        )
    )


def check_logs(paths: Sequence[Path]) -> None:
    """A zero pytest status cannot excuse a native worker crash at finalization."""
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip() or CRASH.search(text):
            raise ValueError(f"Missing or unsafe native evidence: {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("source", "artifact", "native", "logs"))
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    if args.mode == "source":
        head, base = verify_pr(os.environ, fetch_metadata)
        print(f"Verified PR500: head={head}, main={base}")
    elif args.mode == "artifact":
        if len(args.paths) != 2:
            parser.error("artifact needs ZIP and empty output directory paths")
        metadata = fetch_metadata(ARTIFACT_URL)
        verify_artifact(metadata)
        wheel = extract_wheel(*args.paths)
        provenance = {
            "artifact": metadata,
            "wheel": WHEEL_NAME,
            "wheel_sha256": WHEEL_SHA256,
            "official_release": False,
            "source_sha": SOURCE_SHA,
        }
        (wheel.parent / "candidate-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        print(f"Verified approved unreleased wheel SHA-256: {WHEEL_SHA256}")
    elif args.mode == "native":
        if len(args.paths) != 1:
            parser.error("native needs the verified wheel directory")
        native_probe(args.paths[0])
    else:
        if not args.paths:
            parser.error("logs needs at least one required log")
        check_logs(args.paths)


if __name__ == "__main__":
    main()

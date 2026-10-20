"""Temporary hosted-only dependency generation and bounded connector transfer."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

LOCKS = (
    "requirements.lock",
    "requirements-dev.lock",
    "requirements-py314.lock",
    "requirements-dev-py314.lock",
)
UPDATES = {
    "hindsight-client": "0.10.3",
    "tokenizers": "0.23.3",
    "ruff": "0.17.0",
    "uv": "0.13.0",
}
EVIDENCE = Path("dependency-evidence")


def pins(data):
    rows = re.findall(r"^([a-zA-Z0-9_.-]+)==([^\s;\\]+)", data.decode(), re.MULTILINE)
    result = {name.lower().replace("_", "-"): version for name, version in rows}
    assert len(result) == len(rows), "Duplicate pins"
    assert result, "Missing pins"
    return result


def identity(data):
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "git_blob": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
    }


def fetch(url, limit, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
        assert len(data) <= limit, "Response exceeds bound"
        return data, dict(response.headers.items())


def generate():
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert source == os.environ["GITHUB_SHA"], "Checkout is not the event commit"
    assert os.environ["GITHUB_REF"] == "refs/heads/chore/consolidate-dependencies-20261020"
    compiler = subprocess.check_output(["uv", "--version"], text=True).strip()
    assert compiler.split()[1] == "0.13.0", compiler
    EVIDENCE.mkdir()
    original = {name: Path(name).read_bytes() for name in LOCKS}
    commands = [
        [
            "uv", "pip", "compile", "requirements.txt", "--python-version", "3.11",
            "--generate-hashes", "--upgrade-package", "hindsight-client==0.10.3",
            "--upgrade-package", "tokenizers==0.23.3", "--output-file", LOCKS[0],
        ],
        [
            "uv", "pip", "compile", "requirements.txt", "requirements-dev.txt",
            "--constraint", LOCKS[0], "--python-version", "3.11", "--generate-hashes",
            "--upgrade-package", "ruff==0.17.0", "--upgrade-package", "uv==0.13.0",
            "--output-file", LOCKS[1],
        ],
        [
            "uv", "pip", "compile", "requirements.txt", "--python-version", "3.14",
            "--constraint", "config/python314-constraints.txt", "--generate-hashes",
            "--output-file", LOCKS[2],
        ],
        [
            "uv", "pip", "compile", "requirements.txt", "requirements-dev.txt",
            "--python-version", "3.14", "--constraint", "config/python314-constraints.txt",
            "--constraint", LOCKS[2], "--generate-hashes", "--output-file", LOCKS[3],
        ],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(command, capture_output=True, timeout=180, check=False)
        (EVIDENCE / f"compile-{index + 1}.log").write_bytes(result.stdout + result.stderr)
        assert result.returncode == 0, (command, result.stderr.decode()[-6000:])
    current = {name: Path(name).read_bytes() for name in LOCKS}
    files = {}
    for name in LOCKS:
        before, after = pins(original[name]), pins(current[name])
        assert before.keys() == after.keys(), f"Unexpected added or removed package in {name}"
        expected = {key: value for key, value in UPDATES.items() if key in before}
        changed = {key: value for key, value in after.items() if before[key] != value}
        assert changed == expected, (name, changed, expected)
        assert after["numpy"] == "2.3.5", name
        files[name] = {
            **identity(current[name]),
            "original_identity": identity(original[name]),
            "original_pins": before,
            "generated_pins": after,
            "changed_pins": changed,
        }
        (EVIDENCE / name).write_bytes(current[name])
    for runtime, development in ((LOCKS[0], LOCKS[1]), (LOCKS[2], LOCKS[3])):
        assert all(pins(current[development])[key] == value for key, value in pins(current[runtime]).items())
    for baseline, migration in ((LOCKS[0], LOCKS[2]), (LOCKS[1], LOCKS[3])):
        expected = pins(current[baseline])
        assert expected["ctranslate2"] == "4.8.2"
        expected["ctranslate2"] = "4.8.3"
        assert pins(current[migration]) == expected, "Cross-track drift"
    installer_url = "https://github.com/astral-sh/uv/releases/download/0.13.0/uv-installer.sh"
    installer, _ = fetch(installer_url, 200000)
    installer_hash = "283cbef4bdaca819bd896b0dacb8a085c4c05dbc880642aa5001d97bc4db74ba"
    assert hashlib.sha256(installer).hexdigest() == installer_hash, "Official uv installer hash mismatch"
    (EVIDENCE / "uv-installer.sh").write_bytes(installer)
    image_url = "https://mcr.microsoft.com/v2/playwright/manifests/v1.64.0-noble"
    media_types = (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
    image, headers = fetch(image_url, 100000, {"Accept": ", ".join(media_types), "Accept-Encoding": "identity"})
    normalized = {key.lower(): value for key, value in headers.items()}
    digest = "sha256:" + hashlib.sha256(image).hexdigest()
    assert normalized["docker-content-digest"] == digest, "Registry digest does not match bytes"
    index = json.loads(image)
    assert index["schemaVersion"] == 2 and index["mediaType"] in media_types
    assert any(
        entry.get("platform", {}).get("os") == "linux"
        and entry.get("platform", {}).get("architecture") == "amd64"
        for entry in index["manifests"]
    ), "No Linux amd64 image"
    (EVIDENCE / "playwright-index.json").write_bytes(image)
    (EVIDENCE / "playwright-headers.json").write_text(json.dumps(headers, sort_keys=True, indent=2) + "\n")
    receipt = {
        "source_sha": source,
        "compiler": compiler,
        "commands": commands,
        "files": files,
        "uv_installer": {"url": installer_url, **identity(installer)},
        "playwright": {"url": image_url, "digest": digest, "media_type": index["mediaType"], **identity(image)},
    }
    (EVIDENCE / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print("STTB_RECEIPT " + json.dumps(receipt, sort_keys=True, separators=(",", ":")), flush=True)
    subprocess.run(["git", "diff", "--check"], check=True, timeout=30)


def emit(name):
    assert name in LOCKS, "Unexpected transfer filename"
    receipt = json.loads((EVIDENCE / "receipt.json").read_text())
    assert receipt["source_sha"] == os.environ["GITHUB_SHA"], "Artifact source mismatch"
    data = (EVIDENCE / name).read_bytes()
    for key, value in identity(data).items():
        assert receipt["files"][name][key] == value, "Artifact byte identity mismatch"
    encoded = base64.b64encode(data).decode("ascii")
    chunks = [encoded[start:start + 8192] for start in range(0, len(encoded), 8192)]
    meta = {"name": name, "source_sha": receipt["source_sha"], "chunks": len(chunks), **identity(data)}
    print("STTB_BEGIN " + json.dumps(meta, sort_keys=True), flush=True)
    for index, chunk in enumerate(chunks):
        print(f"STTB_CHUNK {index + 1}/{len(chunks)} {len(chunk)} {chunk}", flush=True)
    print("STTB_END " + json.dumps(meta, sort_keys=True), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        generate()
    else:
        assert len(sys.argv) == 2
        emit(sys.argv[1])

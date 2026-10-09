"""The public-checksum exception must stay tied to one historical finding."""

from pathlib import Path


def test_public_evidence_hash_exception_is_an_exact_historical_fingerprint():
    commit = "9c6ed11f7a42f511dfc26936f816d739b62558ad"
    path = "docs/evidence/issue421-native-continuity-v1/evidence-manifest.json"
    expected = f"{commit}:{path}:generic-api-key:10"
    lines = (Path(__file__).resolve().parents[1] / ".gitleaksignore").read_text().splitlines()
    matches = [line for line in lines if line and not line.startswith("#") and path in line]
    assert matches == [expected]

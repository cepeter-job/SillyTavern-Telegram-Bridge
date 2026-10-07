"""Complete embedding caches with separate input provenance and current fact authority."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from story_memory_retrieval_fixture import require, sha256
from story_memory_retrieval_vectors import EmbeddingProfile, decode_vector, pack_vector

HAND_VECTORS = Path(__file__).resolve().parents[1] / "tests/fixtures/story_memory/retrieval_vectors_v1.json"


def json_hash(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def save_artifact(path: Path, artifact: dict) -> None:
    """Atomically keep reviewable results/manifests even if a later operation fails."""
    body = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".study-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_artifact(corpus, profile: EmbeddingProfile, vectors, *, source_identity: dict, acquisition=None) -> dict:
    require(len(vectors) == 66, "Embedding result must contain every fixed slot")
    bindings = corpus.fact_bindings()
    queries = {case.query_id: asdict(case) for case in corpus.cases}
    inputs = []
    for item, vector in zip(corpus.fixture["embedding_input_plan"]["inputs"], vectors, strict=True):
        blob, _ = pack_vector(vector, profile.dimensions)
        entry = {key: item[key] for key in ("index", "kind", "input_key")}
        entry.update(
            text_sha256=sha256(item["text"]),
            profile_id=profile.profile_id,
            vector_float32_le_base64=base64.b64encode(blob).decode("ascii"),
        )
        if item["kind"] == "fact":
            entry["fact_binding"] = bindings[item["input_key"]]
        else:
            entry["query_binding_sha256"] = json_hash(queries[item["input_key"]])
        inputs.append(entry)
    return {
        "schema_version": 1,
        "format": "normalized_float32_le_base64",
        "complete": True,
        "evidence_kind": profile.evidence_kind,
        "fixture_sha256": corpus.fixture_sha256,
        "profile": asdict(profile),
        "profile_sha256": json_hash(asdict(profile)),
        "source_identity": source_identity,
        "inputs": inputs,
        "acquisition": acquisition or {},
        "authority_reuse": "validate stable source bindings, then bind vectors to newly verified local facts",
    }


def _validate_authority(binding):
    require(isinstance(binding, dict) and set(binding) == {"stable", "authority"}, "Missing fact binding")
    authority = binding["authority"]
    require(type(authority["memory_id"]) is int and authority["memory_id"] > 0, "Missing observed fact identity")
    require(isinstance(authority["session_id"], str) and bool(authority["session_id"]), "Missing observed session")
    require(
        type(authority["session_created_at"]) in {float, int} and math.isfinite(authority["session_created_at"]),
        "Invalid observed incarnation",
    )
    evidence = authority["evidence"]
    require(
        evidence["memory_id"] == authority["memory_id"] and bool(evidence["source_document_id"]),
        "Observed evidence identity mismatch",
    )
    require(
        type(evidence["source_start_rowid"]) is int and evidence["source_start_rowid"] > 0,
        "Missing observed source row",
    )


def _validate_acquisition(artifact, profile, expected):
    identity, acquisition = artifact["source_identity"], artifact["acquisition"]
    require(
        identity["dirty"] is False and acquisition.get("mode") == "bounded_live_embeddings",
        "Genuine cache requires frozen source and genuine acquisition provenance",
    )
    require(bool(re.fullmatch(r"[0-9a-f]{64}", identity["source_sha256"])), "Missing captured source hash")
    endpoint = acquisition["endpoint"]
    url = endpoint["endpoint_url"]
    parsed = urlsplit(url)
    require(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None,
        "Invalid captured endpoint",
    )
    require(
        endpoint["endpoint_sha256"] == sha256(url)
        and endpoint["host"] == parsed.hostname
        and endpoint["scheme"] == parsed.scheme
        and endpoint["port"] == (parsed.port or (443 if parsed.scheme == "https" else 80)),
        "Endpoint identity changed",
    )
    require(
        endpoint["inference_boundary"] == "external_provider"
        and endpoint["transport"] == "aiohttp"
        and isinstance(endpoint["transport_version"], str)
        and bool(endpoint["transport_version"]),
        "Missing transport provenance",
    )
    require(
        profile.profile_id == sha256(f"{url}|{profile.model}|{profile.dimensions}|{profile.revision}")[:24],
        "Model profile does not bind the acquired endpoint",
    )
    require(
        acquisition["query_latency_boundary"] == "batched_acquisition_not_per_query_latency",
        "Embedding acquisition boundary changed",
    )
    batches = acquisition["batches"]
    require(isinstance(batches, list) and len(batches) == 5, "Missing complete acquisition batches")
    for batch, offset in zip(batches, range(0, 66, 16), strict=True):
        slots = expected[offset : offset + 16]
        require(
            batch["first_slot"] == offset
            and batch["input_count"] == len(slots)
            and batch["input_keys"] == [item["input_key"] for item in slots]
            and batch["status"] == "available"
            and batch["attempted"] is True,
            "Acquisition batch identity/completeness changed",
        )
        require(type(batch["elapsed_ns"]) is int and batch["elapsed_ns"] > 0, "Missing acquisition batch timing")
    require(
        type(acquisition["elapsed_ns"]) is int and acquisition["elapsed_ns"] > 0, "Missing acquisition elapsed time"
    )


def validate_embedding_artifact(artifact: dict, corpus) -> tuple[EmbeddingProfile, tuple[tuple[float, ...], ...]]:
    try:
        require(artifact["schema_version"] == 1 and artifact["complete"] is True, "Incomplete embedding artifact")
        require(artifact["format"] == "normalized_float32_le_base64", "Unsupported embedding encoding")
        require(artifact["fixture_sha256"] == corpus.fixture_sha256, "Embedding fixture hash mismatch")
        profile = EmbeddingProfile(**artifact["profile"])
        require(artifact["profile_sha256"] == json_hash(artifact["profile"]), "Embedding profile changed")
        require(artifact["evidence_kind"] == profile.evidence_kind, "Embedding evidence cannot be upgraded")
        identity = artifact["source_identity"]
        require(
            isinstance(identity, dict) and re.fullmatch(r"[0-9a-f]{40,64}", identity["revision"]),
            "Missing source revision",
        )
        require(type(identity["dirty"]) is bool, "Missing dirty-source status")
        expected = corpus.fixture["embedding_input_plan"]["inputs"]
        if profile.evidence_kind == "genuine_model_retrieval":
            _validate_acquisition(artifact, profile, expected)
        require(len(artifact["inputs"]) == len(expected) == 66, "Embedding slots incomplete")
        bindings = corpus.fact_bindings()
        queries = {case.query_id: asdict(case) for case in corpus.cases}
        result = []
        for got, wanted in zip(artifact["inputs"], expected, strict=True):
            require(all(got[key] == wanted[key] for key in ("index", "input_key", "kind")), "Embedding slot changed")
            require(got["text_sha256"] == sha256(wanted["text"]), "Embedding text hash mismatch")
            require(got["profile_id"] == profile.profile_id, "Mixed embedding profiles")
            if wanted["kind"] == "fact":
                _validate_authority(got["fact_binding"])
                require(
                    got["fact_binding"]["stable"] == bindings[wanted["input_key"]]["stable"],
                    "Fact content/provenance binding changed",
                )
            else:
                require(got["query_binding_sha256"] == json_hash(queries[wanted["input_key"]]), "Query scope changed")
            encoded = got["vector_float32_le_base64"]
            require(
                isinstance(encoded, str) and len(encoded) <= 4 * ((4 * profile.dimensions + 2) // 3),
                "Oversized vector encoding",
            )
            vector, norm = decode_vector(base64.b64decode(encoded, validate=True), profile.dimensions)
            require(math.isclose(norm, 1.0, rel_tol=2e-6), "Cached vector is not normalized float32")
            result.append(vector)
        return profile, tuple(result)
    except (KeyError, TypeError, binascii.Error) as exc:
        raise ValueError("Invalid embedding artifact structure") from exc


def load_embedding_artifact(path: Path, corpus):
    require(path.stat().st_size <= 8 * 1024 * 1024, "Embedding artifact exceeds bounded size")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    profile, vectors = validate_embedding_artifact(artifact, corpus)
    return artifact, profile, vectors


def hand_vectors(corpus):
    raw = json.loads(HAND_VECTORS.read_text(encoding="utf-8"))
    require(raw["evidence_kind"] == "contract_only", "Hand vectors cannot become semantic evidence")
    require(raw["fixture_sha256"] == corpus.fixture_sha256, "Hand-vector fixture mismatch")
    require(set(raw["fact_vectors"]) == set(corpus.facts), "Hand fact vectors incomplete")
    require(set(raw["query_vectors"]) == {case.query_id for case in corpus.cases}, "Hand query vectors incomplete")
    profile = EmbeddingProfile(
        sha256("hand-authored-contract-v1")[:24], "hand-authored-contract", 2, "1", "contract_only", "synthetic"
    )
    vectors = tuple(tuple(raw["fact_vectors"][key]) for key in corpus.facts)
    vectors += tuple(tuple(raw["query_vectors"][case.query_id]) for case in corpus.cases)
    for vector in vectors:
        pack_vector(vector, profile.dimensions)
    return profile, vectors

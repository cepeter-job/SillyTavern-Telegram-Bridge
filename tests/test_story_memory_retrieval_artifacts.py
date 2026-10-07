"""Complete cached embeddings retain input, model and local-authority provenance."""

import copy
import json
import sys
from pathlib import Path

import pytest
from test_story_memory_retrieval_comparison import corpus as corpus

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_complete_embedding_artifact_round_trips_and_binds_current_authority(corpus, tmp_path):
    from story_memory_retrieval_artifacts import build_artifact, save_artifact, validate_embedding_artifact
    from story_memory_retrieval_vectors import EmbeddingProfile

    profile = EmbeddingProfile("a" * 24, "fixture-model", 2, "r1", "contract_only", "synthetic")
    artifact = build_artifact(corpus, profile, [[1, 0]] * 66, source_identity={"revision": "b" * 40, "dirty": False})
    saved = tmp_path / "vectors.json"
    save_artifact(saved, artifact)
    loaded = json.loads(saved.read_text())
    validated, vectors = validate_embedding_artifact(loaded, corpus)
    assert validated == profile
    assert len(vectors) == 66
    assert len(loaded["inputs"][0]["vector_float32_le_base64"]) > 0
    assert loaded["inputs"][0]["fact_binding"]["authority"]["memory_id"] == corpus.facts["M01"].memory_id
    assert loaded["inputs"][22]["fact_binding"]["authority"]["memory_id"] == corpus.facts["A01"].memory_id
    assert loaded["inputs"][0]["fact_binding"]["stable"] != loaded["inputs"][22]["fact_binding"]["stable"]


@pytest.mark.parametrize("mutation", ["missing", "profile", "dimensions", "query", "binding", "vector", "upgrade"])
def test_incomplete_or_mixed_artifact_is_rejected(corpus, mutation):
    from story_memory_retrieval_artifacts import build_artifact, validate_embedding_artifact
    from story_memory_retrieval_vectors import EmbeddingProfile

    artifact = build_artifact(
        corpus,
        EmbeddingProfile("a" * 24, "fixture-model", 2, "r1", "contract_only", "synthetic"),
        [[1, 0]] * 66,
        source_identity={"revision": "b" * 40, "dirty": False},
    )
    bad = copy.deepcopy(artifact)
    if mutation == "missing":
        bad["inputs"].pop()
    elif mutation == "profile":
        bad["inputs"][1]["profile_id"] = "b" * 24
    elif mutation == "dimensions":
        bad["profile"]["dimensions"] = 3
    elif mutation == "query":
        bad["inputs"][42]["text_sha256"] = "c" * 64
    elif mutation == "binding":
        bad["inputs"][22]["fact_binding"] = bad["inputs"][0]["fact_binding"]
    elif mutation == "vector":
        bad["inputs"][0]["vector_float32_le_base64"] = "bad!!"
    else:
        bad["evidence_kind"] = "genuine_model_retrieval"
    with pytest.raises(ValueError):
        validate_embedding_artifact(bad, corpus)


def test_genuine_cache_requires_bound_endpoint_source_and_complete_acquisition(corpus):
    from story_memory_retrieval_artifacts import build_artifact, validate_embedding_artifact
    from story_memory_retrieval_vectors import EmbeddingProfile

    # A provenance label alone must not turn arbitrary vectors into a reusable genuine cache.
    artifact = build_artifact(
        corpus,
        EmbeddingProfile("a" * 24, "claimed-model", 2, "r1", "genuine_model_retrieval", "external_provider"),
        [[1, 0]] * 66,
        source_identity={"revision": "b" * 40, "dirty": False},
        acquisition={"mode": "bounded_live_embeddings"},
    )
    with pytest.raises(ValueError):
        validate_embedding_artifact(artifact, corpus)

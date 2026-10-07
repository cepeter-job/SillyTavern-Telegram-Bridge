"""Explicit, complete and bounded genuine embedding acquisition; no fallback calls."""

from __future__ import annotations

from time import perf_counter_ns

from story_memory_retrieval_artifacts import build_artifact, validate_embedding_artifact
from story_memory_retrieval_fixture import require, sha256
from story_memory_retrieval_http import BoundedHTTP, StudyHTTPError
from story_memory_retrieval_vectors import EmbeddingProfile, pack_vector


def embedding_client(endpoint: str, request_budget: int, *, token: str = "") -> BoundedHTTP:
    require(request_budget == 5, "The fixed 66-slot panel requires an explicit five-request embedding budget")
    return BoundedHTTP(
        endpoint, request_budget=request_budget, phase_limits={"embedding": 5}, deadline_seconds=120, token=token
    )


def fetch_embeddings(corpus, client, *, model: str, dimensions: int, revision: str, source_identity: dict) -> dict:
    require(source_identity.get("dirty") is False, "Live embeddings require frozen clean source")
    inputs = corpus.fixture["embedding_input_plan"]["inputs"]
    require(len(inputs) == 66 and all(0 < len(item["text"]) <= 1000 for item in inputs), "Fixed embedding input cap")
    require(sum(len(item["text"]) for item in inputs) <= 72000, "Embedding text cap")
    # Same URL|model|dimensions|revision namespace convention as embedding_values.
    profile = EmbeddingProfile(
        sha256(f"{client.endpoint}|{model}|{dimensions}|{revision}")[:24],
        model,
        dimensions,
        revision,
        "genuine_model_retrieval",
        "external_provider",
    )
    vectors, batches = [], []
    result = {
        "status": "failed",
        "artifact": None,
        "requested_input_count": len(inputs),
        "completed_input_count": 0,
        "failed_input_count": 0,
        "not_attempted_input_count": 0,
        "reason": "",
        "batches": batches,
        "endpoint": client.identity,
    }
    started = perf_counter_ns()
    client.begin()
    for offset in range(0, len(inputs), 16):
        batch = inputs[offset : offset + 16]
        observation = {
            "input_keys": [item["input_key"] for item in batch],
            "first_slot": offset,
            "input_count": len(batch),
            "status": "failed",
            "elapsed_ns": 0,
        }
        before = perf_counter_ns()
        attempted = False
        try:
            response = client.request(
                "POST", "", {"model": model, "input": [item["text"] for item in batch]}, phase="embedding"
            )
            attempted = True
            data = response.data or {}
            require(data.get("model", model) == model, "Embedding response model mismatch")
            items = data.get("data")
            require(isinstance(items, list) and len(items) == len(batch), "Incomplete embedding batch")
            mapped = {}
            for item in items:
                index = item["index"]
                require(
                    type(index) is int and 0 <= index < len(batch) and index not in mapped, "Invalid embedding index"
                )
                vector = item["embedding"]
                pack_vector(vector, dimensions)
                mapped[index] = vector
            require(len(mapped) == len(batch), "Incomplete embedding indices")
            vectors.extend(mapped[index] for index in range(len(batch)))
            result["completed_input_count"] += len(batch)
            observation["status"] = "available"
        except StudyHTTPError as exc:
            attempted = exc.attempted
            result["reason"] = exc.category
        except (ValueError, KeyError, TypeError):
            result["reason"] = "invalid_embedding_batch"
        finally:
            observation["elapsed_ns"] = perf_counter_ns() - before
            observation["attempted"] = attempted
            batches.append(observation)
        if observation["status"] != "available":
            result["failed_input_count"] = len(batch) if attempted else 0
            result["not_attempted_input_count"] = len(inputs) - len(vectors) - result["failed_input_count"]
            break
    result["acquisition_elapsed_ns"] = perf_counter_ns() - started
    result["requests"] = list(client.observations)
    if len(vectors) == 66:
        acquisition = {
            "mode": "bounded_live_embeddings",
            "endpoint": client.identity,
            "batches": batches,
            "elapsed_ns": result["acquisition_elapsed_ns"],
            "query_latency_boundary": "batched_acquisition_not_per_query_latency",
        }
        artifact = build_artifact(corpus, profile, vectors, source_identity=source_identity, acquisition=acquisition)
        validate_embedding_artifact(artifact, corpus)
        result.update(status="available", artifact=artifact)
    return result

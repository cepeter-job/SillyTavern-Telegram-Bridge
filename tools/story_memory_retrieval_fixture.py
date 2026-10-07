"""Frozen, predeclared retrieval inputs; this module never builds model answers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/story_memory/retrieval_v1.json"
DOCUMENT_KEYS = tuple([f"M{i:02}" for i in range(1, 23)] + [f"A{i:02}" for i in (*range(1, 17), 23, 24, 25, 26)])
QUERY_KEYS = tuple(f"Q{i:02}" for i in range(1, 25))


def sha256(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    text: str
    reader: str
    through_message_key: str
    session_key: str
    relevant_fact_keys: tuple[str, ...]
    forbidden_fact_keys: tuple[str, ...]
    category: str
    historical: bool


def validate_fixture(fixture: dict) -> None:
    try:
        facts, queries = fixture["facts"], fixture["queries"]
        require(fixture["schema_version"] == 1, "Unsupported fixture version")
        require(tuple(fact["key"] for fact in facts) == DOCUMENT_KEYS, "Document identity/order changed")
        require(tuple(fixture["document_order"]) == DOCUMENT_KEYS, "Invalid document order")
        require(tuple(query["query_id"] for query in queries) == QUERY_KEYS, "Query identity/order changed")
        for fact in facts:
            require(fact["session_key"] in {"main", "alternate"}, "Unknown fact session")
            require(fact["kind"] == "fact" and fact["importance"] == 0.9, "Invalid fact classification")
            require(isinstance(fact["summary"], str) and 0 < len(fact["summary"]) <= 1000, "Invalid summary length")
            require(fact["visibility"] in {"shared", "restricted"}, "Invalid visibility")
            require(set(fact["known_by"]) <= {"Rowan", "Mira"}, "Unknown audience")
            require(bool(fact["known_by"]) == (fact["visibility"] == "restricted"), "Ambiguous audience")
        for query in queries:
            scope, judgment = query["scope"], query["judgments"]
            require(scope["session_key"] in {"main", "alternate"}, "Unknown query session")
            require(scope["reader"] in {"Rowan", "Mira"} and scope["consumer"] == "character", "Invalid reader")
            require(scope["boundary_key"] in {"latest", "after16", "after13", "after14", "after15"}, "Invalid cutoff")
            require(scope["historical"] == (scope["boundary_key"] != "latest"), "Invalid historical scope")
            relevant, forbidden = judgment["relevant_fact_keys"], judgment["named_forbidden_fact_keys"]
            require(set(relevant + forbidden) <= set(DOCUMENT_KEYS), "Unknown judgment key")
            require(not set(relevant).intersection(forbidden), "Conflicting judgments")
            require(len(relevant) == len(set(relevant)), "Duplicate positive judgment")
            require(bool(relevant) == (query["query_id"] not in {"Q23", "Q24"}), "Invalid positive denominator")
            require(isinstance(query["text"], str) and 0 < len(query["text"]) <= 1000, "Invalid query length")
        inputs = fixture["embedding_input_plan"]["inputs"]
        expected = [("fact", f["key"], f["summary"]) for f in facts]
        expected += [("query", q["query_id"], q["text"]) for q in queries]
        require(len(inputs) == 66, "Expected exactly 66 embedding slots")
        for index, (item, wanted) in enumerate(zip(inputs, expected, strict=True)):
            require(item["index"] == index, "Embedding slot order changed")
            require((item["kind"], item["input_key"], item["text"]) == wanted, "Embedding text/binding changed")
        require(sum(len(f["summary"]) for f in facts) <= 48000, "Document text cap exceeded")
        require(sum(len(item["text"]) for item in inputs) <= 72000, "Embedding text cap exceeded")
        require(fixture["execution"]["branch_memory_status"] == "ready", "Branch must use local readiness")
    except (KeyError, TypeError) as exc:
        raise ValueError("Invalid fixture structure") from exc


def load_fixture(path: Path = FIXTURE) -> dict:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    validate_fixture(fixture)
    return fixture

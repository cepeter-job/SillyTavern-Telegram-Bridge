"""Persist ownership before mutation and never erase an ambiguous bank obligation."""

from __future__ import annotations

import uuid
from pathlib import Path

from story_memory_retrieval_artifacts import json_hash, save_artifact
from story_memory_retrieval_fixture import require
from story_memory_retrieval_http import StudyHTTPError


class OwnedBank:
    """Only freshly generated study IDs exist in this API; callers cannot choose one."""

    def __init__(self, client, fixture_sha256: str, manifest_path: Path, source_identity: dict):
        require(not manifest_path.exists(), "Ownership manifest path must be new")
        self.client, self.path = client, manifest_path
        nonce = uuid.uuid4().hex
        self.bank_id = f"story-memory-eval-{nonce}-{fixture_sha256[:12]}"
        self.synthetic_tag = "study:" + nonce
        self.route = "/v1/default/banks/" + self.bank_id
        self.items = {}
        self.manifest = {
            "schema_version": 1,
            "bank_id": self.bank_id,
            "fixture_sha256": fixture_sha256,
            "source_identity": source_identity,
            "endpoint": client.identity,
            "synthetic_tag": self.synthetic_tag,
            "state": "planned",
            "fresh_absence_observed": False,
            "creation_attempted": False,
            "creation_confirmed": False,
            "uncertain_creation": False,
            "uncertain_ingestion": False,
            "observations_disabled_verified": False,
            "cleanup_pending": False,
            "cleanup_absence_observed": False,
            "document_ids": [],
            "retained_batches": [],
            "last_mutation": None,
            "errors": [],
        }
        self.save()

    def save(self):
        self.manifest["request_counts"] = dict(self.client.phase_counts)
        self.manifest["requests"] = list(self.client.observations)
        save_artifact(self.path, self.manifest)

    def bind_documents(self, items):
        require(self.manifest["state"] == "planned" and len(items) == 42, "Invalid owned document inventory")
        require(len({item["document_id"] for item in items}) == 42, "Duplicate owned documents")
        require(
            all(
                0 < len(item["content"]) <= 1000
                and item["document_id"].startswith("session:")
                and self.synthetic_tag in item["tags"]
                and "native-fact" in item["tags"]
                for item in items
            ),
            "Only canonical synthetic fact documents may be retained",
        )
        require(sum(len(item["content"]) for item in items) <= 48000, "Retain text cap")
        self.items = {item["document_id"]: item for item in items}
        self.manifest["document_ids"] = list(self.items)
        self.manifest["document_input_sha256"] = json_hash(items)
        self.save()

    def open(self):
        require(len(self.items) == 42, "Owned document inventory must be bound before creation")
        self.client.begin()
        response = self.client.request("GET", self.route + "/config", phase="absence", acceptable=(200, 404))
        require(response.status == 404, "Generated bank was not proven absent")
        self.manifest.update(
            fresh_absence_observed=True,
            state="creating",
            creation_attempted=True,
            uncertain_creation=True,
            cleanup_pending=True,
            last_mutation={"kind": "create"},
        )
        self.save()
        try:
            self.client.request(
                "PUT", self.route, {"name": "story-memory-eval", "enable_observations": False}, phase="create"
            )
        except StudyHTTPError as exc:
            if not exc.attempted:
                self.manifest.update(creation_attempted=False, uncertain_creation=False, cleanup_pending=False)
            self.save()
            raise
        self.manifest.update(creation_confirmed=True, uncertain_creation=False, state="created")
        self.save()
        config = self.client.request("GET", self.route + "/config", phase="config").data or {}
        require(config.get("bank_id") == self.bank_id, "Owned bank configuration identity mismatch")
        require(
            isinstance(config.get("config"), dict) and isinstance(config.get("overrides"), dict), "Invalid bank config"
        )
        require(
            config["config"].get("enable_observations") is False
            and config["overrides"].get("enable_observations", False) is False,
            "Observations were not disabled",
        )
        self.manifest.update(observations_disabled_verified=True, state="configured")
        self.save()

    def retain(self, items):
        require(self.manifest["observations_disabled_verified"] and 0 < len(items) <= 12, "Invalid retain state/batch")
        require(all(self.items.get(item["document_id"]) == item for item in items), "Retain escaped owned corpus")
        self.manifest.update(
            state="ingesting",
            uncertain_ingestion=True,
            last_mutation={
                "kind": "retain",
                "document_ids": [item["document_id"] for item in items],
                "input_sha256": json_hash(items),
            },
        )
        self.save()
        try:
            response = self.client.request(
                "POST", self.route + "/memories", {"async": False, "items": items}, phase="retain"
            )
        except StudyHTTPError as exc:
            if not exc.attempted:
                self.manifest["uncertain_ingestion"] = False
            self.save()
            raise
        data = response.data or {}
        require(
            data.get("success") is True
            and data.get("bank_id") == self.bank_id
            and type(data.get("items_count")) is int
            and data["items_count"] == len(items)
            and data.get("async") is False,
            "Unconfirmed synchronous retain outcome",
        )
        usage = data.get("usage") or {}
        receipt = {
            "document_ids": [item["document_id"] for item in items],
            "input_sha256": json_hash(items),
            "elapsed_ns": response.elapsed_ns,
            "usage": {
                key: usage[key]
                for key in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "thoughts_tokens")
                if isinstance(usage, dict) and type(usage.get(key)) is int and usage[key] >= 0
            },
        }
        self.manifest["retained_batches"].append(receipt)
        self.manifest.update(uncertain_ingestion=False, state="ingested")
        self.save()

    def cleanup(self):
        if not self.manifest["fresh_absence_observed"] or not self.manifest["creation_attempted"]:
            self.save()
            return
        self.manifest.update(state="cleanup", cleanup_pending=True)
        self.save()
        try:
            self.client.request("DELETE", self.route, phase="cleanup", acceptable=(200, 204, 404))
        except StudyHTTPError as exc:
            self.manifest["errors"].append({"phase": "delete", "category": exc.category})
        try:
            response = self.client.request("GET", self.route + "/config", phase="cleanup", acceptable=(200, 404))
            self.manifest["cleanup_absence_observed"] = response.status == 404
        except StudyHTTPError as exc:
            self.manifest["errors"].append({"phase": "verify_absence", "category": exc.category})
        # A timeout/disconnect can leave a server mutation alive after an observed 404.
        uncertain = self.manifest["uncertain_creation"] or self.manifest["uncertain_ingestion"]
        pending = uncertain or not self.manifest["cleanup_absence_observed"]
        self.manifest.update(cleanup_pending=pending, state="cleanup_pending" if pending else "closed")
        self.save()

"""Offline adapters and production composition; no test helpers or live defaults.

Only external transport, Persona catalog and delivery are synthetic. SQLite,
session/turn acceptance, source jobs, extraction, indexing, scope and final
prompt assembly use their application implementations.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from contextlib import ExitStack
from dataclasses import asdict
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MARKER = re.compile(r"\[\[FACT\|([^|]+)\|([^|]*)\|([^|]+)\|FACT\]\]")
CHAT = "evaluation"


def marker_facts(source):
    """A complete marker is the sole input to this scripted classifier."""
    result = []
    for match in MARKER.finditer(source):
        readers = match.group(2).split(",") if match.group(2) else []
        result.append(
            {
                "kind": "fact",
                "importance": 0.9,
                "summary": match.group(3),
                "visibility": "restricted" if readers else "shared",
                "known_by": readers,
            }
        )
    return result[:6]


class OfflineTransport:
    def __init__(self, aliases, fault=""):
        self.aliases = aliases
        self.fault = fault
        self.reply = "A quiet moment."
        self.documents_by_id = {}
        self.documents = self
        self.calls = []
        self.extraction_sources = []
        self.story_payloads = []
        self.fail_retain = False
        self.native_summary = lambda document: None
        self.native_payloads = []
        self.after_retain = None
        self.extra_candidates = []

    def record(self, kind, payload):
        self.calls.append(
            {
                "kind": kind,
                "bytes": len(json.dumps(payload, ensure_ascii=False).encode()),
                "document_id": payload.get("document_id", ""),
            }
        )

    def generate(self, api_key, model, messages, **kwargs):
        self.record("provider", {"model": model, "messages": messages, "settings": kwargs.get("settings")})
        sid = kwargs.get("session_id", "")
        if sid.startswith("episodic:"):
            source = messages[-1]["content"]
            if self.fault == "drop-tail":
                source = MARKER.sub(lambda m: "" if m.group(1) == "tail" else m.group(0), source)
            self.extraction_sources.append(
                {
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "chars": len(source),
                    "summaries": [fact["summary"] for fact in marker_facts(source)],
                }
            )
            return json.dumps(marker_facts(source))
        self.story_payloads.append({"messages": messages, "settings": dict(kwargs.get("settings") or {})})
        return self.reply

    def retain(self, **kwargs):
        if "native-fact" not in kwargs.get("tags", []):
            raise AssertionError("Only native-fact remote retains are supported")
        accepted = self.native_summary(kwargs["document_id"])
        if accepted is None or accepted != kwargs["content"]:
            raise AssertionError("Retained content must be the exact locally accepted summary")
        self.native_payloads.append(
            {
                "category": "native_fact",
                "document_id": kwargs["document_id"][:256],
                "summary": kwargs["content"][:4000],
                "tags": [tag[:200] for tag in kwargs["tags"][:16]],
                "locally_accepted": True,
            }
        )
        self.record("retain", kwargs)
        if self.fail_retain:
            raise RuntimeError("Synthetic retain outage")
        self.documents_by_id[kwargs["document_id"]] = dict(kwargs)
        if self.after_retain:
            callback, self.after_retain = self.after_retain, None
            callback()

    def recall(self, **kwargs):
        from hindsight_client_api.models.recall_response import RecallResponse
        from hindsight_client_api.models.recall_result import RecallResult

        self.record("recall", kwargs)
        query = kwargs["query"].casefold()
        for alias, term in self.aliases.items():
            if alias in query:
                query += " " + term
        terms = set(re.findall(r"\w+", query))
        # Ranking is intentionally ignorant of audience/branch/as-of policy.
        ranked = sorted(
            self.documents_by_id.items(),
            key=lambda item: -len(terms.intersection(re.findall(r"\w+", item[1]["content"].casefold()))),
        )
        results = [
            RecallResult(id=doc, document_id=doc, text="UNTRUSTED REMOTE ENRICHMENT", type="world")
            for doc, value in ranked
            if "native-fact" in value["tags"] and terms.intersection(re.findall(r"\w+", value["content"].casefold()))
        ][:64]
        results += self.extra_candidates
        return RecallResponse(results=results)

    async def list_documents(self, *, bank_id, limit, offset, tags=None, q=None, **kwargs):
        values = [
            {"id": doc, "tags": list(value["tags"])}
            for doc, value in self.documents_by_id.items()
            if value["bank_id"] == bank_id
            and (not tags or set(tags).intersection(value["tags"]))
            and (not q or q in doc)
        ]
        return SimpleNamespace(items=values[offset : offset + limit], total=len(values))

    async def delete_document(self, **kwargs):
        self.record("delete", kwargs)
        self.documents_by_id.pop(kwargs["document_id"], None)

    def close(self):
        pass

    async def aclose(self):
        pass


class EvaluationRuntime:
    """Own temporary application state and fail-before-I/O guards."""

    def __init__(self, home: Path, fixture, fault=""):
        self.home = home
        self.transport = OfflineTransport(fixture["aliases"], fault)
        self.stack = ExitStack()
        self.unexpected_io = []
        self.mapping = {}
        self.claim_observations = []

    def unexpected(self, *args, **kwargs):
        self.unexpected_io.append("blocked-native-or-network")
        raise RuntimeError("Offline evaluation blocked unconfigured native/external I/O")

    def __enter__(self):
        try:
            return self._setup()
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _setup(self):
        # These modules are explicit-setting modules: import itself has no startup.
        from bridge import (
            codex_transport,
            group_core,
            memory,
            memory_artifact_store,
            memory_backend,
            memory_scope_runtime,
            memory_scope_store,
            npc_extraction,
            persona_sync,
            provider_transport,
            session_core,
        )
        from bridge.conversation_lifecycle import mark_started
        from bridge.group_service import GroupService
        from bridge.memory_service import MemoryService
        from bridge.metadata import set_meta
        from bridge.npc_service import NpcService
        from bridge.persona_service import PersonaService
        from bridge.provider_port import ProviderPort
        from bridge.rag_service import RagService
        from bridge.settings import load_app_settings
        from bridge.sqlite_store import db_connect

        # Install every consumed guard before constructing settings/session state.
        for owner, name, replacement in [
            (socket.socket, "connect", self.unexpected),
            (socket, "create_connection", self.unexpected),
            (persona_sync, "_native_settings", self.unexpected),
            (session_core, "default_persona_id", lambda **kwargs: ""),
            (session_core, "_native_default_world", lambda **kwargs: ""),
            (npc_extraction, "persona_name", lambda *args, **kwargs: "Synthetic User"),
            (provider_transport, "strict_urlopen", self.unexpected),
            (codex_transport, "resolve_access_token", self.unexpected),
            (memory_backend, "hindsight_client", lambda **kwargs: self.transport),
        ]:
            self.stack.enter_context(patch.object(owner, name, replacement))
        self.settings = load_app_settings(
            {"SILLYTAVERN_CONTEXT_WINDOW_TOKENS": "65536", "SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES": "96"},
            home=self.home,
        )
        self.provider = ProviderPort(self.transport.generate)
        self.persona = PersonaService(
            load_personas=lambda: {},
            load_default_persona=lambda: "",
            upsert_persona=self.unexpected,
            delete_persona=self.unexpected,
            update_session_persona=session_core.update_session,
            persona_reference_count=lambda *args: 0,
        )
        self.group = GroupService(
            load_state=group_core.group_state,
            save_state=group_core.save_group_state,
            user_turn_allowed_backend=group_core.group_user_turn_allowed,
            claim_user_turn_backend=group_core.claim_group_user_turn,
            pass_user_turn_backend=group_core.pass_group_user_turn,
            setup_state_backend=group_core.group_setup_state,
            character_option_label_backend=lambda path: "Synthetic",
            resolve_character_backend=lambda name: None,
            member_labels_backend=lambda names: names,
            current_speaker_backend=lambda *args: None,
            advance_turn_backend=group_core.advance_group_turn,
        )
        self.rag = RagService(
            retrieve_backend=lambda *args, **kwargs: [],
            add_backend=self.unexpected,
            reindex_backend=self.unexpected,
            documents_backend=lambda *args: [],
            versions_backend=lambda *args: [],
            activate_backend=self.unexpected,
            remove_backend=self.unexpected,
            coverage_backend=lambda *args: (0, 0),
            mode_backend=lambda *args: "off",
            context_limit=4000,
        )
        self.npc = NpcService()
        from bridge.memory_retirement_store import queue_session_memory_cleanup

        self.memory = MemoryService(
            queue_session_cleanup=queue_session_memory_cleanup,
            resolve_scope=partial(
                memory_scope_runtime.resolve_session_memory_scope,
                app_settings=self.settings,
                load_group_state=self.group.state,
            ),
            scoped_recall=partial(memory_backend.recall_scoped_memory, app_settings=self.settings),
            scoped_episodes=memory_scope_store.read_episodic_block,
            scoped_summary=memory_artifact_store.read_summary_block,
            scoped_scene=memory_artifact_store.read_scene_block,
            validate_blocks=memory_scope_store.validate_memory_blocks,
            summary_state=memory.get_session_summary,
            retain_session=partial(
                memory.retain_session_memory,
                provider_port=self.provider,
                app_settings=self.settings,
                persona_service=self.persona,
            ),
            purge_session_memory=partial(memory.purge_hindsight_session, app_settings=self.settings),
        )
        self.db = db_connect(app_settings=self.settings)
        self.transport.native_summary = self.native_summary
        self.session = session_core.create_session(
            self.db, CHAT, "offline::fixture", session_id="main", app_settings=self.settings
        )
        self.fields = dict.fromkeys(
            [
                "system_prompt",
                "description",
                "personality",
                "scenario",
                "mes_example",
                "first_mes",
                "post_history_instructions",
            ],
            "",
        )
        self.fields["name"] = "Rowan"
        self.fields["system_prompt"] = "Continue this synthetic survey story."
        self.fields["post_history_instructions"] = "Preserve accepted survey facts."
        mark_started(self.db, CHAT, "main", 0)
        set_meta(self.db, f"stream_mode:{CHAT}", "off")
        from bridge import message_commands

        # Delivery/media are external effects, not acceptance or assembly.
        for name in ("send_typing", "queue_user_quote_tts", "send_reply"):
            self.stack.enter_context(patch.object(message_commands, name, lambda *args, **kwargs: None))
        self.stack.enter_context(patch.object(message_commands, "telegram_request", self.unexpected))
        return self

    def __exit__(self, *args):
        if hasattr(self, "db"):
            self.db.close()
        self.stack.close()

    def accept_turn(self, user_text, assistant_text="A quiet moment."):
        from bridge.message_commands import generate_and_store_reply

        before = self.db.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]
        self.transport.reply = assistant_text
        generate_and_store_reply(
            self.db,
            "",
            "",
            self.fields,
            CHAT,
            user_text,
            self.session,
            "main",
            "offline::fixture",
            None,
            "",
            None,
            None,
            group_service=self.group,
            provider_port=self.provider,
            memory_service=self.memory,
            npc_service=self.npc,
            persona_service=self.persona,
            app_settings=self.settings,
            rag_service=self.rag,
        )
        rows = self.db.execute("SELECT id,role,content FROM messages WHERE id>? ORDER BY id", (before,)).fetchall()
        if len(rows) != 2:
            raise AssertionError("Production accepted-turn route did not persist both messages")
        return rows

    def due(self, layer):
        # Synthetic scheduler clock control only; never derived-answer seeding.
        self.db.execute("UPDATE memory_jobs SET next_attempt_at=0 WHERE layer=?", (layer,))
        self.db.commit()

    def native_summary(self, document_id):
        from bridge.memory_fact_store import index_fact_is_current

        stored = index_fact_is_current(self.db, document_id)
        return stored.fact.summary if stored else None

    def run_one(self, layer):
        from bridge.memory_store import claim_jobs
        from bridge.memory_workers import run_memory_claim

        self.due(layer)
        claims = claim_jobs(self.db, chat_id=CHAT, session_id="main", layers=(layer,))
        if not claims:
            return "idle"
        calls_before = len(self.transport.calls)
        result = run_memory_claim(
            self.db,
            claims[0],
            self.session,
            self.fields,
            provider_port=self.provider,
            app_settings=self.settings,
        )
        external_calls = sum(call["kind"] in {"provider", "retain"} for call in self.transport.calls[calls_before:])
        self.claim_observations.append({"layer": layer, "outcome": result, "external_calls": external_calls})
        if external_calls > 8:
            raise AssertionError("Production worker exceeded its eight-call budget")
        return result

    def drain(self, layer):
        outcomes = []
        for _ in range(200):
            outcome = self.run_one(layer)
            outcomes.append(outcome)
            if outcome in {"idle", "complete"}:
                return outcomes
            if outcome != "deferred":
                raise AssertionError(f"Unexpected {layer} outcome: {outcome}")
        raise AssertionError(f"Unbounded {layer} work")

    def context(self, query, reader="Rowan", through=None):
        return self.memory.prompt_context(
            self.db, CHAT, self.session, dict(self.fields, name=reader), query, through_rowid=through
        )

    def map_message(self, fixture_message, row):
        actual_id, role, content = row
        if role != fixture_message["role"]:
            raise AssertionError("Canonical role differs from fixture")
        shift = content.find(fixture_message["content"])
        if shift < 0:
            raise AssertionError("Canonical text no longer contains exact source marker")
        self.mapping[fixture_message["key"]] = {
            "row_id": actual_id,
            "session_id": "main",
            "session_created_at": self.db.execute(
                "SELECT created_at FROM sessions WHERE chat_id=? AND session_id='main'", (CHAT,)
            ).fetchone()[0],
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "fact_spans": [
                dict(fact, start_char=fact["start_char"] + shift, end_char=fact["end_char"] + shift)
                for fact in fixture_message["facts"]
            ],
        }

    def fact_mapping(self):
        from bridge.memory_fact_store import load_current_fact

        result = {}
        for key, message in self.mapping.items():
            for expected in message["fact_spans"]:
                rows = self.db.execute(
                    "SELECT memory_id FROM episodic_memories WHERE chat_id=? AND session_id='main' AND summary=?",
                    (CHAT, expected["summary"]),
                ).fetchall()
                matches = []
                for (mid,) in rows:
                    fact = load_current_fact(self.db, mid)
                    if fact and fact.evidence.source_start_rowid == message["row_id"]:
                        matches.append(
                            {
                                "memory_id": mid,
                                "evidence": asdict(fact.evidence),
                                "canonical_message_key": key,
                                "annotated_start_char": expected["start_char"],
                                "annotated_end_char": expected["end_char"],
                                "summary": expected["summary"],
                            }
                        )
                result[expected["key"]] = matches
        return result

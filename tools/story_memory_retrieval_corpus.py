"""Isolated production acceptance, ending closure, checkpoint clone and source projection."""

from __future__ import annotations

import socket
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from story_memory_eval_support import CHAT, EvaluationRuntime
from story_memory_retrieval_fixture import FIXTURE, QueryCase, load_fixture, require, sha256
from story_memory_retrieval_lifecycle import PanelTransport, close_original, prepare_checkpoint

from bridge.memory_fact_store import load_current_fact, load_source, normalize_principals
from bridge.memory_scope_store import eligible_fact, resolve_memory_scope


class RetrievalRuntime(EvaluationRuntime):
    def __init__(self, home):
        super().__init__(home, {"aliases": {}})
        self.transport = PanelTransport()

    def _setup(self):
        for owner, name in ((socket.socket, "connect_ex"), (socket, "getaddrinfo"), (socket.socket, "sendto")):
            self.stack.enter_context(patch.object(owner, name, self.unexpected))
        return super()._setup()


class RetrievalCorpus:
    """The generated application home is never a real story database."""

    def __init__(self, home: Path, fixture_path: Path = FIXTURE):
        self.fixture = load_fixture(fixture_path)
        self.fixture_sha256 = sha256(fixture_path.read_bytes())
        self.expected = {fact["key"]: fact for fact in self.fixture["facts"]}
        self.runtime = RetrievalRuntime(home)
        self.events = {}
        self.facts = {}
        self.sessions = {}
        self.cases = []
        self.scopes = {}

    def __enter__(self):
        self.runtime.__enter__()
        self.db = self.runtime.db
        self.sessions["main"] = self.runtime.session
        try:
            self._build()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        self.runtime.__exit__(*args)

    def transcript(self, session_key):
        return self.db.execute(
            "SELECT id,role,content FROM messages WHERE chat_id=? AND session_id=? ORDER BY id",
            (CHAT, self.sessions[session_key]["session_id"]),
        ).fetchall()

    def record_row(self, key, session_key, row_id, role, text):
        from bridge.roleplay_format import normalize_roleplay_transport
        from bridge.telegram_output import telegram_transport_output

        # Scripted provider prose is frozen; committed formatting remains production-owned.
        if role == "assistant":
            text = normalize_roleplay_transport(telegram_transport_output(text))
        row = self.db.execute(
            "SELECT role,content FROM messages WHERE chat_id=? AND session_id=? AND id=?",
            (CHAT, self.sessions[session_key]["session_id"], row_id),
        ).fetchone()
        require(row is not None and tuple(row) == (role, text), "Accepted source role/text mismatch")
        self.events[key] = {
            "session_key": session_key,
            "session_id": self.sessions[session_key]["session_id"],
            "row_id": row_id,
            "role": role,
            "content_sha256": sha256(text),
        }

    def accept(self, event_key, user_text, assistant_text, session_key="main"):
        rows = self.runtime.accept_turn(user_text, assistant_text, session=self.sessions[session_key])
        self.record_row(event_key + ".user", session_key, rows[0][0], "user", user_text)
        self.record_row(event_key + ".assistant", session_key, rows[1][0], "assistant", assistant_text)
        self.runtime.drain("episodes", session=self.sessions[session_key])

    def reconcile(self, key, row_id):
        from bridge.narrative_reconciliation import ensure_narrative_state_current
        from bridge.narrative_repository import load_narrative_clock

        ensure_narrative_state_current(
            self.db,
            "",
            CHAT,
            self.sessions["main"],
            through_rowid=row_id,
            provider_port=self.runtime.provider,
            app_settings=self.runtime.settings,
        )
        clock = load_narrative_clock(self.db, CHAT, "main")
        require(clock["updated_through_rowid"] == row_id, "Synthetic reconciliation did not reach captured boundary")
        self.events[key] = {"through_rowid": row_id, "state_revision": clock["state_revision"]}

    def _resolve_fact(self, key):
        expected = self.expected[key]
        session_id = self.sessions[expected["session_key"]]["session_id"]
        rows = self.db.execute(
            "SELECT memory_id FROM episodic_memories WHERE chat_id=? AND session_id=? AND summary=?",
            (CHAT, session_id, expected["summary"]),
        ).fetchall()
        candidates = [fact for row in rows if (fact := load_current_fact(self.db, row[0])) is not None]
        require(len(candidates) == 1, "Expected exactly one current canonical fact per key")
        stored = candidates[0]
        require(
            stored.fact.kind == expected["kind"] and stored.fact.importance == expected["importance"],
            "Fact extraction classification changed",
        )
        require(
            stored.fact.visibility == expected["visibility"]
            and stored.fact.known_by == normalize_principals(expected["known_by"]),
            "Fact audience changed",
        )
        source = load_source(self.db, stored.evidence.source_document_id)
        marker_key = expected["origin_fact_key"] or key
        require(
            source and source.role == "user" and self.marker(marker_key) in source.content,
            "Canonical fact lost its complete accepted marker",
        )
        require(
            stored.evidence.source_start_rowid == self.events[f"fact.{key}.user"]["row_id"],
            "Canonical fact source row is not the recorded event",
        )
        self.facts[key] = stored

    def marker(self, key):
        fact = self.expected[key]
        return f"[[FACT|{key}|{','.join(fact['known_by'])}|{fact['summary']}|FACT]]"

    def _accept_fact(self, key):
        self.accept(
            f"fact.{key}",
            self.marker(key),
            self.fixture["execution"]["acknowledgment"],
            self.expected[key]["session_key"],
        )
        self._resolve_fact(key)

    def _clone(self):
        from bridge.alternate_ending import create_alternate_ending
        from bridge.ending_service import load_ending_state

        self.main_transcript_before_branch = self.transcript("main")
        self.branch = create_alternate_ending(
            self.db,
            CHAT,
            "main",
            self.checkpoint.checkpoint_id,
            "retrieval-panel-v1-alternate",
            title="Aster — Alternate Ending",
            seed_memory=None,
        )
        require(self.branch.applied and self.branch.memory_status == "ready", "Branch local readiness failed")
        self.sessions["alternate"] = self.branch.session
        require(self.branch.session["session_id"] != "main", "Branch reused original identity")
        require(
            load_ending_state(self.db, CHAT, self.branch.session["session_id"]).lifecycle == "open",
            "Branch is not open",
        )
        original = [row for row in self.main_transcript_before_branch if row[0] <= self.checkpoint.through_rowid]
        copied = self.transcript("alternate")
        require(len(original) == len(copied), "Cloned transcript boundary changed")
        remap = {}
        for before, after in zip(original, copied, strict=True):
            require(tuple(before[1:]) == tuple(after[1:]) and before[0] != after[0], "Invalid transcript clone")
            remap[before[0]] = after[0]
        for clone in self.fixture["clone_mapping"]:
            origin_key, clone_key = clone["origin_fact_key"], clone["clone_fact_key"]
            for role in ("user", "assistant"):
                origin_row = self.events[f"fact.{origin_key}.{role}"]["row_id"]
                text = self.marker(origin_key) if role == "user" else self.fixture["execution"]["acknowledgment"]
                self.record_row(f"fact.{clone_key}.{role}", "alternate", remap[origin_row], role, text)
            self._resolve_fact(clone_key)
            origin, restored = self.facts[origin_key], self.facts[clone_key]
            require(
                origin.memory_id != restored.memory_id and origin.session_created_at != restored.session_created_at,
                "Clone reused origin attestation",
            )
            require(origin.payload_digest == restored.payload_digest, "Clone payload changed")
        self.branch_prefix_count = len([f for f in self.facts if f.startswith("A")])
        before = self._current_ids()
        self.runtime.drain("episodes", session=self.sessions["alternate"])
        require(self._current_ids() == before, "Restored source replay duplicated facts")

    def _current_ids(self):
        return {
            row[0]
            for row in self.db.execute("SELECT memory_id FROM episodic_memories")
            if load_current_fact(self.db, row[0]) is not None
        }

    def _build(self):
        for key in self.fixture["execution"]["prefix_order"]:
            self._accept_fact(key)
        require(len(self.facts) == 16, "Prefix fact count changed")
        self.checkpoint = prepare_checkpoint(self)
        for key in self.fixture["execution"]["main_post_order"]:
            self._accept_fact(key)
        self.ending_result = close_original(self)
        self._clone()
        for key in self.fixture["execution"]["alternate_post_order"]:
            self._accept_fact(key)
        require(
            self._current_ids() == {stored.memory_id for stored in self.facts.values()} and len(self.facts) == 42,
            "Canonical corpus must contain exactly the 42 planned facts",
        )
        require(self.main_transcript_before_branch == self.transcript("main"), "Alternate mutated original story")
        self.facts = {key: self.facts[key] for key in self.fixture["document_order"]}
        self._queries()

    def _queries(self):
        for query in self.fixture["queries"]:
            spec, judgment = query["scope"], query["judgments"]
            boundary = spec["boundary_key"]
            event = "" if boundary == "latest" else f"fact.M{int(boundary[5:]):02}.assistant"
            scope = resolve_memory_scope(
                self.db,
                CHAT,
                self.sessions[spec["session_key"]],
                {"name": spec["reader"]},
                through_rowid=self.events[event]["row_id"] if event else None,
                historical=spec["historical"],
            )
            require(scope is not None, "Missing canonical query scope")
            self.scopes[query["query_id"]] = scope
            eligible = {key for key, stored in self.facts.items() if eligible_fact(self.db, scope, stored.memory_id)}
            forbidden = tuple(key for key in self.facts if key not in eligible)
            require(set(judgment["relevant_fact_keys"]) <= eligible, "Predeclared positive is ineligible")
            require(set(judgment["named_forbidden_fact_keys"]) <= set(forbidden), "Named forbidden fact is eligible")
            self.cases.append(
                QueryCase(
                    query["query_id"],
                    query["text"],
                    spec["reader"],
                    event,
                    spec["session_key"],
                    tuple(judgment["relevant_fact_keys"]),
                    forbidden,
                    query["category"],
                    spec["historical"],
                )
            )

    def eligible(self, case):
        return {
            key: current
            for key, stored in self.facts.items()
            if (current := eligible_fact(self.db, self.scopes[case.query_id], stored.memory_id)) is not None
        }

    def fact_bindings(self):
        result = {}
        for key, stored in self.facts.items():
            source = load_source(self.db, stored.evidence.source_document_id)
            require(source is not None, "Missing canonical source")
            result[key] = {
                "stable": {
                    "session_key": self.expected[key]["session_key"],
                    "origin_fact_key": self.expected[key]["origin_fact_key"],
                    "payload_digest": stored.payload_digest,
                    "source_content_sha256": sha256(source.content),
                    "start_offset": source.start_offset,
                    "end_offset": source.end_offset,
                    "provenance_kind": stored.provenance_kind,
                },
                "authority": {
                    "memory_id": stored.memory_id,
                    "session_id": stored.session_id,
                    "session_created_at": stored.session_created_at,
                    "evidence": asdict(stored.evidence),
                },
            }
        return result

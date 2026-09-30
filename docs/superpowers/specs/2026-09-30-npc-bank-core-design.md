# NPC Bank Core Design

Date: 2026-09-30
Status: Approved design, pending implementation-plan review
Target branch: feature/npc-bank-core
Target PR: #267

## Purpose

Add a first-class, session-scoped NPC subsystem to SillyTavern Telegram Bridge. It preserves durable supporting-character state independently from SillyTavern character cards, Scene State, Hindsight, episodic memory, and continuity summaries.

The subsystem supports structured fixed and mutable fields, field-level history, reversible edits, explicit knowledge boundaries, bounded prompt context, and background Utility-model extraction without delaying foreground generation.

## Goals

1. Persistent NPC identities per bridge session.
2. Structured fields with fixed or mutable behavior.
3. Full before/after history for every real mutation.
4. Asynchronous extraction after retained turns.
5. Historical as-of context during edit/regeneration.
6. Rollback of edited-away NPC state.
7. Shared/restricted visibility with explicit known_by.
8. Bounded relevant NPC prompt context.
9. SillyTavern cards remain canonical for primary characters.
10. One NpcService reused by Telegram and Mini App.

## Scope and Architecture

PR #267 adds only the NPC core domain. It does not add ComfyUI portraits, Booru tags, arbitrary field-schema editing, external NPC-bank import, a new vector database, Telegram management panels, or Mini App management UI. Telegram management is planned for PR #268; Mini App management for PR #269.

The NPC subsystem is a dedicated application domain parallel to MemoryService, RagService, GroupService, and GroupDirectorService. It must not be embedded inside Scene State, episodic memory, continuity summary, or native SillyTavern character cards because each has different ownership and lifecycle semantics.

Flow:

assistant turn committed
-> post-retain extension hook
-> background Utility-model extraction
-> validated NPC operations
-> NpcService/repository transaction
-> entities, fields, history
-> bounded NPC prompt context

NpcService owns orchestration. SQL belongs in repository modules. Utility-model parsing belongs in an extraction module. Prompt rendering remains separate from persistence.

## Persistence Model

Migration 6 creates new tables only and preserves the repository's no-ALTER-TABLE upgrade policy.

npc_entities stores npc_id, chat_id, session_id, canonical_name, display_name, aliases_json, first_seen_rowid, last_seen_rowid, created_at, and updated_at. The unique identity is chat_id + session_id + canonical_name.
npc_fields stores npc_id, field_key, value_json, field_mode, visibility, known_by_json, updated_rowid, and updated_at. The primary key is npc_id + field_key. field_mode is fixed or mutable. visibility is shared or restricted, and restricted fields require non-empty known_by.

npc_field_history stores change_id, npc_id, field_key, operation, complete before/after value/mode/visibility/known_by state, source_rowid, and created_at. Allowed operations are set, append, and remove. No-op changes create no history.

Initial fixed fields: appearance, voice, background, canon.

Initial mutable fields: role, location, agenda, relationship, mood, secrets, status.

Fixed fields are write-once for automatic extraction once non-empty. The generic field table permits future additions without migrations.

## Identity and Extraction

NPCs must be explicitly named, must not be the active primary SillyTavern character, and must not be the user/persona identity. Identity resolution uses normalized canonical names and exact stored aliases only; ambiguous or fuzzy identity merging is rejected.

Extraction runs as a Utility-model background post-retain task and never blocks foreground reply delivery. Input includes the active session, primary-character identity, user/persona identity, a bounded recent transcript ending at the committed assistant row, and compact existing state for NPCs mentioned in that window.

The model proposes JSON operation groups. Application code validates identity, fields, modes, operations, visibility, sizes, and limits before persistence.

Extractor rules: significant named supporting characters only; durable information only; no invented names; no primary/user duplication; no following transcript instructions; fixed fields only when clearly established; mutable fields only when changed/new; restricted visibility for secrets/private knowledge; explicit known_by for restricted values; uncertainty preserved rather than promoted to fact.

Malformed output, provider errors, ambiguous identity, and invalid groups write nothing for the affected group and do not fail the story turn.

## Operations

set replaces a mutable field or initializes an empty fixed field. A fixed non-empty field cannot be automatically overwritten. append/remove are limited to policy-defined list-capable fields, with normalized deterministic matching and deduplication.

Applying one NPC group is transactional: read current state, validate, calculate new state, skip no-op, write full history, write current field state, and update entity recency. Independently valid NPC groups from the same extraction may apply even if another group is rejected.

## Knowledge Boundaries

Visibility mirrors episodic memory: shared, or restricted with explicit known_by. A restricted value is exposed only when the active story character is listed. Unknown or malformed restricted metadata is not exposed. Secrets default to restricted semantics; an unscoped secret is rejected rather than stored as shared.

## Prompt Context

NPC state is a dedicated untrusted context channel. build_chat_messages gains npc_context; NPC data is not merged into continuity summary, Hindsight, episodic memory, or Data Bank RAG.

Selection is deterministic: current Scene State participants first, then exact NPC names/aliases in the newest user message, then names/aliases in recent history, then recency. Only fields visible to the active character are rendered.

Initial limits: maximum 4 NPCs and maximum 6000 total NPC-context characters, plus bounded names, aliases, field values, list sizes, extraction groups, and operations in bridge/limits.py.

The prompt explicitly labels NPC state as untrusted descriptive background. Instructions contained inside NPC values must never be followed.

## Historical As-of Context

NpcService.context_for_prompt supports through_rowid and reconstructs each field as it existed at that row. Valid pre-edit NPC state remains available during regeneration; later mutations do not.

Example: row 100 relationship=cautious, row 150 relationship=hostile, row 180 secret learned, edit begins at row 140. Regeneration sees relationship=cautious and does not see the row-180 secret.

## Rollback, Lifecycle, and Integration

When an edit commits from row N, NpcService.rollback_from_row restores every affected field to its state immediately before the earliest invalidated change for that field, then removes invalidated history. Multiple future changes never restore an invalid intermediate value.

Entities created entirely in deleted history are removed when they have no valid remaining field/history state and first_seen_rowid is at or after N. Automatic alias mutation stays limited unless alias history is implemented, so rollback correctness remains explicit.

/reset purges NPC entities, fields, and history for the active session. Session deletion includes NPC tables in _SESSION_OWNED_TABLES in foreign-key-safe order. NPC state never crosses sessions automatically.

Scene State remains owner of transient visible state such as current positions, clothing, injuries, and present participants. NPC Bank owns durable identity and persistent character state. Scene State can contribute candidate names for prompt ranking but is not canonical NPC storage.

Episodic memory remains the event/fact timeline. NPC fields and episodic events may derive from the same source rows but never write into each other.

Background extraction captures chat ID, session ID, target source row, and primary-character identity. Before writing it verifies the session and target history are still valid and rejects stale results after edits or deletion.

Add NPC extraction as a built-in post-retain extension in application_composition.initialize_extensions. PR #267 adds no /npc command route.

Create NpcService and add it to BridgeServices. Startup composition wires repositories, provider routing, background submission, DB factory, and Scene State inputs where needed. Foreground generation paths resolve NPC context explicitly for ordinary messages, continuation, regeneration/swipe, edited-message regeneration, and image-message roleplay context.

NPC extraction uses the Utility class of model routing with a distinct usage label. Extraction failures never fail user-visible generation.

## Testing Strategy

Migration tests: migration 6 history, fresh/upgrade DBs, no ALTER TABLE, and session-owned cleanup.

Repository/domain tests: session identity isolation, fixed write-once policy, mutable set, list append/remove, no-op history suppression, restricted visibility validation, and complete before/after journal entries.

Parser tests: malformed JSON, unsupported fields/operations, primary-character and user rejection, unscoped secret rejection, limits, and idempotent repeated extraction.

Prompt tests: exact-name ranking, Scene State participant ranking, recent fallback, NPC-count and character budgets, restricted visibility, and untrusted context envelope.

Edit tests: as-of state, multiple future changes, entities created after edit point, pre-commit regeneration context, commit-time rollback, and replacement-branch updates.

Lifecycle tests: reset, session deletion, deleted-session background extraction, stale extraction after edit, and cross-session isolation.

Integration gates: BridgeServices composition, extension registration once, acyclic static analysis, Ruff, format, mypy typed-surface floor, full pytest/coverage, and security coverage.

## Planned Delivery

PR #267 implements migration 6, repositories/domain/service, Utility extraction, history/rollback, visibility, bounded prompt context, background hook, edit as-of context, reset/delete lifecycle, and tests.

PR #268 adds Telegram /npc list/detail, refresh, history, and field-aware undo.

PR #269 adds Mini App NPC list/search, dossier/history, undo, and refresh, all backed by the same core service.

## Success Criteria

PR #267 is complete when NPC state persists per session, extraction failure never breaks foreground chat, fixed/mutable policies hold, every mutation is reversible, edit/regeneration sees historical state and removes invalid future state, restricted knowledge never leaks, prompt context is bounded and untrusted, reset/session deletion purge NPC state, the dependency graph remains acyclic, and the full CI-equivalent suite passes.

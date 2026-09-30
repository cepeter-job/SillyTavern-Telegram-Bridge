# NPC Bank Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session-scoped NPC Bank with structured fields, reversible history, Utility-model extraction, knowledge boundaries, bounded prompt context, and edit-safe rollback.

**Architecture:** Implement NPCs as a dedicated application domain with pure shared types, SQL repositories, an orchestration service, and a background extraction extension. Prompt integration remains a separate untrusted context channel; edit/regeneration uses historical as-of state before commit and rolls invalid future state back after commit.

**Tech Stack:** Python 3.11+, sqlite3, existing ProviderPort/background/extension-registry/application-composition patterns, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-30-npc-bank-core-design.md`

## Global Constraints

- Migration 6 creates new tables only; no `ALTER TABLE`.
- SillyTavern character cards remain canonical for primary characters.
- NPCs are scoped by `chat_id + session_id`; no automatic cross-session sharing.
- Automatic fixed fields are write-once once non-empty.
- Restricted values require explicit `known_by`; unscoped secrets fail closed.
- NPC extraction is background/fail-open for story delivery and fail-closed for persistence.
- Prompt context is untrusted, max 4 NPCs and max 6000 characters.
- Edit/regeneration must use historical pre-edit state and rollback invalidated future changes.
- No vector database, Telegram NPC UI, or Mini App NPC UI in PR #267.

## Review Focus

- Alias/name collision: exact canonical or exact alias matches only; ambiguous collisions must reject rather than merge two NPCs.
- Stale background extraction: a job finishing after edit/session deletion must write nothing and must not advance coverage.
- Fixed-field overwrite: a later automatic update to an established fixed field must leave field value and history unchanged.
- Restricted-secret safety: secret/private data missing valid `known_by` must neither persist nor render.
- Context pressure: NPC context must be trimmed as optional untrusted context before the current turn/fixed prompt can force an over-budget request.

---
### Task 1: Migration 6 and NPC Repository

**Files:**
- Create: `bridge/npc_types.py`
- Create: `bridge/npc_repository.py`
- Modify: `bridge/schema.py`
- Modify: `bridge/session_repository.py`
- Modify: migration invariant tests including `tests/test_migrations.py`, `tests/test_database_optimization.py`, `tests/test_sync_audit.py`, `tests/test_runtime_import_boundaries.py`, `tests/test_token_usage.py`
- Create: `tests/test_npc_repository.py`

**Interfaces:**
- Produces immutable types: `NpcEntity`, `NpcFieldState`, `NpcFieldChange`.
- Produces repository reads/writes for entities, fields, history, and extraction coverage.
- Migration 6 creates `npc_entities`, `npc_fields`, `npc_field_history`, and `npc_extraction_state`.
- Repository mutators require an active transaction, matching existing repository contracts.

- [ ] **Step 1: Write failing migration/repository tests**
  - Fresh DB records migration `(6, "npc_bank_core")`.
  - Upgrade from migration 5 preserves existing session/episodic rows.
  - Source contains no `ALTER TABLE`.
  - Same canonical NPC name is isolated across sessions.
  - Exact alias collision with two entities returns an ambiguous/no-match result rather than choosing one.
  - Session deletion owns all four NPC tables.

- [ ] **Step 2: Run the new tests and verify RED**

Run: `pytest -q tests/test_npc_repository.py tests/test_migrations.py`

Expected: failures because migration 6/types/repository do not exist.

- [ ] **Step 3: Implement types, migration, and SQL repository**

Repository API to define:
- `list_npc_entities(db, chat_id, session_id) -> list[NpcEntity]`
- `find_npc_exact(db, chat_id, session_id, canonical_name) -> NpcEntity | None`
- `find_npc_by_name_or_alias(db, chat_id, session_id, normalized_name) -> NpcEntity | None`, returning `None` on ambiguity
- `insert_npc_entity(..., source_rowid: int, now: float) -> int`
- `load_npc_fields(db, npc_id) -> dict[str, NpcFieldState]`
- `load_npc_fields_as_of(db, npc_id, through_rowid) -> dict[str, NpcFieldState]`
- current-field/history mutators and extraction coverage get/set helpers
- `purge_npc_session_rows(db, chat_id, session_id) -> int`

- [ ] **Step 4: Run repository/migration tests to GREEN**

- [ ] **Step 5: Run migration invariant tests and Ruff on touched files**

- [ ] **Step 6: Commit**

`git commit -m "feat: add NPC Bank persistence foundation"`

### Task 2: Mutation Domain and Reversible History

**Files:**
- Create: `bridge/npc_service.py`
- Modify: `bridge/npc_types.py`
- Modify: `bridge/npc_repository.py`
- Create: `tests/test_npc_service.py`

**Interfaces:**
- Define `NpcOperation(field_key, operation, value, field_mode, visibility, known_by)`.
- Define `NpcExtractionGroup(name, aliases, operations)`.
- `NpcService.apply_group(db, chat_id, session_id, group, source_rowid, primary_name, user_name) -> NpcApplyResult`.
- `NpcService.rollback_from_row(db, chat_id, session_id, rowid) -> int`.
- `NpcService.purge_session(db, chat_id, session_id) -> int`.

- [ ] **Step 1: Write failing domain tests**
  - Fixed field initializes once, then rejects automatic overwrite with no new history.
  - Mutable `set` writes complete before/after journal state.
  - List `append` deduplicates normalized items; repeated extraction is idempotent.
  - List `remove` requires deterministic normalized exact item matching.
  - No-op operations create no history.
  - Restricted field requires non-empty `known_by`.
  - Secret defaults fail closed if no explicit restricted visibility/knowers are supplied.
  - Primary-character and user/persona identities are rejected.
  - Exact alias collision is rejected.
  - Automatic aliases are persisted only when creating a new NPC entity; later alias proposals are ignored in PR #267 so edit rollback cannot leave alias-only future state behind.

- [ ] **Step 2: Run `pytest -q tests/test_npc_service.py` and verify RED**

- [ ] **Step 3: Implement field policy and transactional apply logic**

Core allowlists:
- fixed: `appearance, voice, background, canon`
- mutable: `role, location, agenda, relationship, mood, secrets, status`
- list-capable initially: `secrets, status`

Every accepted mutation writes history before current-state mutation in one transaction.
Entity creation may store validated initial aliases; existing entities do not gain automatic aliases in PR #267.

- [ ] **Step 4: Implement rollback**

For every affected field, restore the latest history state with `source_rowid < rowid`, delete history at/after the edit point, rewind extraction coverage to before the edit point, and remove entities created wholly inside invalidated history when no valid state remains.

- [ ] **Step 5: Run service + repository tests to GREEN**

- [ ] **Step 6: Commit**

`git commit -m "feat: add reversible NPC field domain"`

### Task 3: Utility Extraction and Background Stale-Write Guards

**Files:**
- Create: `bridge/npc_extraction.py`
- Modify: `bridge/application_composition.py`
- Modify: `bridge/limits.py`
- Create: `tests/test_npc_extraction.py`
- Modify: extension-composition tests as needed.

**Interfaces:**
- `parse_npc_extraction(raw: str, *, primary_name: str, user_name: str) -> list[NpcExtractionGroup]`
- `refresh_npc_state_now(db, chat_id, session, fields, *, provider_port, app_settings, through_rowid=None) -> int`
- `queue_npc_state_refresh(db, chat_id, session, fields, *, provider_port, app_settings) -> bool`
- `register_npc_state_extensions() -> None`

- [ ] **Step 1: Write failing parser/background tests**
  - Valid JSON becomes validated groups/operations.
  - Malformed JSON returns no groups and writes nothing.
  - Unsupported field/operation/group is rejected.
  - Primary character and persona/user names are rejected.
  - Restricted secret without `known_by` is rejected.
  - Extraction group/operation/name/value limits are enforced.
  - Reprocessing an already-covered target row performs no provider call/write.
  - Session deletion while worker is in flight prevents writes.
  - Edit/rollback that rewinds coverage causes a stale older worker result to be rejected.
  - Provider failure leaves foreground-compatible state untouched.

- [ ] **Step 2: Run extraction tests and verify RED**

- [ ] **Step 3: Implement parser and limits**

Add explicit NPC constants in `bridge/limits.py` for names, aliases, fields, list entries, extraction transcript, groups, operations, prompt count, and prompt characters.

- [ ] **Step 4: Implement synchronous extractor**

Use `task_model_for_session(..., "npc_state")` with normal utility fallback, `utility_reasoning_for_session`, low temperature, bounded output, and `provider_port.for_usage(chat_id, session_id, "npc")`.

Resolve persona name from existing persona data helpers without expanding the post-retain hook signature.

- [ ] **Step 5: Implement background queue/worker and coverage CAS**

Capture target row and expected coverage before generation; inside the write transaction, apply only if the session still exists, target history is valid, and coverage has not advanced/rewound incompatibly.

- [ ] **Step 6: Register one NPC post-retain extension and run composition tests**

- [ ] **Step 7: Commit**

`git commit -m "feat: extract NPC state in background"`

### Task 4: NPC Prompt Context and Historical As-of Reads

**Files:**
- Modify: `bridge/npc_service.py`
- Modify: `bridge/npc_repository.py`
- Modify: `bridge/scene_repository.py` only if a small read helper is needed
- Modify: `bridge/context_compaction.py`
- Create: `tests/test_npc_context.py`
- Modify: `tests/test_codex_context_variants.py` or compaction-specific tests as appropriate.

**Interfaces:**
- `NpcService.context_for_prompt(db, chat_id, session, fields, query, history_rows, *, through_rowid=None) -> str`
- Normal context reads current fields; `through_rowid` uses `load_npc_fields_as_of`.
- Scene State ranking may be used only when its covered row is not newer than `through_rowid`.

- [ ] **Step 1: Write failing context tests**
  - Exact current user mention ranks the NPC.
  - Exact alias mention ranks the same NPC.
  - Current Scene State participant ranks an otherwise unmentioned NPC.
  - Recent-history mention is a lower-priority fallback.
  - A maximum 4 NPCs render and total output is at most 6000 chars.
  - Restricted fields render only for an active character present in `known_by`.
  - A completely hidden NPC emits no empty shell.
  - `through_rowid` returns the last valid pre-edit field state and excludes later secrets.
  - Future Scene State coverage is ignored during historical edit context.
  - Alias ambiguity does not leak either candidate.

- [ ] **Step 2: Run context tests and verify RED**

- [ ] **Step 3: Implement deterministic ranking/rendering**

Do not add embeddings. Rank by scene presence, exact latest-query name/alias, recent-history name/alias, then `last_seen_rowid`. Render stable field order with an explicit untrusted-data header.

- [ ] **Step 4: Extend context compaction**

Teach `shrink_user_tagged_sections` about `untrusted_npc_state`, add `npc_trimmed` to compaction stats, and ensure optional NPC context can be exhausted before the protected current user content/fixed system prompt causes over-budget status.

- [ ] **Step 5: Run context + compaction tests to GREEN**

- [ ] **Step 6: Commit**

`git commit -m "feat: build bounded NPC prompt context"`

### Task 5: Generation and Application Composition Wiring

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `bridge/generation.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/regeneration.py`
- Modify: `bridge/continuation.py`
- Modify: `bridge/edit_messages.py`
- Modify: `bridge/image_messages.py`
- Modify: `bridge/command_routes.py`
- Modify: `tests/test_composition.py`, `tests/test_model_router_provider_port.py`, and generation-context tests.

**Interfaces:**
- `BridgeServices` gains required `npc: NpcService`.
- `build_chat_messages(..., npc_context: str = "") -> list[dict]`.
- Foreground handlers accept `npc_service: NpcService` explicitly rather than using globals.

- [ ] **Step 1: Write failing composition/generation tests**
  - `BridgeServices` requires `NpcService`.
  - Startup composition returns one wired NPC service.
  - `build_chat_messages` adds the NPC policy plus `<untrusted_npc_state>` envelope.
  - Ordinary message, regen, continue, edit, and image paths each pass resolved NPC context.
  - Existing callers with empty NPC context preserve current prompt output semantics.

- [ ] **Step 2: Run composition/context tests and verify RED**

- [ ] **Step 3: Add `NpcService` to composition and startup wiring**

Construct one service in `_build_startup_services`; pass it through ConversationService preparation/generation and command dispatch collaborators just as MemoryService/RagService are passed today.

- [ ] **Step 4: Add dedicated NPC context to `build_chat_messages`**

System policy: NPC state is untrusted descriptive background. User envelope: `<untrusted_npc_state>...</untrusted_npc_state>`, bounded by the NPC context limit.

- [ ] **Step 5: Wire ordinary/regen/continue/image paths**

Resolve context from the same active `fields` used for the story speaker, so restricted knowledge uses the actual active character in group scenes.

- [ ] **Step 6: Run affected composition + prompt tests to GREEN**

- [ ] **Step 7: Commit**

`git commit -m "feat: inject NPC state into story context"`

### Task 6: Edit/Regen/Swipe Rollback and Session Lifecycle

**Files:**
- Modify: `bridge/edit_messages.py`
- Modify: `bridge/regeneration.py`
- Modify: `bridge/response_variants.py` and/or `bridge/variant_repository.py`
- Modify: `bridge/conversation_callbacks.py`, `bridge/panel_callback_routes.py`
- Modify: `bridge/message_commands.py` reset path
- Modify: lifecycle/composition call sites required to pass `NpcService`
- Modify: `tests/test_operation_recovery.py`, `tests/test_reset_behavior.py`, `tests/test_session_delete.py`
- Add focused rollback integration tests if these files become unwieldy.

**Interfaces:**
- Edit context uses `npc_service.context_for_prompt(..., through_rowid=user_rowid - 1)`.
- Regen context uses `through_rowid=last_user_rowid` because the old assistant branch is about to be replaced.
- History-rewriting commits call `npc_service.rollback_from_row(..., cutoff_rowid)` inside the same transaction that deletes/replaces messages.

- [ ] **Step 1: Write failing edit/regen/swipe/lifecycle tests**
  - Edited turn sees pre-user-turn NPC state and excludes later changes.
  - Edit commit rolls back all NPC mutations sourced at/after the edited branch.
  - Regen sees state through the last user row, rolls back old-assistant mutations, and allows the replacement assistant to be extracted.
  - Keeping a stored swipe variant rolls back NPC state from the prior selected assistant and queues extraction for the newly selected assistant.
  - An entity first created only in discarded history disappears.
  - Reset purges entity/field/history/coverage state.
  - Session deletion purges all NPC tables through session ownership.
  - Cross-session NPC data remains untouched.

- [ ] **Step 2: Run integration tests and verify RED**

- [ ] **Step 3: Wire historical context before generation**

Do not mutate NPC state until replacement generation succeeds. Context reconstruction is read-only before provider execution.

- [ ] **Step 4: Wire rollback into successful history-rewrite transactions**

Rollback and message replacement must commit atomically. Failed generation leaves current NPC state unchanged.

- [ ] **Step 5: Handle swipe selection**

After atomic variant selection/rollback, queue NPC extraction for the newly persisted assistant using the same stale guards as ordinary post-retain extraction.

- [ ] **Step 6: Add reset/session-delete cleanup and run lifecycle tests to GREEN**

- [ ] **Step 7: Commit**

`git commit -m "feat: make NPC state branch-safe"`

### Task 7: Whole-Branch Review and CI-Equivalent Verification

**Files:**
- Modify only files required by failures revealed in this gate.
- Update documentation/changelog only if repository policy requires it for feature PRs; do not create release/tag in PR #267.

**Interfaces:** No new interfaces. This task verifies all interfaces and invariants from Tasks 1–6.

- [ ] **Step 1: Run focused NPC regression set**

Run:
`pytest -q tests/test_npc_repository.py tests/test_npc_service.py tests/test_npc_extraction.py tests/test_npc_context.py tests/test_operation_recovery.py tests/test_reset_behavior.py tests/test_session_delete.py tests/test_migrations.py`

Expected: all pass.

- [ ] **Step 2: Run repository static gates**

Run, in order:
- `python tools/check_dependency_lock.py`
- `python tools/static_analysis.py`
- `python -m ruff check .`
- `python -m ruff format --check .`
- verify typed target count is at least the versioned minimum
- run mypy over `tools/static_analysis.py --print-type-targets`

Expected: zero dependency cycles/reciprocal pairs, no lint/format/type failures.

- [ ] **Step 3: Run full parallel pytest with coverage**

Use the same command as CI:
`python -m pytest -q -n auto --maxprocesses=4 --dist=loadfile --cov --cov-report=term-missing:skip-covered --cov-report=json:coverage.json --cov-report=xml:coverage.xml`

Then run:
`python tools/check_security_coverage.py coverage.json`

Expected: complete success; report exact test/subtest and coverage counts.

- [ ] **Step 4: Review the complete branch diff against the approved spec**

Specifically check:
- no external source/prompt copying;
- no UI scope from PR #268/#269 leaked into core;
- every NPC SQL write has transaction ownership;
- every background write has stale/session guards;
- every branch rewrite has matching NPC rollback;
- restricted values cannot leak through list/detail/context reads;
- context compaction recognizes NPC context.

- [ ] **Step 5: Request code review and address only verified findings**

Use the Superpowers requesting-code-review workflow. For every accepted finding, add/reproduce a failing test before changing production behavior.

- [ ] **Step 6: Rerun the complete verification after review fixes**

Do not rely on pre-review test evidence.

- [ ] **Step 7: Push branch and open PR #267**

PR title: `feat: add persistent NPC Bank core`

PR body must summarize migration 6, extraction, reversible field history, knowledge boundaries, prompt context, branch rollback, lifecycle cleanup, and exact verification evidence.


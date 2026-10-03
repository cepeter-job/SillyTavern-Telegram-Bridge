# Narrative Engine Phase 5: Alternate Ending Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a CLOSED story create a new independent session from its immutable pre-finale checkpoint without modifying the original or leaking post-finale state/memory.

**Architecture:** Alternate Ending is a durable branch operation whose target session ID is deterministic from the admitted operation/checkpoint, making retries idempotent. One local SQLite transaction creates the target session, copies transcript/configuration/local-derived state only through the checkpoint boundary, and restores the checkpoint Narrative/Director/Ending snapshot; external Hindsight work occurs afterward and never points target recall at the source session. The original remains CLOSED forever.

**Tech Stack:** Python 3.11, SQLite, pytest, existing session/repository services, existing operations framework, Hindsight session tags/document IDs, Telegram/Mini App panel patterns.

**Spec:** `docs/superpowers/specs/2026-10-03-narrative-engine-ai-director-design.md`

## Global Constraints

- Alternate Ending is available only from a valid immutable pre-finale checkpoint on a CLOSED originating session.
- The existing `/branch` command remains response-variant selection; Alternate Ending is a separate action.
- The same checkpoint may be reused by later explicit Alternate Ending requests; idempotency is per admitted operation, not per checkpoint.
- One admitted operation creates at most one deterministic target session.
- Original finale/resolution/epilogue/CLOSED state is never copied as current branch state.
- Target session receives fresh job/delivery/operation identity.
- Hindsight uses one bank per Telegram chat; target isolation comes from a new session tag and session-derived document IDs with strict recall filtering.
- V1 must never make target recall query the source session tag.
- Failure during local branch creation yields either no target or a complete target; no half-copied session.
- External-memory failure after local commit may degrade memory but must not roll back or corrupt the target session.

## Review Focus

- User double-taps Alternate Ending → same admitted operation returns the same target session, not two copies.
- User intentionally requests a second alternate ending later → same checkpoint may create a different new target session.
- Source has post-checkpoint summary/NPC/scene/episodic changes → target receives only state valid through checkpoint.
- Hindsight is enabled and source has finale memories → target recall never uses source session tag/document IDs.
- Crash after local target commit but before external-memory step → restart resumes target operation without recreating/corrupting the target.

---

### Task 1: Durable Alternate Ending operation and transactional session clone

**Files:**
- Create: `bridge/alternate_ending.py`
- Modify: `bridge/session_core.py`
- Modify: `bridge/session_repository.py`
- Modify: `bridge/operations.py`
- Test: `tests/test_alternate_ending.py`
- Test: `tests/test_operation_recovery.py`

**Interfaces:**
- Produces:
  - `alternate_ending_target_id(origin_session_id: str, checkpoint_id: str, operation_id: str) -> str` using deterministic bounded hash-based ID.
  - `create_alternate_ending(db, chat_id: str, origin_session_id: str, checkpoint_id: str, operation_id: str, *, title: str | None = None) -> dict[str, str]`
  - operation phases: `prepared → local_committed → memory_checked → applied`.
- The validator checks origin CLOSED + checkpoint validity before target session exists.
- Local transaction copies transcript rows through checkpoint `source_revision` and configuration/state described in Task 2.

- [ ] **Step 1: Write branch-operation tests**

Assert:
- non-CLOSED origin rejected;
- missing/stale/superseded checkpoint rejected before target creation;
- deterministic same operation/checkpoint returns same target ID;
- two distinct explicit operation IDs may reuse the same checkpoint and create different targets;
- injected failure inside local transaction leaves no target session/messages;
- original remains byte-for-byte/lifecycle unchanged.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending.py tests/test_operation_recovery.py`  
Expected: FAIL.

- [ ] **Step 3: Implement validation and deterministic target creation**

Use existing `operations` state for admission. Do not mark the immutable checkpoint consumed.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending.py tests/test_operation_recovery.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/alternate_ending.py bridge/session_core.py bridge/session_repository.py bridge/operations.py tests/test_alternate_ending.py tests/test_operation_recovery.py
git commit -m "feat: create alternate ending sessions"
```

### Task 2: Copy checkpoint-valid configuration and local derived state

**Files:**
- Modify: `bridge/alternate_ending.py`
- Modify: `bridge/narrative_checkpoints.py`
- Modify: `bridge/narrative_repository.py`
- Modify: `bridge/ending_repository.py`
- Modify: `bridge/variant_repository.py`
- Test: `tests/test_alternate_ending_state_copy.py`
- Test: `tests/test_npc_branch_safety.py`
- Test: `tests/test_scene_state.py`

**Interfaces:**
- Produces internal `copy_checkpoint_state(db, chat_id: str, origin_session_id: str, target_session_id: str, checkpoint: NarrativeCheckpoint) -> None`, callable only inside the branch transaction.
- Copy through boundary:
  - session Character/Persona/World/System Prompt/Author's Note/response language;
  - Story/Utility/Director model selections and reasoning settings;
  - Narrative Style;
  - generation settings;
  - transcript up to checkpoint boundary;
  - summary/episodic/NPC/physical Scene State valid through boundary;
  - Narrative scenes/threads/arcs/state;
  - Director state/goal history through boundary;
  - Ending Goal history through boundary.
- Target ending lifecycle resets to OPEN at the checkpoint's pre-finale story state; target has no finale/epilogue/CLOSED state.
- Do not copy response-variant/delivery/job records that refer to post-boundary or source row IDs without remapping.

- [ ] **Step 1: Write exact copy-boundary tests**

Seed source state before and after checkpoint. Assert target contains only pre-checkpoint values and no source finale/epilogue. Verify every target transcript/derived foreign row references target session and remapped target message rowids where required.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_state_copy.py tests/test_npc_branch_safety.py tests/test_scene_state.py`  
Expected: FAIL.

- [ ] **Step 3: Implement row mapping and state restore**

Create an origin-rowid → target-rowid map while copying transcript and use it for summary/episodic/NPC/scene source boundaries. Do not reuse source Telegram message IDs or delivery IDs.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_state_copy.py tests/test_npc_branch_safety.py tests/test_scene_state.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/alternate_ending.py bridge/narrative_checkpoints.py bridge/narrative_repository.py bridge/ending_repository.py bridge/variant_repository.py tests/test_alternate_ending_state_copy.py tests/test_npc_branch_safety.py tests/test_scene_state.py
git commit -m "feat: restore narrative state for alternate endings"
```

### Task 3: Hindsight isolation and best-effort target memory initialization

**Files:**
- Create: `bridge/alternate_ending_memory.py`
- Modify: `bridge/memory.py`
- Modify: `bridge/memory_backend.py`
- Test: `tests/test_alternate_ending_memory.py`
- Test: `tests/test_hindsight_session_cleanup.py`
- Test: `tests/test_memory_native_backend.py`

**Interfaces:**
- Produces:
  - `initialize_alternate_ending_memory(db, chat_id: str, target_session: dict[str, str], checkpoint_revision: int, *, memory_service: MemoryService, app_settings: AppSettings) -> str`
  - result `"disabled" | "ready" | "degraded"`.
- V1 does not copy opaque source-session Hindsight documents.
- If memory is enabled, prefer rebuilding the target's deterministic conversation document from the copied target transcript through the existing retain path. All tags/document IDs use **target_session_id**.
- Failure is best-effort/degraded and never changes target recall to source tags.

- [ ] **Step 1: Write isolation tests**

Assert:
- `hindsight_bank_id(chat_id)` is shared but `memory_recall_filter` returns only target `session:<target>` tag;
- target conversation document ID is derived from target session;
- source finale/epilogue documents are never copied/referenced;
- repeated resume of same operation does not create duplicate target deterministic document IDs;
- failed Hindsight retain leaves target local state usable and operation can complete as degraded.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_memory.py tests/test_hindsight_session_cleanup.py tests/test_memory_native_backend.py`  
Expected: FAIL.

- [ ] **Step 3: Implement best-effort initialization**

Do external Hindsight I/O only after `local_committed`. Record operation phase after the attempt; do not retry automatically forever.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_memory.py tests/test_hindsight_session_cleanup.py tests/test_memory_native_backend.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/alternate_ending_memory.py bridge/memory.py bridge/memory_backend.py tests/test_alternate_ending_memory.py tests/test_hindsight_session_cleanup.py tests/test_memory_native_backend.py
git commit -m "feat: isolate alternate ending memory"
```

### Task 4: Alternate Ending Telegram and Mini App controls

**Files:**
- Create: `bridge/alternate_ending_panels.py`
- Create: `bridge/alternate_ending_callbacks.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/session_panels.py`
- Modify: `bridge/director_panels.py`
- Modify: `bridge/miniapp_sessions.py`
- Modify: `bridge/miniapp_director.py`
- Test: `tests/test_alternate_ending_panels.py`
- Test: `tests/test_miniapp_management.py`
- Test: `tests/test_panel_callback_singleflight.py`

**Interfaces:**
- Closed-session UI offers Alternate Ending only when a valid checkpoint exists.
- Callback creates/adopts one durable operation ID and reports the resulting target session.
- Default title: `<origin title> — Alternate Ending`; optional validated rename may occur through existing session rename flow.

- [ ] **Step 1: Write panel/ownership tests**

Assert:
- button absent without valid checkpoint;
- actor/session binding prevents cross-user reuse;
- double click/admitted operation creates one target;
- completed operation can reopen target session result without rerunning copy;
- a later explicit click may create another alternate branch with a new operation ID;
- existing `/branch` command remains unchanged.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_panels.py tests/test_panel_callback_singleflight.py tests/test_miniapp_management.py`  
Expected: FAIL.

- [ ] **Step 3: Implement controls**

Keep adapters thin; branch creation lives in `alternate_ending.py`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_panels.py tests/test_panel_callback_singleflight.py tests/test_miniapp_management.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/alternate_ending_panels.py bridge/alternate_ending_callbacks.py bridge/panel_callback_routes.py bridge/session_panels.py bridge/director_panels.py bridge/miniapp_sessions.py bridge/miniapp_director.py tests/test_alternate_ending_panels.py tests/test_miniapp_management.py
git commit -m "feat: add alternate ending controls"
```

### Task 5: Restart recovery and operation convergence

**Files:**
- Modify: `bridge/alternate_ending.py`
- Modify: `bridge/conversation_jobs.py`
- Modify: `bridge/operation_recovery.py`
- Test: `tests/test_alternate_ending_recovery.py`
- Test: `tests/test_operation_recovery.py`
- Test: `tests/test_sqlite_contention.py`

**Interfaces:**
- Recovery derives action from operation phase and deterministic target ID:
  - no operation/local target → create local branch;
  - `local_committed` → run/check external memory only;
  - `memory_checked` → mark applied/return target;
  - `applied` → return target with zero writes/provider calls.
- Recovery never creates a second target for the same operation.

- [ ] **Step 1: Write failure-injection tests**

Inject crash:
- before local transaction;
- mid-copy;
- after local commit;
- during Hindsight retain;
- after memory attempt before applied;
- after applied.
Assert convergence to one complete target or no target according to durable phase.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_recovery.py tests/test_operation_recovery.py`  
Expected: FAIL.

- [ ] **Step 3: Implement recovery adapter**

Use deterministic target ID and existing operations framework; do not add a parallel operation table.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_alternate_ending_recovery.py tests/test_operation_recovery.py tests/test_sqlite_contention.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/alternate_ending.py bridge/conversation_jobs.py bridge/operation_recovery.py tests/test_alternate_ending_recovery.py tests/test_operation_recovery.py
git commit -m "feat: recover alternate ending creation"
```

### Task 6: Documentation and final whole-feature verification

**Files:**
- Modify: `README.md`
- Modify: `docs/user-guide.md`
- Modify: `docs/miniapp.md`
- Modify: `docs/operations.md`
- Modify: `bridge/help_details.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Docs distinguish existing `/branch` response variants from Alternate Ending.
- Docs state source remains CLOSED/immutable, checkpoint boundary semantics, local-state copy limits, and Hindsight isolation/degraded behavior.

- [ ] **Step 1: Update user/operator docs**

Document the complete feature set now that all phases are implemented.

- [ ] **Step 2: Run whole Narrative Engine test set**

Run:
```bash
python -m pytest -q   tests/test_narrative_migrations.py   tests/test_narrative_policy.py   tests/test_narrative_reconciliation.py   tests/test_narrative_generation.py   tests/test_director_model_selection.py   tests/test_director_validation.py   tests/test_director_service.py   tests/test_director_cadence.py   tests/test_director_panels.py   tests/test_narrative_arcs.py   tests/test_ending_state.py   tests/test_finale_readiness.py   tests/test_narrative_checkpoints.py   tests/test_finale_resolution.py   tests/test_epilogue_generation.py   tests/test_closed_story_recovery.py   tests/test_closed_session_guard.py   tests/test_alternate_ending.py   tests/test_alternate_ending_state_copy.py   tests/test_alternate_ending_memory.py   tests/test_alternate_ending_recovery.py
python -m ruff check .
python -m ruff format --check .
python tools/static_analysis.py
python tools/static_analysis.py --print-type-targets | xargs python -m mypy
git diff --check
```
Expected: all pass.

- [ ] **Step 3: Run full repository tests**

Run: `python -m pytest -q -n 2 --dist=loadfile --cov --cov-report=term-missing:skip-covered`  
Expected: PASS at or above the configured coverage floor.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/user-guide.md docs/miniapp.md docs/operations.md bridge/help_details.py CHANGELOG.md
git commit -m "docs: document alternate endings"
```

- [ ] **Step 5: Request whole-branch code review before integration**

Use the Superpowers requesting-code-review workflow against the complete implementation branch. Resolve correctness findings before choosing merge/release actions.

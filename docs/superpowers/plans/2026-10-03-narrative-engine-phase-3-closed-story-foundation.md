# Narrative Engine Phase 3: Arcs and Closed Story Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add revision-aware arc tracking, Ending Goal/history, finale-readiness evaluation, and atomic pre-finale checkpoints without enabling hard closure or epilogue generation yet.

**Architecture:** Arc state extends the existing reconciliation pipeline but remains committed-story-derived. A dedicated Ending service owns Ending Goal/history and the mechanical `OPEN → FINALE_READY → FINALE` transition; pre-finale checkpoint creation and entering FINALE are one SQLite transaction. Phase 3 builds and tests the durable ending machinery but keeps user-facing Closed Story execution disabled until Phase 4 can complete the epilogue/closure pipeline safely.

**Tech Stack:** Python 3.11, SQLite, pytest, existing Narrative/Director services, ProviderPort, Telegram/Mini App panel patterns.

**Spec:** `docs/superpowers/specs/2026-10-03-narrative-engine-ai-director-design.md`

## Global Constraints

- Arc/Ending facts come from committed Story plus current reconciliation; Director intention alone never resolves an arc or makes finale-ready true.
- Ending Goal is optional, hidden from roleplay characters, and may be adapted only with auditable revision history.
- Ending Goal history retains the newest 100 rows per session; current goal is stored separately.
- Finale readiness must be revision-bound; continuing/editing the story invalidates stale readiness.
- Pre-finale checkpoint creation and transition to `FINALE` are one atomic SQLite commit.
- The checkpoint is immutable, versioned, and tied to the exact source revision.
- No epilogue or `CLOSED` transition exists in this phase.
- Do not expose a Closed Story mode that can enter FINALE in production UI until Phase 4 completes the full ending pipeline.
- No provider calls occur inside write transactions.

## Review Focus

- Director proposes an arc resolution that Story did not establish → arc remains unresolved after reconciliation.
- User edits before a stored finale-ready revision → readiness and checkpoint become invalid/stale.
- Crash between checkpoint creation and FINALE transition → impossible because both writes are one transaction.
- Ending Goal is automatically adapted → old/new values and reason remain in bounded history.
- Repeated finale confirmation on same revision → one transition/checkpoint, not duplicates.

---

### Task 1: Arc repository and reconciliation ownership

**Files:**
- Create: `bridge/narrative_arcs.py`
- Modify: `bridge/narrative_repository.py`
- Modify: `bridge/narrative_reconciliation.py`
- Test: `tests/test_narrative_arcs.py`
- Test: `tests/test_narrative_reconciliation.py`

**Interfaces:**
- Produces:
  - `NarrativeArc` value with `arc_id`, `status`, `phase`, `importance`, `summary`, `open_questions`, `related_threads`, `source_revision`.
  - `load_arcs(db, chat_id: str, session_id: str) -> list[NarrativeArc]`
  - `apply_reconciled_arcs(db, chat_id: str, session_id: str, arcs: list[NarrativeArc], *, expected_state_revision: int, through_rowid: int) -> bool`
- Initial statuses: `planned | active | dormant | resolved | abandoned`.

- [ ] **Step 1: Write arc reconciliation tests**

Assert:
- Utility reconciliation can create/update arcs from committed transcript;
- one arc may reference multiple threads;
- Director-only planned outcome never becomes resolved without Story evidence;
- stale reconciliation cannot overwrite newer arc revision;
- edit invalidation removes/rebuilds only state derived after the valid boundary.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_narrative_arcs.py tests/test_narrative_reconciliation.py`  
Expected: FAIL because arc reconciliation is absent.

- [ ] **Step 3: Implement arc values/repository use case**

Keep model parsing in reconciliation owner and SQL in repository owner. Bound arc count/input sizes in tests and implementation.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_narrative_arcs.py tests/test_narrative_reconciliation.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/narrative_arcs.py bridge/narrative_repository.py bridge/narrative_reconciliation.py tests/test_narrative_arcs.py tests/test_narrative_reconciliation.py
git commit -m "feat: track narrative arcs"
```

### Task 2: Ending state and Ending Goal history services

**Files:**
- Create: `bridge/ending_repository.py`
- Create: `bridge/ending_service.py`
- Create: `bridge/ending_values.py`
- Test: `tests/test_ending_state.py`
- Test: `tests/test_repository_transactions.py`

**Interfaces:**
- Produces `EndingLifecycle = Literal["open","finale_ready","finale","resolution_committed","epilogue_pending","epilogue_committed","closed"]`.
- Produces:
  - `load_ending_state(db, chat_id: str, session_id: str) -> EndingState`
  - `set_ending_goal(db, chat_id: str, session_id: str, goal: str, *, source: str, story_revision: int, scene_id: str = "", reason: str = "") -> EndingState`
  - `ending_goal_history(db, chat_id: str, session_id: str, limit: int = 100) -> list[EndingGoalRevision]`
  - `transition_ending_state(db, chat_id: str, session_id: str, expected_lifecycle_revision: int, from_state: str, to_state: str, *, story_revision: int) -> bool`
- Phase 3 exposes transitions only through `FINALE`; later lifecycle values exist in schema/type validation for future phases.
- History prune: newest 100 rows per session in append transaction.

- [ ] **Step 1: Write lifecycle/goal tests**

Assert:
- legal `OPEN → FINALE_READY → FINALE`;
- illegal direct `OPEN → CLOSED`, `FINALE_READY → RESOLUTION_COMMITTED`, etc.;
- goal revision history stores before/after/source/reason/story revision;
- automatic Director adaptation and manual user edit both append history;
- 101st history row prunes oldest without losing current goal;
- stale lifecycle revision CAS fails without mutation.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_ending_state.py tests/test_repository_transactions.py`  
Expected: FAIL.

- [ ] **Step 3: Implement repository/service**

Keep all transition validation deterministic. No model calls in repository/service transition methods.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_ending_state.py tests/test_repository_transactions.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/ending_repository.py bridge/ending_service.py bridge/ending_values.py tests/test_ending_state.py tests/test_repository_transactions.py
git commit -m "feat: add ending state and goal history"
```

### Task 3: Director arc/ending proposal extensions

**Files:**
- Modify: `bridge/director_contracts.py`
- Modify: `bridge/director_validation.py`
- Modify: `bridge/director_service.py`
- Test: `tests/test_director_validation.py`
- Test: `tests/test_director_service.py`
- Test: `tests/test_director_ending.py`

**Interfaces:**
- Extend `DirectorProposal` with bounded:
  - `arc_updates`
  - `story_phase`
  - `ending_goal_update`
  - `ending_goal_reason`
  - `finale_ready: bool | None`
- Director may propose; `EndingService` and reconciliation decide whether state changes are legal/current.
- Use usage purpose `director_ending` only for explicit ending reassessment; ordinary cadence remains `director`.

- [ ] **Step 1: Write proposal tests**

Assert:
- arc IDs/statuses validate against canonical state;
- Director cannot resolve an arc that reconciliation still marks active;
- Ending Goal adaptation requires a non-empty reason and current expected revision;
- finale-ready proposal is rejected if Narrative State is stale;
- malformed ending proposal follows one-repair maximum.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_ending.py tests/test_director_validation.py tests/test_director_service.py`  
Expected: FAIL.

- [ ] **Step 3: Implement proposal extensions**

Keep accepted Director decision separate from committed arc/ending facts. Delegate actual writes to arc/ending owners.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_director_ending.py tests/test_director_validation.py tests/test_director_service.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/director_contracts.py bridge/director_validation.py bridge/director_service.py tests/test_director_ending.py tests/test_director_validation.py tests/test_director_service.py
git commit -m "feat: let director plan arcs and endings"
```

### Task 4: Finale readiness and stale-readiness invalidation

**Files:**
- Create: `bridge/finale_service.py`
- Modify: `bridge/narrative_reconciliation.py`
- Modify: `bridge/edit_messages.py`
- Modify: `bridge/regeneration.py`
- Test: `tests/test_finale_readiness.py`
- Test: `tests/test_native_message_edit.py`
- Test: `tests/test_npc_branch_safety.py`

**Interfaces:**
- Produces:
  - `evaluate_finale_readiness(db, api_key: str, chat_id: str, session: dict[str, str], *, director_service: DirectorService, app_settings: AppSettings) -> EndingState`
  - `invalidate_finale_readiness_after(db, chat_id: str, session_id: str, source_rowid: int) -> None`
- Readiness stores exact story revision/rowid that produced it.
- With `require_finale_confirmation`, Story may continue; any later committed story invalidates old `FINALE_READY` back to `OPEN`.

- [ ] **Step 1: Write readiness tests**

Assert:
- current reconciled state can become FINALE_READY;
- stale Narrative State forces reconciliation before evaluation;
- new committed Story after FINALE_READY invalidates readiness;
- edit/regen before readiness revision invalidates readiness;
- repeated evaluation on unchanged revision is idempotent.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_finale_readiness.py tests/test_native_message_edit.py`  
Expected: FAIL.

- [ ] **Step 3: Implement readiness owner/invalidation hooks**

Do not enter FINALE yet except through Task 5 atomic checkpoint service.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_finale_readiness.py tests/test_native_message_edit.py tests/test_npc_branch_safety.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/finale_service.py bridge/narrative_reconciliation.py bridge/edit_messages.py bridge/regeneration.py tests/test_finale_readiness.py tests/test_native_message_edit.py tests/test_npc_branch_safety.py
git commit -m "feat: evaluate finale readiness safely"
```

### Task 5: Atomic pre-finale checkpoints and FINALE entry

**Files:**
- Create: `bridge/narrative_checkpoints.py`
- Modify: `bridge/ending_repository.py`
- Modify: `bridge/ending_service.py`
- Test: `tests/test_narrative_checkpoints.py`
- Test: `tests/test_ending_recovery.py`

**Interfaces:**
- Produces:
  - `create_pre_finale_checkpoint_and_enter(db, chat_id: str, session_id: str, *, expected_story_revision: int, expected_lifecycle_revision: int) -> NarrativeCheckpoint`
  - `load_pre_finale_checkpoint(db, chat_id: str, session_id: str) -> NarrativeCheckpoint | None`
  - `invalidate_checkpoints_after(db, chat_id: str, session_id: str, source_revision: int) -> None`
- Checkpoint payload includes NarrativePolicy, NarrativeState, DirectorState, EndingState, scene/thread/POV, arcs, Ending Goal history, accepted direction, exact transcript revision, `format_version=1`.
- Checkpoint insert + EndingState `FINALE` transition occur in one `write_transaction`.

- [ ] **Step 1: Write checkpoint/crash-window tests**

Assert:
- successful call creates exactly one immutable checkpoint and enters FINALE;
- injected exception before commit leaves neither checkpoint nor FINALE;
- repeated same admitted transition returns/reuses existing checkpoint, no duplicate;
- edit before source revision invalidates checkpoint;
- checkpoint JSON is versioned and bounded;
- no provider calls occur inside checkpoint transaction.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_narrative_checkpoints.py tests/test_ending_recovery.py`  
Expected: FAIL.

- [ ] **Step 3: Implement atomic checkpoint/transition**

Use only already-current committed state; no reconciliation/model work inside transaction.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_narrative_checkpoints.py tests/test_ending_recovery.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/narrative_checkpoints.py bridge/ending_repository.py bridge/ending_service.py tests/test_narrative_checkpoints.py tests/test_ending_recovery.py
git commit -m "feat: checkpoint stories before finale"
```

### Task 6: Ending Goal/arc inspection surfaces without enabling closure

**Files:**
- Modify: `bridge/director_panels.py`
- Modify: `bridge/director_callbacks.py`
- Modify: `bridge/miniapp_director.py`
- Test: `tests/test_director_panels.py`
- Test: `tests/test_miniapp_director.py`

**Interfaces:**
- Director Room can inspect/edit Ending Goal and arc metadata.
- Closed Story execution controls remain hidden/disabled until Phase 4.
- Finale-ready status may be shown only as internal/diagnostic state if needed; no button enters incomplete ending pipeline from production UI.

- [ ] **Step 1: Write panel tests**

Assert Ending Goal/history and arcs are visible/editable only to authenticated actor; raw hidden model prompts are absent; Closed Story start/finale controls are not exposed yet.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_panels.py tests/test_miniapp_director.py`  
Expected: FAIL on missing arc/goal surfaces.

- [ ] **Step 3: Implement bounded views/mutations**

Reuse EndingService and DirectorService; no direct SQL from adapters.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_director_panels.py tests/test_miniapp_director.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/director_panels.py bridge/director_callbacks.py bridge/miniapp_director.py tests/test_director_panels.py tests/test_miniapp_director.py
git commit -m "feat: expose story arcs and ending goals"
```

### Task 7: Phase 3 verification and internal documentation

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `CHANGELOG.md`
- Do not advertise Closed Story in user docs yet.

- [ ] **Step 1: Document developer lifecycle contracts**

Document checkpoint atomicity, revision invalidation, and the fact that Phase 3 foundation is not yet a complete user-facing Closed Story feature.

- [ ] **Step 2: Run Phase 3 gates**

Run:
```bash
python -m pytest -q tests/test_narrative_arcs.py tests/test_ending_state.py tests/test_director_ending.py tests/test_finale_readiness.py tests/test_narrative_checkpoints.py tests/test_ending_recovery.py tests/test_director_panels.py tests/test_miniapp_director.py
python -m ruff check bridge tests
python -m ruff format --check bridge tests
python tools/static_analysis.py
git diff --check
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add CONTRIBUTING.md CHANGELOG.md
git commit -m "docs: record closed story foundation"
```

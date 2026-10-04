# Narrative Engine Phase 4: Finale, Epilogue, and Hard Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Closed Story pipeline from FINALE through resolution, separate epilogue generation, durable recovery, and immutable CLOSED-session enforcement.

**Architecture:** Existing Story generation remains the prose owner during FINALE. After every committed finale turn, a synchronous ending reconciliation decides whether resolution conditions are actually satisfied. Once resolved, a dedicated epilogue workflow atomically advances to `EPILOGUE_PENDING`, obtains a Director brief, generates one separate Story-model epilogue, commits it with durable operation/message identity, then advances to CLOSED; Telegram delivery is recovered independently from story truth.

**Tech Stack:** Python 3.11, SQLite, pytest, existing Story ProviderPort, DirectorService, delivery recovery, durable operations, Telegram/Mini App panels.

**Spec:** `docs/superpowers/specs/2026-10-03-narrative-engine-ai-director-design.md`

## Global Constraints

- FINALE may span multiple committed Story turns; only reconciliation can establish `RESOLUTION_COMMITTED`.
- Committed Story beats Director intention.
- Resolution is never regenerated merely because epilogue planning/generation/delivery failed.
- `RESOLUTION_COMMITTED → EPILOGUE_PENDING` is a durable transition before epilogue work starts.
- Epilogue is a separate Story-model generation based on a Director-produced bounded brief.
- CLOSED is stored only after resolution, current reconciliation, and committed epilogue.
- Telegram delivery failure cannot reopen, rewind, or regenerate a committed ending.
- Closed original sessions make zero provider calls for blocked operations.
- Closed original sessions do not allow fresh image/model generation, including `/imagine`.
- Read-only history/status/usage/Director surfaces and delivery recovery remain available.
- Alternate Ending is not implemented until Phase 5.

## Review Focus

- Process restarts after epilogue commit but before Telegram delivery → session remains CLOSED and only delivery resumes.
- Epilogue provider fails after resolution commit → state remains EPILOGUE_PENDING and resolution is never called again.
- Finale Story turn does not satisfy resolution conditions → lifecycle remains FINALE and another normal finale turn is allowed.
- Closed-session command disguised through mention/queued recovery → canonical guard still blocks new generation.
- Duplicate ending worker/retry → durable operation IDs ensure one committed epilogue and one closure transition.

---

### Task 1: Ending reconciliation after committed FINALE turns

**Files:**
- Create: `bridge/ending_reconciliation.py`
- Modify: `bridge/narrative_reconciliation.py`
- Modify: `bridge/ending_service.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/regeneration.py`
- Modify: `bridge/edit_messages.py`
- Test: `tests/test_finale_resolution.py`
- Test: `tests/test_narrative_reconciliation.py`

**Interfaces:**
- Produces:
  - `reconcile_finale_after_commit(db, api_key: str, chat_id: str, session: dict[str, str], assistant_rowid: int, *, director_service: DirectorService, provider_port: ProviderPort, app_settings: AppSettings) -> EndingState`
  - `resolution_conditions_satisfied(state: NarrativeState, arcs: list[NarrativeArc], decision: DirectorDecision | None) -> bool`
- Called only after the assistant Story row is committed.
- Ensures Narrative State is current through `assistant_rowid` synchronously before changing ending lifecycle.

- [ ] **Step 1: Write resolution tests**

Assert:
- unresolved committed finale keeps lifecycle FINALE;
- committed Story that contradicts planned outcome updates Narrative/Arc reality first and then evaluates resolution;
- current reconciled resolution advances exactly once to RESOLUTION_COMMITTED;
- provider/reconciliation failure never advances ending lifecycle;
- edit/regeneration invalidating the finale row invalidates later resolution state before another ending action.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_finale_resolution.py tests/test_narrative_reconciliation.py`  
Expected: FAIL.

- [ ] **Step 3: Implement synchronous finale reconciliation**

Reuse narrative/arc reconciliation contracts; do not duplicate transcript parsing in ending code.

- [ ] **Step 4: Integrate after Story commit**

In `generate_and_store_reply`, invoke ending reconciliation after the local turn commit and before any epilogue orchestration. Preserve normal Telegram reply delivery even when ending reconciliation degrades, unless lifecycle correctness requires stopping epilogue work.

- [ ] **Step 5: Run tests**

Run: `python -m pytest -q tests/test_finale_resolution.py tests/test_narrative_reconciliation.py tests/test_generation_delivery_port.py`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/ending_reconciliation.py bridge/narrative_reconciliation.py bridge/ending_service.py bridge/message_commands.py bridge/regeneration.py bridge/edit_messages.py tests/test_finale_resolution.py tests/test_narrative_reconciliation.py
git commit -m "feat: reconcile finale resolution"
```

### Task 2: Durable epilogue brief and `EPILOGUE_PENDING` workflow

**Files:**
- Create: `bridge/epilogue_service.py`
- Modify: `bridge/ending_repository.py`
- Modify: `bridge/ending_service.py`
- Modify: `bridge/director_service.py`
- Test: `tests/test_epilogue_service.py`
- Test: `tests/test_ending_recovery.py`

**Interfaces:**
- Produces:
  - `EpilogueBrief` dataclass with `time_scope`, `cover`, `do_not_invent`, `pov`, `expected_revision`.
  - `begin_epilogue(db, chat_id: str, session_id: str, *, expected_lifecycle_revision: int, operation_id: str) -> EndingState` atomically moves RESOLUTION_COMMITTED → EPILOGUE_PENDING and stores operation identity.
  - `build_epilogue_brief(db, api_key: str, chat_id: str, session: dict[str, str], *, director_service: DirectorService) -> EpilogueBrief`.
- Director brief usage purpose: `director_epilogue`.

- [ ] **Step 1: Write epilogue-state tests**

Assert:
- RESOLUTION_COMMITTED first atomically enters EPILOGUE_PENDING;
- crash before transaction commit leaves RESOLUTION_COMMITTED;
- duplicate operation ID returns existing pending state;
- Director brief failure leaves EPILOGUE_PENDING and no Story epilogue call;
- brief may choose immediate/days/months/years but includes explicit do-not-invent boundaries from current committed state;
- no write transaction spans Director call.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_epilogue_service.py tests/test_ending_recovery.py`  
Expected: FAIL.

- [ ] **Step 3: Implement pending transition and Director brief**

Use durable `ending_state.epilogue_operation_id`. Do not infer pending work from logs/jobs alone.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_epilogue_service.py tests/test_ending_recovery.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/epilogue_service.py bridge/ending_repository.py bridge/ending_service.py bridge/director_service.py tests/test_epilogue_service.py tests/test_ending_recovery.py
git commit -m "feat: prepare durable epilogue workflow"
```

### Task 3: Separate Story-model epilogue generation and commit

**Files:**
- Modify: `bridge/epilogue_service.py`
- Modify: `bridge/generation.py`
- Modify: `bridge/response_delivery.py`
- Test: `tests/test_epilogue_generation.py`
- Test: `tests/test_transcript_delivery_recovery.py`

**Interfaces:**
- Produces:
  - `generate_epilogue(db, token: str, api_key: str, chat_id: str, session: dict[str, str], brief: EpilogueBrief, *, provider_port: ProviderPort, delivery_port: DeliveryPort, app_settings: AppSettings) -> int`
- Returns committed assistant rowid.
- Story usage purpose for prose remains Story/ending-specific attribution chosen by existing usage layer; Director brief remains `director_epilogue`.
- `ending_state.epilogue_committed_rowid` is written in the same local commit that persists the epilogue message.

- [ ] **Step 1: Write generation tests**

Assert:
- Story model, not Director/Utility, writes epilogue prose;
- epilogue prompt contains committed final state and brief but not raw Director hidden history;
- successful local transaction inserts exactly one assistant epilogue and records its rowid;
- retry after committed epilogue performs zero Story calls;
- provider failure before commit leaves no assistant row and keeps EPILOGUE_PENDING;
- delivery failure after commit does not trigger Story regeneration.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_epilogue_generation.py tests/test_transcript_delivery_recovery.py`  
Expected: FAIL.

- [ ] **Step 3: Implement epilogue prompt/generation**

Use a dedicated prompt builder in `generation.py` or `epilogue_service.py` that reuses roleplay output rendering but does not treat epilogue as a user turn. Keep source transcript immutable.

- [ ] **Step 4: Integrate delivery recovery**

Reuse existing committed assistant delivery identity/checkpoints. Do not build a second Telegram retry system.

- [ ] **Step 5: Run tests**

Run: `python -m pytest -q tests/test_epilogue_generation.py tests/test_transcript_delivery_recovery.py tests/test_response_delivery.py`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/epilogue_service.py bridge/generation.py bridge/response_delivery.py tests/test_epilogue_generation.py tests/test_transcript_delivery_recovery.py
git commit -m "feat: generate separate story epilogues"
```

### Task 4: Atomic closure and restart recovery

**Files:**
- Create: `bridge/ending_recovery.py`
- Modify: `bridge/ending_repository.py`
- Modify: `bridge/ending_service.py`
- Modify: `bridge/main.py`
- Modify: `bridge/background.py`
- Modify: `bridge/operation_recovery.py`
- Test: `tests/test_closed_story_recovery.py`
- Test: `tests/test_operation_recovery.py`

**Interfaces:**
- Produces:
  - `close_after_epilogue(db, chat_id: str, session_id: str, *, expected_lifecycle_revision: int, epilogue_rowid: int) -> bool`
  - `recover_ending_workflow(db, chat_id: str, session_id: str) -> EndingRecoveryAction`
  - `recoverable_endings(db) -> list[tuple[str, str]]`
  - `queue_startup_ending_recovery(..., *, app_settings: AppSettings) -> int`
- Recovery decisions use durable ending fields: checkpoint ID, finale operation ID/committed rowid, resolution rowid, epilogue operation ID/committed rowid.
- Startup queues bounded recovery only after services/providers are composed; migration/schema initialization itself makes no provider calls.
- Closure does not depend on Telegram delivery state.

- [ ] **Step 1: Write recovery-matrix tests**

Cover:
- OPEN/FINALE_READY without checkpoint → reassess;
- FINALE + checkpoint + no committed finale → reuse checkpoint/direction;
- FINALE + committed finale unresolved → reconcile, do not regenerate;
- RESOLUTION_COMMITTED → enter/resume EPILOGUE_PENDING;
- EPILOGUE_PENDING + no committed epilogue → brief/generate only;
- EPILOGUE_PENDING + committed rowid → no Story call, finalize lifecycle;
- CLOSED + undelivered epilogue → delivery recovery only;
- process restart never rewinds CLOSED.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_closed_story_recovery.py tests/test_operation_recovery.py`  
Expected: FAIL.

- [ ] **Step 3: Implement recovery state machine and startup admission**

Return explicit actions; keep generation/delivery owners responsible for performing the action. On startup, scan only durable nonterminal EndingState rows and queue bounded recovery work after application composition. No “scan latest assistant message and guess” fallback and no provider work during migration/database initialization.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_closed_story_recovery.py tests/test_operation_recovery.py tests/test_delivery_review_regressions.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/ending_recovery.py bridge/ending_repository.py bridge/ending_service.py bridge/main.py bridge/background.py bridge/operation_recovery.py tests/test_closed_story_recovery.py tests/test_operation_recovery.py
git commit -m "feat: recover closed story endings durably"
```

### Task 5: Canonical CLOSED-session guard

**Files:**
- Create: `bridge/closed_session_guard.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/command_routes.py`
- Modify: `bridge/light_novel_callbacks.py`
- Modify: `bridge/image_generation.py`
- Modify: `bridge/image_messages.py`
- Modify: `bridge/voice_jobs.py`
- Modify: `bridge/director_callbacks.py`
- Test: `tests/test_closed_session_guard.py`
- Test: `tests/test_light_novel_flow.py`
- Test: `tests/test_image_generation.py`
- Test: `tests/test_voice_conversation_boundary.py`

**Interfaces:**
- Produces:
  - `CLOSED_STORY_MESSAGE = "This story has ended. Please start new story."`
  - `is_session_closed(db, chat_id: str, session_id: str) -> bool`
  - `guard_story_mutation(db, chat_id: str, session_id: str, action: str) -> None`, raising `ClosedStoryError`.
- Delivery recovery of already committed ending bypasses mutation guard through an explicit recovery path, not a generic allowlist escape.

- [ ] **Step 1: Write guard tests**

Assert after CLOSED:
- ordinary text returns exact fixed response and zero provider calls;
- regen/edit/continue blocked;
- Light Novel choice blocked;
- Director reassess/objective mutation blocked;
- voice/photo story turns blocked;
- `/imagine` and all fresh image generation blocked;
- status/usage/history/View Ending allowed;
- committed delivery recovery allowed;
- queued/recovered operations and @bot command normalization cannot bypass the closed-session mutation guard.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_closed_session_guard.py`  
Expected: FAIL.

- [ ] **Step 3: Implement one guard and wire all mutation entry points**

Do not duplicate lifecycle checks in each feature. Entry points call the canonical guard before provider/admission work.

- [ ] **Step 4: Run boundary tests**

Run: `python -m pytest -q tests/test_closed_session_guard.py tests/test_light_novel_flow.py tests/test_image_generation.py tests/test_voice_conversation_boundary.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/closed_session_guard.py bridge/message_commands.py bridge/command_routes.py bridge/light_novel_callbacks.py bridge/image_generation.py bridge/image_messages.py bridge/voice_jobs.py bridge/director_callbacks.py tests/test_closed_session_guard.py
git commit -m "feat: hard close completed stories"
```

### Task 6: Enable Closed Story UI and finale confirmation

**Files:**
- Modify: `bridge/narrative_panels.py`
- Create: `bridge/ending_panels.py`
- Create: `bridge/ending_callbacks.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/director_panels.py`
- Modify: `bridge/miniapp_director.py`
- Test: `tests/test_ending_panels.py`
- Test: `tests/test_miniapp_director.py`
- Test: `tests/test_panel_callback_singleflight.py`

**Interfaces:**
- Narrative settings now expose Open-ended / Closed Story and Require confirmation before finale.
- `FINALE_READY` confirmation callback is bound to the exact story/lifecycle revision.
- Continuing the story before confirming invalidates the old confirmation.

- [ ] **Step 1: Write UI/revision tests**

Assert:
- Closed Story option now visible;
- optional Ending Goal entry lives in Director Room;
- confirmation panel shows only for current FINALE_READY revision;
- double click enters FINALE once;
- stale confirmation after new Story is rejected and readiness returns/open reassesses;
- CLOSED view shows View Ending/New Story, but not Alternate Ending until Phase 5.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_ending_panels.py tests/test_panel_callback_singleflight.py`  
Expected: FAIL.

- [ ] **Step 3: Implement UI/callbacks**

Adapters call EndingService/FinaleService only. No direct lifecycle SQL.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_ending_panels.py tests/test_miniapp_director.py tests/test_panel_callback_singleflight.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/narrative_panels.py bridge/ending_panels.py bridge/ending_callbacks.py bridge/panel_callback_routes.py bridge/director_panels.py bridge/miniapp_director.py tests/test_ending_panels.py tests/test_miniapp_director.py
git commit -m "feat: enable closed story controls"
```

### Task 7: Usage, documentation, and full ending verification

**Files:**
- Modify: `docs/user-guide.md`
- Modify: `docs/miniapp.md`
- Modify: `docs/token-usage.md`
- Modify: `docs/operations.md`
- Modify: `bridge/help_details.py`
- Modify: `CHANGELOG.md`
- Test: `tests/test_token_usage.py`

**Interfaces:**
- Usage distinguishes `director_ending` and `director_epilogue`; Story epilogue remains actual Story-model consumption.
- Docs state hard-close exact response, separate epilogue, no fresh generation after closure, and delivery-recovery semantics.

- [ ] **Step 1: Add usage/docs tests where executable**

Assert usage ledger records Director ending/epilogue purposes and no extra calls on committed-delivery retry.

- [ ] **Step 2: Update shipped docs/help**

Document full Closed Story feature. Explicitly state Alternate Ending is not yet available until Phase 5.

- [ ] **Step 3: Run Phase 4 gates**

Run:
```bash
python -m pytest -q tests/test_finale_resolution.py tests/test_epilogue_service.py tests/test_epilogue_generation.py tests/test_closed_story_recovery.py tests/test_closed_session_guard.py tests/test_ending_panels.py tests/test_token_usage.py tests/test_transcript_delivery_recovery.py tests/test_operation_recovery.py
python -m ruff check bridge tests
python -m ruff format --check bridge tests
python tools/static_analysis.py
git diff --check
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add docs/user-guide.md docs/miniapp.md docs/token-usage.md docs/operations.md bridge/help_details.py CHANGELOG.md tests/test_token_usage.py
git commit -m "docs: document closed story endings"
```

# Narrative Engine Phase 2: Canonical AI Director Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the constrained autonomous AI Director, dedicated Director model/reasoning route, adaptive cadence, Director Room, and canonical Group Director integration on top of the Phase 1 Narrative Engine.

**Architecture:** A new `DirectorService` reads only committed/reconciled Narrative State, calls the configured Director route outside transactions, parses versioned structured proposals, validates them through one canonical validator, and persists accepted decisions with CAS semantics. Telegram/Mini App Director Room surfaces current plans and manual overrides; the existing Group Director consumes accepted direction instead of maintaining a competing hidden story director.

**Tech Stack:** Python 3.11, SQLite, pytest, ProviderPort, existing task-model routing, existing Telegram/Mini App panel patterns, existing background executor.

**Spec:** `docs/superpowers/specs/2026-10-03-narrative-engine-ai-director-design.md`

## Global Constraints

- Director is constrained autonomous: it may propose scene/thread/POV/pacing/arc direction but may not write roleplay prose or mutate committed facts directly.
- Director structured output schema version 1 is the only accepted version in this phase; unsupported versions are hard rejects and do not enter repair.
- Malformed schema-version-1 output gets at most one bounded repair attempt.
- Director calls never run inside SQLite write transactions.
- Stale proposals are rejected, never merged heuristically.
- Ordinary RP must remain usable when Director is degraded; Narrative Style must not fall back to Player-centric.
- Director model inherits Utility, then Story/default, when unset.
- Director reasoning is independent from Utility reasoning.
- Director Room plans remain hidden from roleplay characters/transcript.
- One accepted mutation per expected narrative revision; no last-writer-wins.
- No Closed Story/epilogue/Alternate Ending user flow is enabled in this phase.

## Review Focus

- Director result arrives after an edit/reconciliation advanced state → reject stale proposal and keep current accepted direction.
- Unsupported `schema_version` → hard reject with zero repair calls.
- Director model unavailable → Story still generates from existing policy/state with degraded status.
- Repeated Reassess button taps → one admitted Director operation, no duplicate decisions.
- Manual persistent Director objective exists → later AI reassessment cannot silently remove it.

---

### Task 1: Director model target and reasoning controls

**Files:**
- Modify: `bridge/model_selection.py`
- Modify: `bridge/provider_panels.py`
- Modify: `bridge/provider_callbacks.py`
- Modify: `bridge/status_panels.py`
- Test: `tests/test_provider_panel_contract.py`
- Test: `tests/test_provider_reset_enhancements.py`
- Test: `tests/test_enum_provider_routing.py`
- Create: `tests/test_director_model_selection.py`

**Interfaces:**
- Produces:
  - `director_reasoning_key(chat_id: str, session_id: str) -> str`
  - `director_reasoning_for_session(db, chat_id: str, session_id: str) -> int`
  - `set_director_reasoning(db, chat_id: str, session_id: str, budget: int) -> int`
- Extend `set_model_target_selection(..., target: str)` and `get_model_target_selection(...)` to accept `story | utility | director`.
- Director route uses existing `task_model_for_session(..., "director")` fallback chain.

- [ ] **Step 1: Write failing model-routing tests**

Assert:
- target selector accepts Director;
- unset Director resolves Utility, then Story;
- explicitly selected Director does not rewrite Story/Utility;
- Director reasoning has independent 0..32000 validation;
- provider panel displays Story, Utility, Director and each effective route clearly.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_model_selection.py tests/test_provider_panel_contract.py tests/test_enum_provider_routing.py`  
Expected: FAIL because Director target/reasoning is absent.

- [ ] **Step 3: Implement model/reasoning selection**

Reuse generic task-model metadata. Add only the target-selection/UI logic and Director-specific reasoning key.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest -q tests/test_director_model_selection.py tests/test_provider_panel_contract.py tests/test_provider_reset_enhancements.py tests/test_enum_provider_routing.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/model_selection.py bridge/provider_panels.py bridge/provider_callbacks.py bridge/status_panels.py tests/test_director_model_selection.py tests/test_provider_panel_contract.py tests/test_provider_reset_enhancements.py tests/test_enum_provider_routing.py
git commit -m "feat: add director model and reasoning controls"
```

### Task 2: Director proposal contracts, parser, and canonical validator

**Files:**
- Create: `bridge/director_contracts.py`
- Create: `bridge/director_validation.py`
- Test: `tests/test_director_validation.py`

**Interfaces:**
- Consumes: `NarrativePolicy`, `NarrativeState`, scene/thread values from Phase 1.
- Produces:
  - `DirectorProposal` dataclass with `schema_version`, `action`, `expected_revision`, optional scene/thread/viewpoint/POV/purpose/direction fields.
  - `parse_director_proposal(raw: str) -> DirectorProposal`
  - `validate_director_proposal(proposal: DirectorProposal, *, policy: NarrativePolicy, state: NarrativeState, valid_characters: set[str], ending_state: str = "open") -> None`
  - `DirectorProposalError` with stable categories for parse/version/stale/policy/reference/lifecycle failures.

- [ ] **Step 1: Write contract/validator tests**

Pin:
- schema version 1 accepted;
- unsupported/missing version rejected without repair eligibility;
- unknown optional fields ignored;
- missing action/expected revision rejected;
- invalid POV/viewpoint/thread rejected;
- structural user-decision mutation rejected;
- CLOSED lifecycle rejected even though Closed Story UI is not yet shipped;
- expected revision mismatch categorized stale.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_validation.py`  
Expected: FAIL because contracts/validator do not exist.

- [ ] **Step 3: Implement parser and validator**

Keep validation pure: no DB writes and no provider calls. Do not infer missing identifiers.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_director_validation.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/director_contracts.py bridge/director_validation.py tests/test_director_validation.py
git commit -m "feat: validate director proposals"
```

### Task 3: Director state/history repository and constrained service

**Files:**
- Create: `bridge/director_repository.py`
- Create: `bridge/director_service.py`
- Modify: `bridge/narrative_repository.py`
- Test: `tests/test_director_service.py`
- Test: `tests/test_repository_transactions.py`

**Interfaces:**
- Consumes: Task 2 parser/validator; Phase 1 narrative repositories/reconciliation.
- Produces:
  - `DirectorDecision` value with decision ID, expected revision, source `ai|user`, result `accepted|rejected|superseded`, and sanitized reason.
  - `load_director_state(db, chat_id: str, session_id: str) -> dict[str, object]`
  - `append_director_decision(..., decision: DirectorDecision, *, expected_state_revision: int) -> bool`
  - `DirectorService.reassess(db, api_key: str, chat_id: str, session: dict[str, str], *, provider_port: ProviderPort, app_settings: AppSettings, reason: str) -> DirectorDecision`
  - `DirectorService.accept_manual_direction(..., scope: str, direction: str, expected_revision: int) -> DirectorDecision`
- History retention: newest 200 `director_decisions` rows per session, pruned inside append transaction.

- [ ] **Step 1: Write service tests**

Assert:
- service calls `ensure_narrative_state_current` before provider request;
- provider call sees no active DB transaction;
- one malformed version-1 response gets one repair request; second failure marks degraded;
- unsupported version gets zero repair requests;
- valid proposal persists one accepted decision;
- stale CAS result persists no active mutation;
- 201st decision prunes oldest and keeps current `director_state`;
- provider failure records degraded state without altering accepted direction.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_service.py tests/test_repository_transactions.py`  
Expected: FAIL.

- [ ] **Step 3: Implement repository/service**

Use `provider_port.for_usage(chat_id, session_id, "director")`. Generate strict JSON with Director reasoning settings and bounded output. Persist only after parse+validation.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_director_service.py tests/test_repository_transactions.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/director_repository.py bridge/director_service.py bridge/narrative_repository.py tests/test_director_service.py tests/test_repository_transactions.py
git commit -m "feat: add constrained ai director service"
```

### Task 4: Adaptive Director cadence and event triggers

**Files:**
- Create: `bridge/director_cadence.py`
- Modify: `bridge/narrative_reconciliation.py`
- Modify: `bridge/extension_registry.py`
- Modify: `bridge/main.py`
- Test: `tests/test_director_cadence.py`
- Test: `tests/test_memory_completion_safety.py`

**Interfaces:**
- Produces:
  - `director_due(state: dict[str, object], narrative_state: NarrativeState, *, event: str = "") -> bool`
  - `record_director_run(..., completed_story_turn: int, phase: str, now: float) -> None`
  - adaptive max intervals: stable/setup 10, development 6, escalation 4, climax/finale 2.
- Fixed/custom cadence override comes from Phase 1 NarrativeSettings.
- Event triggers include scene transition, material reconciliation change, thread resolution, style change, stale direction, and explicit reassess.

- [ ] **Step 1: Write cadence tests**

Assert:
- Player-centric stable turns below cap make zero Director calls;
- phase-specific caps match 10/6/4/2;
- fixed override wins over Adaptive;
- material scene/thread transition triggers immediately;
- repeated post-retain hooks while an operation is in-flight do not enqueue duplicate reassessment.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_cadence.py`  
Expected: FAIL.

- [ ] **Step 3: Implement cadence owner and background admission**

Use one per-session admitted/in-flight Director operation. Do not call Director synchronously on every normal reply.

- [ ] **Step 4: Run cadence/concurrency tests**

Run: `python -m pytest -q tests/test_director_cadence.py tests/test_memory_completion_safety.py tests/test_panel_callback_singleflight.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/director_cadence.py bridge/narrative_reconciliation.py bridge/extension_registry.py bridge/main.py tests/test_director_cadence.py
git commit -m "feat: add adaptive director cadence"
```

### Task 5: Director Room Telegram surface and manual overrides

**Files:**
- Create: `bridge/director_panels.py`
- Create: `bridge/director_callbacks.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/command_routes.py`
- Modify: `bridge/help_details.py`
- Test: `tests/test_director_panels.py`
- Test: `tests/test_callback_domains.py`
- Test: `tests/test_panel_callback_singleflight.py`

**Interfaces:**
- Consumes: `DirectorService`, Director repository, Narrative State/Policy.
- Produces `/director` panel with:
  - current phase, scene, POV, thread, accepted direction;
  - Reassess now;
  - Change active thread;
  - Edit one-scene direction;
  - Edit persistent objective;
  - Decision history;
  - links to Narrative Style.
- Manual directions require explicit scope `next_scene | persistent`.

- [ ] **Step 1: Write panel/ownership tests**

Assert:
- panel contains no prompt secrets/model raw output;
- Reassess is actor/session-bound and single-flight;
- one-scene manual direction expires after accepted scene transition;
- persistent objective survives AI reassessment;
- stale panel callback cannot overwrite newer Director revision.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_director_panels.py tests/test_panel_callback_singleflight.py`  
Expected: FAIL.

- [ ] **Step 3: Implement panels/callbacks**

Keep callbacks thin; all writes delegate to DirectorService/repository owners.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_director_panels.py tests/test_callback_domains.py tests/test_panel_callback_singleflight.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/director_panels.py bridge/director_callbacks.py bridge/panel_callback_routes.py bridge/command_routes.py bridge/help_details.py tests/test_director_panels.py tests/test_callback_domains.py
git commit -m "feat: add director room controls"
```

### Task 6: Mini App Director Room

**Files:**
- Create: `bridge/miniapp_director.py`
- Modify: `bridge/miniapp_http.py`
- Modify: `bridge/miniapp_assets/app.js`
- Modify: `bridge/miniapp_assets/index.html`
- Test: `tests/test_miniapp_director.py`
- Test: `tests/miniapp_browser_fixture.py`

**Interfaces:**
- Read endpoint returns sanitized current Director/Narrative state.
- Mutation endpoints reuse the same DirectorService methods as Telegram; no duplicate business logic.
- Actor/chat/session identity is derived from authenticated Mini App context.

- [ ] **Step 1: Write API tests**

Assert unauthorized identity cannot read/mutate another chat/session, stale revision returns conflict, Reassess reuses admitted operation, and history is bounded/sanitized.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_miniapp_director.py`  
Expected: FAIL.

- [ ] **Step 3: Implement API/UI**

Add Director Room navigation under Manage/Advanced without exposing hidden plan data on Home.

- [ ] **Step 4: Run API/browser smoke**

Run:
```bash
python -m pytest -q tests/test_miniapp_director.py
MINIAPP_JSDOM_ROOT=tests/miniapp-ui PYTHON=python node --experimental-vm-modules tools/miniapp_ui_smoke.mjs
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/miniapp_director.py bridge/miniapp_http.py bridge/miniapp_assets/app.js bridge/miniapp_assets/index.html tests/test_miniapp_director.py tests/miniapp_browser_fixture.py
git commit -m "feat: add mini app director room"
```

### Task 7: Canonicalize Group Director and legacy `/group goal`

**Files:**
- Modify: `bridge/group_director_service.py`
- Modify: `bridge/director_goals.py`
- Modify: `bridge/group_commands.py`
- Modify: `bridge/director_goal_panel.py`
- Test: `tests/test_group_director_service.py`
- Test: `tests/test_group_director.py`
- Test: `tests/test_director_goals.py`

**Interfaces:**
- Group Director consumes accepted canonical direction/objective via a narrow `DirectorPolicy` adapter.
- `/group goal` reads/writes the same persistent Director objective shown in Director Room.
- No second Director model call occurs solely for group speaker selection unless the canonical Director cadence already requires reassessment.

- [ ] **Step 1: Write canonicalization tests**

Assert:
- `/group goal` and Director Room see the same objective;
- changing one updates the other;
- World-driven accepted direction constrains group speaker selection;
- user is not selected merely because they just spoke;
- Director failure still falls back to existing safe group selection.

- [ ] **Step 2: Run tests**

Run: `python -m pytest -q tests/test_group_director_service.py tests/test_group_director.py tests/test_director_goals.py`  
Expected: FAIL on old separate behavior.

- [ ] **Step 3: Modify group adapter**

Keep `GroupDirectorService` bounded to speaker selection; remove story-planning responsibility now owned by canonical DirectorService.

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q tests/test_group_director_service.py tests/test_group_director.py tests/test_director_goals.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/group_director_service.py bridge/director_goals.py bridge/group_commands.py bridge/director_goal_panel.py tests/test_group_director_service.py tests/test_group_director.py tests/test_director_goals.py
git commit -m "refactor: make narrative director canonical"
```

### Task 8: Phase 2 documentation and verification

**Files:**
- Modify: `docs/user-guide.md`
- Modify: `docs/configuration.md`
- Modify: `docs/miniapp.md`
- Modify: `docs/token-usage.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Documentation covers only shipped Director behavior; Closed Story remains documented as future/not yet enabled.

- [ ] **Step 1: Update docs**

Document Director route inheritance, Director reasoning, cadence, Director Room, usage purposes, degraded behavior, and canonical Group Director relationship.

- [ ] **Step 2: Run Phase 2 gates**

Run:
```bash
python -m pytest -q tests/test_director_model_selection.py tests/test_director_validation.py tests/test_director_service.py tests/test_director_cadence.py tests/test_director_panels.py tests/test_miniapp_director.py tests/test_group_director.py tests/test_group_director_service.py
python -m ruff check bridge tests
python -m ruff format --check bridge tests
python tools/static_analysis.py
git diff --check
```
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add docs/user-guide.md docs/configuration.md docs/miniapp.md docs/token-usage.md CHANGELOG.md
git commit -m "docs: document ai director"
```

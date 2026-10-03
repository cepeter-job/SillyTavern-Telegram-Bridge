# Narrative Engine Phase 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the persistent Narrative Policy/State foundation, integrate Narrative Style into `/character`, and make Story, Light Novel, and Group generation consume the same session narrative policy without adding an AI Director yet.

**Architecture:** Migration 10 creates the normalized Narrative Engine tables and retires the legacy `director_goals` table after verified data copy. New focused modules own validated narrative values, settings/defaults, repository access, policy rendering, and revision-aware reconciliation. Existing workflow owners remain in place: `conversation_setup*` owns setup, `generation.py` owns Story prompt assembly, `light_novel_service.py` owns choices, and `group_director_service.py` owns group speaker behavior.

**Tech Stack:** Python 3.11, SQLite, pytest, existing Telegram panel/callback framework, existing ProviderPort and background executor.

**Spec:** `docs/superpowers/specs/2026-10-03-narrative-engine-ai-director-design.md`

## Global Constraints

- Player-centric is the compatibility/default preset for existing and new sessions unless explicitly changed.
- Personal Narrative Style defaults are setup-prefill only; Apply writes a session-owned `narrative_settings` row.
- World-driven and Observer allow free off-screen storytelling with no forced return-to-user rule.
- Shipped presets use physical-continuity user control; no code path may invent user dialogue, thoughts, commitments, emotional conclusions, or consequential decisions.
- `I am not MC` remains independent; World-driven/Observer may recommend it but never toggle it.
- No migration/startup path may contact a provider.
- Never hold a SQLite write transaction across provider/network I/O.
- Migration 10 retires `director_goals` with copy + content verification + drop; no compatibility read/write path remains.
- Do not expose Closed Story, autonomous AI Director, or Alternate Ending UI before their owning phases ship.
- Tests must mock external APIs and may use only loopback fixtures.

## Review Focus

- Existing session with no old narrative metadata after Migration 10 → explicit Player-centric session row and unchanged Story behavior.
- User changes personal default after a session already exists → existing session remains unchanged.
- World-driven off-screen Light Novel scene → choices steer narrative threads rather than fabricate user actions.
- Reconciliation result finishes after transcript revision changed → stale result is rejected with no state overwrite.
- Reset/delete path → story-derived narrative rows clear/cascade while session Narrative Style configuration survives reset.

---

### Task 1: Migration 10 and canonical narrative repositories

**Files:**
- Create: `bridge/narrative_schema.py`
- Create: `bridge/narrative_repository.py`
- Create: `bridge/narrative_values.py`
- Modify: `bridge/schema.py`
- Modify: `bridge/director_goal_repository.py`
- Modify: `bridge/director_goals.py`
- Test: `tests/test_narrative_migrations.py`
- Test: `tests/test_repository_transactions.py`
- Test: `tests/test_director_goals.py`

**Interfaces:**
- Produces: immutable value types `NarrativeSettings`, `NarrativeState`, `NarrativeScene`, `NarrativeThread`.
- Produces: `migrate_narrative_engine_foundation(db: sqlite3.Connection) -> None`.
- Produces repository functions that require an active caller transaction for writes:
  - `load_narrative_settings_row(db, chat_id: str, session_id: str) -> str | None`
  - `store_narrative_settings_row(db, chat_id: str, session_id: str, settings_json: str, updated_at: float) -> None`
  - `load_narrative_default_row(db, owner_user_id: str) -> str | None`
  - `store_narrative_default_row(db, owner_user_id: str, settings_json: str, updated_at: float) -> None`
  - `load_narrative_state_row(db, chat_id: str, session_id: str) -> dict[str, object] | None`
  - `upsert_narrative_state_if_fresh(db, chat_id: str, session_id: str, state_json: str, expected_state_revision: int, through_rowid: int, updated_at: float) -> bool`
  - focused scene/thread load/upsert helpers using the spec's session-local IDs and source revisions.
- Produces canonical Director objective storage through `director_state`; legacy `director_goals` table is dropped after verified copy.

- [ ] **Step 1: Write migration tests**

Add tests asserting:
- schema migration version 10 is registered after version 9;
- all 11 Narrative Engine tables exist;
- every session-owned table has a session FK with `ON DELETE CASCADE`;
- an existing session is backfilled with Player-centric `narrative_settings`;
- existing `scene_states` rows survive unchanged;
- a seeded `director_goals(chat_id, session_id, goal)` row appears in canonical Director state with identical content before the old table is absent;
- a deliberately mismatched copied goal makes migration fail before legacy storage is dropped;
- running startup again applies no migration writes or provider calls.

- [ ] **Step 2: Run migration tests to verify failure**

Run: `python -m pytest -q tests/test_narrative_migrations.py tests/test_migrations.py`  
Expected: FAIL because Migration 10 and narrative tables do not exist.

- [ ] **Step 3: Implement `narrative_values.py` and `narrative_schema.py`**

Define the exact enum/string values from the spec and Migration 10 DDL. Implement logical key contracts from spec §30.4.2, including revision/CAS columns and explicit history bounds. Enable no provider/model work.

- [ ] **Step 4: Implement `narrative_repository.py`**

Keep SQL-only ownership. Every write calls `require_active_transaction(db)`. Add tests proving repository writes reject missing caller transactions and stale CAS updates return `False` without overwriting newer state.

- [ ] **Step 5: Migrate legacy Group Director objective ownership**

Change `director_goal_repository.py` / `director_goals.py` to read/write the canonical Director objective in the new storage. Remove SQL access to the dropped legacy table.

- [ ] **Step 6: Run focused persistence tests**

Run: `python -m pytest -q tests/test_narrative_migrations.py tests/test_repository_transactions.py tests/test_director_goals.py tests/test_migrations.py`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bridge/narrative_schema.py bridge/narrative_repository.py bridge/narrative_values.py bridge/schema.py bridge/director_goal_repository.py bridge/director_goals.py tests/test_narrative_migrations.py tests/test_repository_transactions.py tests/test_director_goals.py
git commit -m "feat: add narrative engine persistence foundation"
```

### Task 2: Narrative presets, defaults, and policy rendering

**Files:**
- Create: `bridge/narrative_settings.py`
- Create: `bridge/narrative_policy.py`
- Test: `tests/test_narrative_policy.py`

**Interfaces:**
- Consumes: repository/value interfaces from Task 1.
- Produces:
  - `preset_narrative_settings(preset: str) -> NarrativeSettings`
  - `normalize_narrative_settings(values: dict[str, object]) -> NarrativeSettings`
  - `load_user_narrative_default(db, owner_user_id: str) -> NarrativeSettings`
  - `save_user_narrative_default(db, owner_user_id: str, settings: NarrativeSettings) -> None`
  - `load_session_narrative_settings(db, chat_id: str, session_id: str) -> NarrativeSettings`
  - `save_session_narrative_settings(db, chat_id: str, session_id: str, settings: NarrativeSettings) -> None`
  - `narrative_policy(settings: NarrativeSettings) -> NarrativePolicy`
  - `story_policy_text(policy: NarrativePolicy, state: NarrativeState | None = None) -> str`
  - `choice_policy_text(policy: NarrativePolicy, state: NarrativeState | None = None) -> str`
  - `group_policy_text(policy: NarrativePolicy, state: NarrativeState | None = None) -> str`

- [ ] **Step 1: Write preset and precedence tests**

Pin all four preset mappings exactly. Assert:
- any advanced value differing from a preset normalizes visible preset to `custom`;
- reselecting a preset restores canonical values;
- World-driven/Observer have free off-screen behavior;
- Observer defaults cinematic/objective;
- existing session rows do not change when personal default changes;
- missing/corrupt session rows defensively resolve Player-centric, never the current personal default;
- `I am not MC` state is never written by these functions;
- invalid user-anchored first-person configuration is rejected.

- [ ] **Step 2: Run policy tests to verify failure**

Run: `python -m pytest -q tests/test_narrative_policy.py`  
Expected: FAIL because narrative settings/policy modules do not exist.

- [ ] **Step 3: Implement values and rendering**

Render concise policy text that states POV, scene focus, off-screen freedom, and physical-continuity boundaries. Do not duplicate Grounded User policy text; consumers combine policies independently.

- [ ] **Step 4: Run policy tests**

Run: `python -m pytest -q tests/test_narrative_policy.py`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/narrative_settings.py bridge/narrative_policy.py tests/test_narrative_policy.py
git commit -m "feat: add narrative style policies"
```

### Task 3: Add Narrative Style to the `/character` setup wizard and session panel

**Files:**
- Create: `bridge/narrative_panels.py`
- Create: `bridge/narrative_callbacks.py`
- Modify: `bridge/conversation_setup.py`
- Modify: `bridge/conversation_setup_panels.py`
- Modify: `bridge/conversation_setup_callbacks.py`
- Modify: `bridge/panel_callback_routes.py`
- Modify: `bridge/command_routes.py`
- Modify: `bridge/help_details.py`
- Test: `tests/test_conversation_setup.py`
- Test: `tests/test_narrative_panels.py`
- Test: `tests/test_callback_domains.py`

**Interfaces:**
- Consumes: Task 2 settings/default APIs.
- Produces: dedicated `/narrative` panel for existing sessions and reusable panel renderers for setup.
- Setup draft fields use the exact normalized NarrativeSettings field names.
- Setup order becomes `Character → Narrative Style → Mode → Persona → World → System Prompt → Session → Review → Apply`.

- [ ] **Step 1: Extend setup tests first**

Assert:
- `begin_setup(...)` starts at `stage == "narrative"` and prefills the actor's personal default;
- choosing a preset advances to mode;
- Advanced changes keep the setup draft actor-scoped and mark preset Custom;
- Back returns to Narrative Style in the correct order;
- Cancel persists no session narrative row;
- Apply writes the target session NarrativeSettings in the same DB transaction as conversation/session setup;
- Review text includes effective POV/off-screen/user-control values;
- World-driven/Observer show an `I am not MC` recommendation but do not change Grounded User metadata.

- [ ] **Step 2: Run setup tests to verify failure**

Run: `python -m pytest -q tests/test_conversation_setup.py tests/test_narrative_panels.py`  
Expected: FAIL on the old Mode-first wizard.

- [ ] **Step 3: Modify `ConversationSetupService`**

Add narrative fields to the temporary setup JSON. Keep actor/nonce/source-epoch validation unchanged. Apply normalized settings only inside the existing final `write_transaction`.

- [ ] **Step 4: Implement reusable Narrative panel/callback owner**

Provide preset buttons first and Advanced controls below them. Add explicit `Save as my default` only on the session Narrative panel; setup selection alone never mutates the personal default.

- [ ] **Step 5: Run callback/setup tests**

Run: `python -m pytest -q tests/test_conversation_setup.py tests/test_narrative_panels.py tests/test_callback_domains.py tests/test_character_session_chain.py`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/narrative_panels.py bridge/narrative_callbacks.py bridge/conversation_setup.py bridge/conversation_setup_panels.py bridge/conversation_setup_callbacks.py bridge/panel_callback_routes.py bridge/command_routes.py bridge/help_details.py tests/test_conversation_setup.py tests/test_narrative_panels.py tests/test_callback_domains.py
git commit -m "feat: add narrative style to conversation setup"
```

### Task 4: Revision-aware narrative scene/thread reconciliation

**Files:**
- Create: `bridge/narrative_reconciliation.py`
- Create: `bridge/narrative_context.py`
- Modify: `bridge/extension_registry.py`
- Modify: `bridge/main.py`
- Test: `tests/test_narrative_reconciliation.py`
- Test: `tests/test_scene_state.py`
- Test: `tests/test_memory_completion_safety.py`

**Interfaces:**
- Consumes: NarrativeState/scene/thread repositories from Task 1 and NarrativePolicy from Task 2.
- Produces:
  - `reconcile_narrative_state_now(db, api_key: str, chat_id: str, session: dict[str, str], through_rowid: int | None = None, *, provider_port: ProviderPort, app_settings: AppSettings) -> NarrativeState`
  - `queue_narrative_reconciliation(db, chat_id: str, session: dict[str, str], *, provider_port: ProviderPort, app_settings: AppSettings) -> bool`
  - `ensure_narrative_state_current(..., through_rowid: int) -> NarrativeState`
  - `narrative_context_for_session(db, chat_id: str, session_id: str, consumer: str) -> str`
- Reconciliation provider calls use `provider_port.for_usage(chat_id, session_id, "director_reconcile")` so the extra Utility work is visible in usage reporting from its first shipped phase.

- [ ] **Step 1: Write reconciliation tests**

Use a fake Utility provider and assert:
- strict bounded JSON creates/updates one active scene/thread;
- committed transcript rowid becomes `updated_through_rowid`;
- a stale completion targeting an older rowid cannot overwrite newer state;
- an edit/reset that moves the valid boundary invalidates later scene/thread state;
- provider failure leaves committed transcript untouched and old state available;
- no provider call occurs while `db.in_transaction` is true;
- current physical `scene_states` remains separate and unchanged.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest -q tests/test_narrative_reconciliation.py tests/test_scene_state.py`  
Expected: FAIL because reconciliation does not exist.

- [ ] **Step 3: Implement bounded reconciliation**

Use recent committed transcript plus current physical Scene State as untrusted descriptive input. Parse only scene/thread/viewpoint/POV/user-presence/story-phase fields owned by Phase 1. Do not create arcs or Director plans yet.

- [ ] **Step 4: Register a post-retain background hook**

Queue reconciliation after committed replies. Keep normal reply delivery independent of reconciliation success.

- [ ] **Step 5: Run reconciliation/concurrency tests**

Run: `python -m pytest -q tests/test_narrative_reconciliation.py tests/test_scene_state.py tests/test_memory_completion_safety.py`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/narrative_reconciliation.py bridge/narrative_context.py bridge/extension_registry.py bridge/main.py tests/test_narrative_reconciliation.py tests/test_scene_state.py tests/test_memory_completion_safety.py
git commit -m "feat: track narrative scenes and threads"
```

### Task 5: Apply Narrative Policy to Story, Light Novel, and Group flows

**Files:**
- Modify: `bridge/generation.py`
- Modify: `bridge/message_commands.py`
- Modify: `bridge/regeneration.py`
- Modify: `bridge/continuation.py`
- Modify: `bridge/edit_messages.py`
- Modify: `bridge/image_messages.py`
- Modify: `bridge/light_novel_service.py`
- Modify: `bridge/group_director_service.py`
- Modify: `bridge/status_panels.py`
- Test: `tests/test_narrative_generation.py`
- Test: `tests/test_light_novel_flow.py`
- Test: `tests/test_group_director.py`
- Test: `tests/test_grounded_user.py`
- Test: `tests/test_image_generation.py`

**Interfaces:**
- Consumes: `narrative_context_for_session(...)`.
- Extend `build_chat_messages(..., narrative_context: str = "") -> list[dict]`.
- Light Novel receives `choice_policy_text` based on current user-presence/scene state.
- Group Director receives `group_policy_text`; Grounded User remains a separate appended policy.

- [ ] **Step 1: Write policy-injection tests**

Assert:
- Story prompt has one dedicated Narrative Policy block;
- Player-centric remains behaviorally compatible when no special state exists;
- World-driven prompt explicitly permits sustained off-screen scenes;
- physical continuity text permits only already-implied connective movement;
- Grounded User block remains independent;
- off-screen Light Novel choices are narrative-steering choices, not user actions;
- Group Director cannot select a speaker merely to recenter the user under World-driven/Observer.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest -q tests/test_narrative_generation.py tests/test_light_novel_flow.py tests/test_group_director.py`  
Expected: FAIL because consumers do not inject Narrative Policy.

- [ ] **Step 3: Wire Story/edit/regen/continue/image-story consumers**

Compute narrative context before prompt assembly and pass it through the new optional `build_chat_messages` parameter from normal text, edit, regeneration, continuation, and photo/image-message Story turns. Narrative Style governs image-backed roleplay Story generation too; Phase 1 does not add closed-story media guards.

- [ ] **Step 4: Wire Light Novel and Group**

When current narrative state is off-screen, generate bounded narrative-steering choices. Keep A/B/C call strategy behavior unchanged. Group speaker selection must consume policy without introducing another Director model call.

- [ ] **Step 5: Add Narrative status summary**

Show preset, POV, active scene/thread, and whether narrative state is current/stale. Do not expose hidden future Director state.

- [ ] **Step 6: Run focused and regression tests**

Run: `python -m pytest -q tests/test_narrative_generation.py tests/test_light_novel_flow.py tests/test_light_novel_generation.py tests/test_group_director.py tests/test_grounded_user.py tests/test_image_generation.py tests/test_context_window_hardening.py`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bridge/generation.py bridge/message_commands.py bridge/regeneration.py bridge/continuation.py bridge/edit_messages.py bridge/image_messages.py bridge/light_novel_service.py bridge/group_director_service.py bridge/status_panels.py tests/test_narrative_generation.py tests/test_light_novel_flow.py tests/test_group_director.py tests/test_image_generation.py
git commit -m "feat: apply narrative policy across story flows"
```

### Task 6: Reset/delete cleanup, documentation, and Phase 1 verification

**Files:**
- Modify: `bridge/message_commands.py`
- Modify: `bridge/session_service.py`
- Modify: `bridge/reset_panel.py`
- Modify: `docs/user-guide.md`
- Modify: `docs/miniapp.md` only if the Narrative panel is surfaced there in Phase 1
- Modify: `CHANGELOG.md`
- Test: `tests/test_reset_behavior.py`
- Test: `tests/test_session_delete.py`

**Interfaces:**
- Consumes: narrative repository cleanup from Task 1.
- Reset preserves `narrative_settings` and personal `narrative_defaults`; it clears story-derived narrative state.
- Session deletion relies on FK cascade and verifies no narrative orphan rows remain.

- [ ] **Step 1: Add reset/delete tests**

Assert reset clears narrative state/scenes/threads and keeps session settings. Assert deletion cascades all session-owned Narrative Engine rows. Verify no provider call is made by either operation.

- [ ] **Step 2: Run reset/delete tests to verify failure**

Run: `python -m pytest -q tests/test_reset_behavior.py tests/test_session_delete.py`  
Expected: FAIL until narrative cleanup is integrated.

- [ ] **Step 3: Integrate cleanup and update reset copy**

Keep existing Hindsight fail-closed ordering. Narrative cleanup occurs in the local committed phase after memory purge and before operation completion.

- [ ] **Step 4: Update only shipped documentation**

Document Narrative Style, setup order, presets, physical continuity, `/narrative`, and current scene/thread status. Explicitly do not document AI Director, Closed Story, or Alternate Ending as available.

- [ ] **Step 5: Run Phase 1 gates**

Run:
```bash
python -m pytest -q tests/test_narrative_migrations.py tests/test_narrative_policy.py tests/test_narrative_panels.py tests/test_narrative_reconciliation.py tests/test_narrative_generation.py tests/test_conversation_setup.py tests/test_light_novel_flow.py tests/test_group_director.py tests/test_reset_behavior.py tests/test_session_delete.py
python -m ruff check bridge tests
python -m ruff format --check bridge tests
python tools/static_analysis.py
git diff --check
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add bridge/message_commands.py bridge/session_service.py bridge/reset_panel.py docs/user-guide.md docs/miniapp.md CHANGELOG.md tests/test_reset_behavior.py tests/test_session_delete.py
git commit -m "docs: document narrative engine foundation"
```

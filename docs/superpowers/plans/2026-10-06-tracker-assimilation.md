# Tracker Assimilation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the recovered tracker feature so canonical state replaces prompt-maintained Internal States and survives the bridge's full story lifecycle.

**Architecture:** Extend existing NPC Utility publication and canonical NPC/Narrative owners with typed simulation records and a deterministic check interface. Context reads use captured source boundaries and the normal token budget. Finish the approved implementation in the GitHub-backed worktree and merge only after full verification.

**Tech Stack:** Python 3.11+, SQLite, existing provider ports and Telegram entity delivery; no new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-10-06-tracker-assimilation-design.md`

## Global Constraints

- Every mutation belongs to an existing message in the same chat/session. Ordered publication rejects older source rows. Repeating a completed row is a no-op, including rows that caused no state change.
- Utility inference runs outside write transactions. The existing memory draft acceptance validates its lease, source digest, rewrite identity and session incarnation before publishing NPC and simulation changes in one transaction.
- Relationship decay/conversion and offscreen agenda advancement run once per committed assistant turn, never once per source fragment or user message.
- Invalidating any message immediately prevents its dependent tracker state from entering prompts. Re-extraction rolls back the invalidated suffix before applying corrected evidence.
- New Python modules stay at or below 500 lines. Existing module-size exceptions may only shrink. No new runtime dependencies.
- Work from GitHub after the recovered VPS checkpoint. Preserve the closed/unmerged PR #377 branch. No live deployment or release is part of this implementation task.

## Review Focus

- Replaying an accepted no-op or publishing a user message must not advance a timer or change a score twice.
- A slow worker or historical generation must not restore facts from a rewritten or deleted future.
- Multi-part add/remove operations must preserve source order and remain bounded without silently dropping work.
- Private agendas and future payoffs must not become character knowledge or mandatory fixed token overhead.
- A retried command or restored alternate branch must not reroll an already admitted check or share mutable state.

---

### Task 1: Make canonical tracker persistence safe

**Files:** `bridge/simulation_schema.py`, `bridge/simulation_repository.py`, `bridge/simulation_service.py`, new focused `bridge/simulation_mechanics.py`, `bridge/simulation_checks.py`, `bridge/simulation_context.py`, `bridge/simulation_extraction.py`; `tests/test_simulation_trackers.py`, new `tests/test_simulation_safety.py` and `tests/test_simulation_parts.py`.

**Interfaces:** Preserve `SimulationService.state`, `apply_payload(..., source_rowid=...)`, `rollback_from_row`, `purge_session`, `context_for_prompt(..., through_rowid=None)`, and `perform_check`. Add a completed-row receipt/progress contract and valid-source checks. Context can accept reader scope and resolves pending NPC invalidation. Repositories remain caller-transaction-owned.

- [x] Add failing tests for replay, empty rows, user-message ticking, nonexistent/cross-session/older source rows, invalidated reads, and historical check modifiers. Use explicit expected numeric values from the spec.
- [x] Run these tests and record the failing behavior before changes.
- [x] Implement source fencing, bounded receipt/revision handling and SQL historical reconstruction; split mechanics, checks and context into focused modules.
- [x] Add failing multipart tests for add-then-remove, remove-then-add, more than 32 total updates, invalid booleans and maximal records; normalize with chronological operation semantics and enforced aggregate bounds.
- [x] Run `python -m pytest -q tests/test_simulation_trackers.py tests/test_simulation_safety.py tests/test_simulation_parts.py tests/test_simulation_extraction.py` and commit only after it passes.

### Task 2: Integrate canonical owners and story lifecycle

**Files:** new `bridge/simulation_projection.py`, `bridge/simulation_snapshot.py`; `bridge/memory_draft_publish.py`, `bridge/edit_messages.py`, `bridge/regeneration.py`, `bridge/response_variants.py`, `bridge/continuation.py`, `bridge/message_commands.py`, `bridge/sync_core.py`, `bridge/narrative_checkpoint_capture.py`, `bridge/alternate_ending_restore.py`; new `tests/test_simulation_lifecycle.py` and `tests/test_simulation_projection.py`.

**Interfaces:** `snapshot_simulation_state(db, chat_id, session_id, through_rowid) -> dict` and `restore_simulation_snapshot(db, chat_id, session_id, payload) -> None` use explicit remappable source metadata. `project_simulation_state(...)` links canonical NPC identities and narrative arc/thread projections within the accepted publication transaction.

- [x] Add and observe failing real-SQLite tests for reset, deletion, edit/regen/continuation invalidation, snapshot cutoff/remapping/branch isolation and alias projections.
- [x] Wire the existing canonical mutation boundaries; preserve caller-owned transactions and the checkpoint's 1 MiB limit.
- [x] Extend NPC relationship/agenda projections and narrative quest/foreshadow links without introducing a second plot authority.
- [x] Run the new lifecycle/projection suites plus existing alternate-ending, NPC and transcript mutation tests; commit after passing.

### Task 3: Complete extraction, context and usable checks

**Files:** `bridge/npc_extraction.py`, `bridge/generation.py`, story callers, `bridge/light_novel_service.py`, `bridge/director_prompt.py`, context-compaction modules, `bridge/telegram_output.py`; new `bridge/simulation_prompt.py`, `bridge/simulation_commands.py`; `bridge/application_composition.py`; new `tests/test_simulation_integration.py`, `tests/test_simulation_commands.py`, `tests/test_simulation_output.py`.

**Interfaces:** NPC extraction returns its existing payload with a bounded `simulation` object. `build_chat_messages(..., simulation_context="")` treats it as untrusted optional context. The new command route uses the existing durable operation identity; explicit `/check` syntax is validated before admitting one user action.

- [x] Add and observe failing tests showing the real NPC worker publishes parsed simulation state only after full-row acceptance, rejects malformed payloads and preserves quoted legacy numeric evidence without guessing.
- [x] Add the shared Utility instructions/parser accumulator and bounded prior-state input. Reuse the existing job and provider call.
- [x] Add and observe failing tests for ordinary/historical/choice/Director context, compaction and legacy prompt-template suppression.
- [x] Wire the shared context and fixed story-output policy; discard complete/incomplete Internal States transport blocks while preserving ordinary spoilers and formatting.
- [x] Add and observe failing command tests for explicit DC validation, one roll per durable request, session-owned source, historical modifiers, reset and later prompt availability; implement `/check` and register it through existing composition.
- [x] Run focused integration and delivery suites; update user documentation, changelog and plan progress; commit after passing.

### Task 4: Verify, review and integrate

**Files:** existing CI/static tooling; design, plan, changelog, user/configuration documentation as needed.

- [x] Run full pytest with resource warnings as errors and coverage, static analysis, dependency direction, module-size ratchet, reference audit, lint/format and security coverage gates.
- [x] Prepare the whole-branch diff and obtain a fresh independent code review against the spec and Review Focus.
- [ ] Reproduce important findings in failing tests, fix them and run the relevant tests plus a final full suite. Record any material decision and limitation.
- [ ] Push exact commits, verify GitHub CI on that head, mark PR #380 ready and merge using the existing user authorization.
- [ ] Verify the actual merge SHA/main checks and report completed phases, evidence and deployment status.

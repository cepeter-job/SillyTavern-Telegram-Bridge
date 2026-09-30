# Audit Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Fix all eleven confirmed audit defects and their associated hardening observations, then integrate through the authorized VPS/GitHub workflow.

**Architecture:** Preserve the existing service/port/repository owners. Isolate provider/ingestion, memory, and runtime changes in separate review worktrees; integrate and verify on the canonical VPS worktree. The runtime task owns all schema changes and worker-orchestration edits.

**Tech Stack:** Python 3.11, SQLite, pytest, urllib, Telegram, jsdom/Node 24, Git/GitHub.

**Spec:** `docs/superpowers/specs/2026-09-30-audit-repairs-design.md`

## Global Constraints

- Python 3.11 remains the supported target; use the committed hashed locks.
- Preserve immutable AppSettings, explicit ports, and actor/session context.
- Repository modules remain SQL-only and require caller-owned transactions.
- Network I/O must not run inside an application SQLite write transaction.
- Add forward migrations; do not change historical migration declarations.
- Preserve existing data, message row references, indexes and session isolation.
- Keep changes within the audited behaviors; no unrelated installer or UI redesign.
- Use synthetic records and intercepted external adapters for regressions.
- VPS worktree is canonical for integration, full verification, commits and pushes.
- Review mirrors must match its source tree; remote tool calls remain sequential.
- Do not deploy, restart a live service, publish a release or change private data.

## Review Focus

- A reset that occurs after the curator's local check but before remote retain must still win (Task 2).
- An interrupted schema upgrade preserves all prior row IDs and rolls back atomically (Task 3).
- A preview edit succeeds but its next chunk fails; acknowledged IDs must remain usable (Task 3).
- An unrelated actor's non-JSON document must leave the upload owner's pending state untouched (Task 1).
- Summary work that fails on a later segment must not claim coverage of that failed segment (Task 2).

## Task 1: Provider, document and pending-interaction safety

**Files:** Modify `bridge/image_generation.py`, `bridge/document_extraction.py`, `bridge/native_imports.py`, `bridge/world_callbacks.py`, `bridge/callbacks.py` and their existing tests. Create `tests/test_provider_ingestion_safety.py` for integrated regressions if existing owners cannot host them cleanly. Narrow changes to callback/pending-state owners are permitted; do not modify worker_orchestration or schema.

**Interfaces:** Keep `generate_image` and `extract_data_bank_text` public signatures. Pass the existing RequestContext into the World upload consumer, retaining its stored session identity and actor_id. Update all callers. Preserve unrelated character-import behavior introduced by PR #275.

- [ ] Write and run regressions: `test_explicit_missing_image_key_never_uses_default`, `test_empty_image_key_never_uses_default`, and valid explicit/default routes. Assert intercepted request contents or zero transport calls.
- [ ] Fail before network I/O for missing explicit credentials and bound image JSON reads to the base64 size for IMAGE_MAX_BYTES plus 65536 bytes. Add a reader double that records the requested bounded read and an oversized-response rejection test.
- [ ] Write and run `test_unterminated_markup_has_bounded_processing` in a subprocess, plus exact extraction fixtures for valid tags, entities, empty tags, and unterminated markup. Verify failure against the old parser before changing it.
- [ ] Replace regex tag stripping with linear scanning; run existing RAG/extraction tests and the new cases.
- [ ] Write and run `test_world_upload_requires_initiating_actor_and_session` with actual pending-state persistence and native importer. Include another actor's non-JSON file, expired/legacy state, wrong queued session, and competing documents; only the owner may consume/install.
- [ ] Bind actor/session when opening World upload, then atomically validate/consume. Add no ambient request context and do not clear the state on actor/session mismatch.
- [ ] Write and run `test_expired_memory_panel_does_not_lose_actor_protection` through real callback dispatch; make the relevant callback expire closed while preserving live owner behavior.
- [ ] Run focused tests, Ruff, formatting, static architecture, and the full suite in the review environment. Report the known root-only installer limitations by name if present.
- [ ] Commit the implementation and tests; write a report containing base/head commits, failing/passing commands, observed results, and any concerns.

## Task 2: Curator invalidation, NPC audience and summary coverage

**Files:** Modify `bridge/memory_curator.py`, `bridge/memory.py`, `bridge/npc_service.py`, their existing tests and narrowly necessary memory lifecycle owners. Create `tests/test_memory_completion_safety.py` if useful for cross-connection regressions. Do not modify schema or worker_orchestration; coordinate any needed edit_messages changes with Task 3.

**Interfaces:** Preserve `curate_memory_now`, `clear_curated_memory_state`, `generate_session_summary`, and NpcService public contracts. Use the existing `hindsight_session_lock` and durable metadata invalidation; no new global lock map. Public results should accurately indicate partial/failed summary completion without claiming omitted coverage.

- [ ] Write and run `test_curator_reset_discards_inflight_completion` with a blocking provider, actual reset, separate SQLite connections and intercepted backend retention. Add deletion/manual-change cases and the post-check/pre-retain interleaving.
- [ ] Capture a durable invalidation revision before I/O. Recheck revision/session/transcript identity under the existing Hindsight coordination lock and a short transaction. Store accepted state atomically and retain outside the SQLite transaction while preserving purge ordering.
- [ ] Write and run `test_npc_append_preserves_restricted_audience`: Alice-only then Bob-only items must reject incompatible mutation without changing value/audience/history. Add same-audience append, mismatched remove and explicit set coverage.
- [ ] Reject incompatible list-audience mutations in the NpcService validation path; retain deliberate whole-field replacement behavior.
- [ ] Write and run `test_summary_coverage_stops_at_processed_rows` with 40 roughly 2000-character messages and a marker beyond the initial 50000-character source budget. Cover forced/incremental, prior summary overhead, oversized one-row input and later-segment provider failure.
- [ ] Segment complete message rows within the existing input bound and advance coverage only through successfully processed content. Handle oversized input explicitly and leave source messages unchanged.
- [ ] Run focused memory/NPC tests, Ruff, formatting, static architecture, and the full suite in the review environment. Report the known root-only installer limitations by name if present.
- [ ] Commit the implementation and tests; write a report containing base/head commits, failing/passing commands, observed results, and any concerns.

## Task 3: Transcript identity, media policy and durable delivery

**Files:** Modify `bridge/edit_messages.py`, `bridge/response_variants.py`, `bridge/variant_repository.py`, `bridge/schema.py`, `bridge/response_delivery.py`, `bridge/telegram.py`, `bridge/worker_orchestration.py`, `bridge/scheduler_safety.py`, `bridge/update_message_routing.py`, `bridge/voice_jobs.py`, and existing tests as needed. Put cohesive new SQL/schema/delivery values in dedicated owners rather than enlarging adapters. Create `tests/test_transcript_delivery_recovery.py` for end-to-end failure regressions.

**Interfaces:** Own all new migrations and delivery-progress contracts. Message identity must continue to support existing rowid queries. Persist progress per assistant row and expose one canonical completion query used by workers. Extend send_text with an optional keyword-only acknowledged-chunk callback if needed, preserving all existing callers. Keep delivery-only recovery under the original operation ID and session.

- [ ] Write and run the actual edit -> new unrelated prompt -> variants -> Keep regression. Assert deleted variants never attach to new turns and IDs do not reuse after deleting the highest rows.
- [ ] Add a forward migration to give messages explicit INTEGER PRIMARY KEY AUTOINCREMENT identity while preserving rowid values, content, metadata and indexes. Test migration of populated databases, repeat initialization, rollback, and existing references. Prune discarded variants transactionally and prevent incompatible pre-edit prompt alternatives.
- [ ] Write and run owner/non-owner tests across text, photo, image document and voice, including an ownership change after enqueue. Assert denial precedes download/transcription/provider/state side effects.
- [ ] Apply the canonical GroupService policy at ingress and execution, preserving queued actor/session and delivery-only recovery.
- [ ] Write and run actual three-chunk delivery with failure on chunk two; assert the acknowledged ID persists, retry sends no duplicate, cleanup deletes acknowledged IDs and no second provider call occurs. Repeat for preview replacement and a later failure.
- [ ] Implement durable partial/complete progress through a forward migration and canonical owner. Preserve legacy completed-ID semantics, update all relevant recovery paths, and ensure persistence failures cannot falsely mark delivery complete.
- [ ] Write and run post-commit URLError/TimeoutError native edit tests with real job/operation state and recovery; assert the branch stays committed, delivery retries under the same operation, and completion is recorded only after delivery.
- [ ] Replace exception-text completion inference with explicit phase/completion state and typed delivery failure handling. Preserve accurate terminal partial-success feedback.
- [ ] Write and run transient-claim tests through DurableWorkerGuard for each worker family. First claim fails, next succeeds, business work executes once and no false provider failure is recorded.
- [ ] Move claim/start error handling outside business-failure classification so bounded requeue receives transient claim errors. Preserve close/finally and queue-notice cleanup correctly.
- [ ] Run focused transcript/jobs/media/migration tests, Ruff, formatting, static architecture, mypy and the full suite in the review environment. Report the known root-only installer limitations by name if present.
- [ ] Commit the implementation and tests; write a report containing base/head commits, migration details, failing/passing commands, observed results, and any concerns.

## Task 4: Integration, merge controls and completion

**Files:** Update `CONTRIBUTING.md`, the stale comment in `pyproject.toml`, and a concise audit-fix completion record. Preserve unrelated documentation and public example hashes. Read `.github/workflows/ci.yml` and live check names before changing required checks.

**Interfaces:** Consume reviewed task commits as binary patches into the canonical VPS worktree. Require matching source trees at integration boundaries. Preserve existing branch-protection settings while adding only actual reporting security contexts.

- [ ] Review each task for spec compliance and quality using a diff package; fix material findings and re-review the fix scope.
- [ ] Integrate reviewed code into the VPS worktree and reconcile overlapping changes without dropping tests. Record the mapping from local review commits to canonical VPS commits.
- [ ] Correct coverage documentation to 76% and list observed required CI/security checks. Do not create source-text tests for prose.
- [ ] Run the full suite with coverage as the unprivileged VPS user, security coverage floors, lock consistency, pip check, both dependency audits, Ruff, formatting, architecture, mypy, shell syntax and the committed Mini App DOM smoke suite.
- [ ] Independently review the combined diff and migration/data/recovery interactions; resolve material findings before merge.
- [ ] Push fix branch, create PR with exact verification results and audit coverage, and wait for all configured checks on its current head.
- [ ] Inspect current main/PR state, preserve concurrent changes, create and test the merge through the authorized VPS workflow, and push/synchronize without bypassing repository protection. If protection requires GitHub's merge endpoint, use that supported path and synchronize the VPS to the exact merge commit.
- [ ] Align required security checks using existing administrator authorization, preserving other settings; verify the resulting configuration and completed head checks.
- [ ] Report merged PR/commit, all finding dispositions, test results, and any deployment boundary. Do not restart or publish a release as part of source integration.

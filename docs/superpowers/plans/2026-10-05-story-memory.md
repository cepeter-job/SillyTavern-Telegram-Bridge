# Story memory implementation plan

> Execute the approved work sequentially with task-scoped implementation and independent review.

**Goal:** Complete all four approved stages and merge verified code.
**Architecture:** SQLite canonical transcript + durable memory work + indexed native memory + provenance-checked Hindsight.
**Tech stack:** Python 3.11, SQLite/FTS5, existing Hindsight client, pytest, ruff, mypy, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-05-story-memory-design.md
**Base:** 0049d9ce52486990e3a7ba7eeb8ea300cba12235

## Global constraints

The design's Global constraints apply to every task. Preserve session isolation, source and purge guards, bounded resource use, network-outside-transaction behavior, existing provider choices and production test protection. Do not edit the live checkout or deploy. User approval covers implementation, tests, PR creation and merge.

## Review focus

Review durable acknowledgments under concurrent mutations, source eligibility before prompt use, knowledge-time correctness, all final provider request budgets, migrations on existing data, shutdown recovery and observable test evidence. A source revision is not the same as an append counter. External memory output is not trusted authorization.

## Baseline

- Worktree: feat/story-memory-stages
- Baseline targeted memory tests: 122 passed, 65 subtests passed.
- Full suite is reserved for GitHub CI; device validation stays within the repository's production-host guard.

## Task 1: Stage 1 — durable coverage and segments

### Files and interfaces

Create focused memory schema/repository/segment/worker modules. Register the next forward migration in bridge/schema.py. Integrate with bridge/worker_orchestration.py and bridge/runtime_lifecycle.py, preserving the existing durable job dispatcher. Update bridge/memory_backend.py, bridge/hindsight_integrity.py, bridge/memory.py and bridge/episodic_extraction.py; adjust scene, NPC and curator queue entry points as required.

Repository interfaces should express request, claim, successful acknowledgment, retry/defer, recovery and independent layer progress. Segment interfaces should expose deterministic source identity, complete bounded payloads and source validation. Workers reconstruct inputs after claim instead of storing stale conversation closures.

### Steps

1. Add regression tests for rollback, executor rejection, failed retain, restart/lease recovery, empty versus stale extraction and independent coverage; show them failing.
2. Add forward migration for durable coalesced jobs, layer progress, segment provenance and atomic transcript mutation invalidation. Handle old/new owners and session deletion/recreation.
3. Implement bounded workers with leases, idempotent segment retain, retry/defer and current-mode checks. Integrate startup and periodic dispatch.
4. Route normal and alternate-session source retention through full accepted segments. Preserve purge and edit guards and avoid progress loss when source changes during a call.
5. Decouple episode coverage from summary advancement and remove success ambiguity.
6. Run focused new tests and affected existing memory/lifecycle tests, format and lint changed files, inspect the diff and commit.
7. Produce a report with RED/GREEN commands, log paths, commit and integration interfaces for task review.

## Task 2: Stage 2 — shared eligibility and knowledge timing

### Files and interfaces

Create a memory eligibility/scope module and focused provenance storage as required. Integrate bridge/memory_service.py, bridge/memory_backend.py, bridge/episodic_memory.py, bridge/scene_state.py, summary context, bridge/edit_messages.py and regeneration paths. Add a forward migration for temporal visibility/source metadata if not covered by task 1.

### Steps

1. Add failing tests for future scene/summary context, discarded assistant outcomes, foreign branches, deleted/recreated sessions, mixed-secret raw sources and later knowledge grants queried historically.
2. Implement the shared branch/incarnation/as-of/source/reader contract. Preserve valid old evidence on ordinary append.
3. Require mapped, locally eligible provenance for Hindsight results. Exclude unverifiable observations or verify all constituent source facts.
4. Version visibility or persist per-viewer grant boundaries. Bind authorized evidence to source spans; do not expose raw mixed-knowledge transcript as character-visible memory.
5. Pass a consistent generation boundary through edit/regeneration and all memory channels.
6. Run focused and existing isolation/visibility tests, format/lint and commit; obtain independent review and fix blocking findings.

## Task 3: Stage 3 — indexed evidence and final context budget

### Files and interfaces

Update bridge/episodic_memory.py with a derived FTS5 index migration and bounded full-corpus search; add a retrieval/evidence helper when that keeps responsibilities clear. Update scene/curator extraction windowing. Update bridge/context_compaction.py, bridge/generation.py and actual generation call sites/light-novel assembly to budget the completed request.

### Steps

1. Add failing tests for a relevant old fact beyond 200 episodes, continuation query context, authorized source evidence, long-message tails, appended contract overflow and output-reserve mismatch.
2. Implement full-corpus indexed retrieval with deterministic ordering, temporal/visibility filters and bounded evidence.
3. Add complete bounded input window processing and explicit oversized-row parts, keeping independent coverage.
4. Move or repeat final compaction after all request instructions; reserve requested max_tokens and protect independent scene context using consistent model token heuristics.
5. Verify normal, edit, regenerate, continuation and image paths affected by the budget contract. Persist accurate final diagnostics.
6. Run affected retrieval/extraction/context tests, static checks and commit; obtain independent review.

## Task 4: Stage 4 — reproducible story evaluation

### Files and interfaces

Add tools/evaluate_story_memory.py, synthetic fixtures and focused tests, plus docs describing architecture, migration, recovery and evaluation. Integrate the deterministic evaluation into CI only where it adds a gate beyond existing pytest coverage.

### Steps

1. Define fixed synthetic stories and objective expected answers/provenance for old facts, branches, secrets, edits, long tails and failures.
2. Implement a repeatable offline evaluation that uses production retrieval/eligibility/prompt code rather than a parallel toy implementation.
3. Record measured pipeline outcomes and latency/resource methodology. Offer real-provider evaluation separately with explicit cost/config controls; do not conflate deterministic pipeline pass rates with answer quality.
4. Run the evaluation and targeted integration tests on the final implementation, save machine-readable and human-readable results where appropriate, and document operational limitations.
5. Format/lint/static-check the full changed surface, commit and obtain task review.

## Task 5: final verification, PR and protected merge

1. Read final diff and obtain independent whole-branch review; fix important findings.
2. Run the meaningful aggregate targeted suite within device limits plus required static checks. Ensure no background jobs or test fixtures affect live data.
3. Push feature branch and create a reviewable PR explaining the problem, stage changes, validation and limits.
4. Inspect every required CI check on the final head. Resolve failures rather than bypass protection.
5. Merge through normal GitHub controls after all required checks pass. Verify main contains the merge and the working tree is clean.
6. Report completed stages, PR/merge links, measured verification, and any material operational limits. State deployment status accurately.

# Memory Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Each checkpoint receives an independent review before its GitHub merge.

**Goal:** Simplify Hindsight integration while preserving canonical story authority and responsive local session operations.
**Architecture:** SQLite owns accepted facts and durable cleanup obligations. Hindsight ranks locally authorized fact identities. External work is fenced at dispatch and completion; each checkpoint is independently mergeable.
**Tech Stack:** Python 3.11+, SQLite, hindsight-client 0.10.2, pytest, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-07-memory-integration-design.md

## Global Constraints
- Keep SQLite as the authority for accepted story, source revisions, branch and historical boundaries, and character audiences.
- Keep Hindsight as an optional semantic ranker for locally verified native facts.
- Never delete new-generation documents during delayed cleanup.
- Pending deletion survives deleted sessions, crashes, restarts, offline Hindsight, and memory-off mode.
- Never expire unresolved erasure obligations on a timer or drop authority tombstones merely for size.
- Preserve local complete source segmentation, source provenance, episodic extraction, summaries, scene, NPC, and curator data.
- Preserve Python 3.11 support, numeric-loopback destination restriction, dependency locks, and all existing CI gates.
- Each checkpoint ends in a reviewed PR and merge; do not publish a release or deploy as part of a checkpoint merge.

## Review Focus
- In-flight remote requests must not block the local reset lock or republish after reset.
- Deleted and recreated session keys must not share cleanup authority.
- Memory-off mode must allow an already requested deletion to finish.
- No synthetic benchmark result is reported as real semantic quality or production RAM.
- Resource cleanup must finish on the same loop that created the transport, including error paths.

### Task 1: Own the retirement request and close loop
**Files:** bridge/memory_backend.py; tests/test_hindsight_loop_lifecycle.py.
**Interfaces:** Preserve hindsight_client_scope and cleanup_retired_memory_documents signatures.
- [x] Add parameterized success/failure regression using the installed SDK wrapper with only external document I/O replaced. Observe request/close loops and prove every observed loop closes.
- [x] Run the test against the old implementation and record the expected loop mismatch/unclosed-loop failure.
- [x] Run retired deletion on the loop owned by hindsight_client_scope; avoid nested asyncio.run.
- [x] Run focused lifecycle/retirement/session-cleanup tests, lint and format. Full required CI verifies the submitted commit.
- [x] Commit code, test and approved design/plan. Create PR, obtain read-only review, resolve material findings, wait for required checks, merge.

### Task 2: Commit reset locally and retain cleanup obligations
**Files:** bridge/memory_backend.py; bridge/memory_store.py; bridge/memory_workers.py; bridge/memory_archival_schema.py; bridge/schema.py and its migration registry; bridge/memory.py; bridge/hindsight_integrity.py; bridge/message_commands.py; bridge/session_core.py; relevant reset/session UI and tests.
**Interfaces:** Add an explicit transactional queue_cleanup service port for reset/delete; retain purge_session as the synchronous compatibility contract. Reuse exact document identities, incarnation, epoch, source validation and retirement storage.
- [ ] Pin reset/delete success with an unavailable Hindsight transport, durable work after reopen, and old-scope rejection.
- [ ] Pin reset while a remote retain/delete is in flight; a new-generation fact must survive late cleanup and late acknowledgments.
- [ ] Pin cleanup admission with memory off and preservation of unresolved raw/native dispatch obligations.
- [ ] Add the minimal forward migration and local cleanup enqueue transaction. Release session locks across remote I/O and fence completion.
- [ ] Replace misleading failure copy with local completion and pending external-cleanup semantics. Keep explicit remote purge status accurate.
- [ ] Run targeted lifecycle, scope, retirement, migration and route tests; update runtime docs. Review and merge a separate PR after required CI.

### Task 3: Index facts only and retire raw archival work
**Files:** bridge/memory_workers.py; bridge/memory_backend.py; bridge/memory_store.py; bridge/alternate_ending_memory.py; memory lifecycle/diagnostics and migrations; associated tests and docs.
**Interfaces:** Preserve local source/provenance APIs and native fact rehydration. Hindsight worker consumes pending native facts; alternate-ending readiness consumes local memory work.
- [ ] Pin that accepted turns produce native-fact retains and no new raw source_segment retains.
- [ ] Pin complete local extraction of long messages and alternate-ending readiness with Hindsight unavailable.
- [ ] Pin continued cleanup of pre-existing raw documents and unresolved attempts through restart.
- [ ] Remove raw dispatch and obsolete branch-seed dependencies, retire old pending archival state, and compact only terminal unreferenced records.
- [ ] Adjust existing contract fixtures to the new behavior; do not keep tests asserting removed raw ingestion.
- [ ] Run affected worker/lifecycle/branch/evaluation tests, document migration behavior, review and merge after required CI.

### Task 4: Bound foreground recall and stabilize client setup
**Files:** bridge/memory_backend.py or a focused client adapter; bridge/memory_service.py; application lifecycle/configuration seams; dedicated recall/lifecycle tests and docs.
**Interfaces:** Keep scoped recall results as locally rehydrated native-fact IDs. No remote response text grants authority.
- [ ] Pin a finite total recall deadline, repeated-outage bypass, recovery, and unchanged local memory output when semantic recall fails.
- [ ] Pin scope invalidation while recall is in progress and prevent stale responses from entering the prompt.
- [ ] Configure foreground recall separately from background ingestion; establish a bounded failure cache and predictable loop-owned client lifecycle.
- [ ] Move proxy-bypass preparation out of repeated request work while preserving destination validation and supported SDK behavior.
- [ ] Run focused tests plus required CI; review and merge a separate PR.

### Task 5: Compare retrieval approaches reproducibly
**Files:** tools/evaluate_memory_retrieval.py; a focused versioned synthetic roleplay fixture and tool tests; docs/memory-retrieval-comparison.md and measured result artifact.
**Interfaces:** Explicit backend selection for FTS, Hindsight and ordinary BLOB exact-vector scoring. Reuse canonical eligibility before ranking; never reintroduce a latest-200 cap.
- [ ] Pin deterministic candidate authorization, non-lexical semantic matches, provider failure behavior, and result schema in small offline tests.
- [ ] Implement a bounded comparison harness with corpus/embedding-profile identity, per-stage latency, retrieval judgments, and precise measurement boundary labels.
- [ ] Use only isolated synthetic data for optional genuine-provider runs. Protect credentials and existing story banks; do not invent results if a usable embedding endpoint is unavailable.
- [ ] Run available comparisons, record quality/latency and limitations, and retain facts-only Hindsight unless evidence justifies a later migration.
- [ ] Review and merge the tooling, tests, results and operating documentation after required CI.

## Progress
- Approved design captured from the 2026-10-07 audit and explicit implementation approval.
- Base: caedf3f5a1be5314ed22f18d536c9339c5708e79, cepeter/SillyTavern-Telegram-Bridge.
- Task1 complete: PR398 merged as bfb71bfbd1b175274db4154a426565244143857d after independent review and all required checks.
- Task2 complete: PR401 merged as e379214 after independent review and all required checks.
- Task3 complete: PR404 merged as 2a049fd2aee4d37c712e0c8142a228f9eedbb270 after independent specification/code review and all required checks.
- Task4 in progress: bounded foreground recall implementation; independent review, required CI, and merge remain pending.

### Checkpoint 1 verification

- Regression reproduced with the installed hindsight-client 0.10.2: both success and failure used different loops for deletion and transport closure.
- The full local baseline run was deliberately interrupted because of low VPS throughput; no full-suite pass is claimed. Focused local regressions and the full required GitHub CI on each submitted commit are the merge gates.

- Focused retirement/session lifecycle tests passed: 14 tests, with ResourceWarning treated as an error. Independent review requested actual lazy transport coverage; the strengthened SDK regression creates a real aiohttp session/connector without HTTP, and both success/failure tests pass with session, connector and loop closure asserted.
- PR: https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/398 (merged; all required checks passed).

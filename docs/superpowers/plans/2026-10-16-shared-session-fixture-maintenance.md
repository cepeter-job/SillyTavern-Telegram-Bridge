# Shared Session Fixture Maintenance Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for this scoped test-only refactor.

**Goal:** Remove one proven duplicate persistence test and give one reused fixture a test-support owner.

**Architecture:** Move `session_db` unchanged into `tests/session_test_support.py`. Its original module and 47 direct consumers import the fixture explicitly; no global registration, wrapper or shared database is introduced.

**Tech Stack:** Existing Python 3.11, pytest, SQLite and repository CI locks.

**Spec:** User-approved first maintenance PR from the 2026-10-16 workflow/test audit of `e5877e3b60336fd8bcb42ed5f70154f71928336e`.

## Global constraints

- Change test/support code and contributor guidance only; preserve production, workflows, locks and coverage/security gates.
- Preserve function-scoped `tmp_path`, persona seed, session identity, yielded values and database teardown.
- Keep existing autouse transport guards in their original modules; do not widen their scope.
- Keep all distinct negative cases and all surviving assertions. Do not add historical-filename blacklists.
- Run product/tests only on GitHub-hosted runners. This PR does not authorize merge, release or deployment.

## Review focus

Fixture imports must not register unrelated autouse behavior. No consumer may rely on the old fixture's module globals. Every collected test ID must remain except the mapped duplicate. Parallel execution must retain database isolation and resource cleanup. Existing workflow and security coverage checks remain mandatory.

## Task 1: Move the fixture and map the duplicate

- [ ] Verify the green baseline and review fixture aliases, dependencies and patch targets.
- [ ] Copy `session_db(tmp_path)` with its decorator and body unchanged to `tests/session_test_support.py`, importing only its actual dependencies.
- [ ] Redirect the 47 explicit imports and import the fixture in `tests/test_memory_completion_safety.py`; remove its now-unused settings-builder import.
- [ ] Remove `HumanizerPersistenceTests.test_update_session_accepts_humanizer_column`; retain `test_set_humanizer_persists_on`, whose setup, input and assertions are identical.
- [ ] Document shared-fixture ownership in `docs/development/testing.md`.

This is the refactor stage of an already-green contract, not new behavior. Do not manufacture new product failures or permanent migration-only tests.

## Task 2: Verify and publish the PR

- [ ] Compare moved and surviving functions as ASTs, including fixture decorators and teardown.
- [ ] Run pinned static lint/format checks and inspect the complete diff for scope and private data.
- [ ] Publish a draft PR and run the unchanged GitHub-hosted CI.
- [ ] Compare retained shard manifests with the baseline: exactly one mapped node ID removed, no additions, skips or duplicates.
- [ ] Verify whole-application and security coverage, both browser engines and all required checks; obtain independent final diff review.
- [ ] Record exact head/base/run/artifact evidence and mark ready for review without merging.

# Inactive Memory Backlog Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop stale inactive story sessions from autonomously consuming provider calls while preserving bounded recovery for recent work and automatic catch-up when the story is used again.

**Architecture:** Keep durable `memory_jobs` rows as canonical pending work, but make claim eligibility depend on recent canonical session activity and a bounded retry budget. Do not delete or falsely acknowledge parked work; a new message reactivates the session naturally by making its latest source recent and re-enqueueing the job.

**Tech Stack:** Python 3.11, sqlite3, pytest.

**Spec:** Production incident diagnosed 2026-10-07: an unused Carna session retained pending `scene`, `summary`, and `npc` jobs with 260+ `work_failed` attempts and was repeatedly dispatched.

## Global Constraints

- Durable work must remain recoverable; parked jobs are not marked complete.
- Recent interrupted work must still recover after restart.
- Old inactive sessions must not be autonomously claimed.
- Repeated non-configuration failures must stop consuming provider calls after a bounded attempt count.
- No schema migration unless required by the minimal fix.

## Review Focus

- A recent session with pending work remains claimable.
- A stale session with pending work is left pending and unleased.
- A stale session becomes claimable after a new canonical message is written.
- A repeatedly failing job reaches a terminal parked state without being acknowledged complete.
- Startup recovery clears expired leases but does not bypass activity/retry eligibility.

---

### Task 1: Claim eligibility guard

**Files:**
- Modify: `bridge/memory_store.py`
- Test: `tests/test_durable_memory.py`

**Interfaces:**
- Consumes: existing `claim_jobs(..., now=...)`.
- Produces: claim filtering by recent canonical message activity and retry budget.

- [ ] Write failing tests for stale-session parking, recent-session recovery, and reactivation by a new message.
- [ ] Run focused tests and verify RED.
- [ ] Add minimal claim eligibility predicates/constants.
- [ ] Run focused tests and verify GREEN.

### Task 2: Bounded failed-job retries

**Files:**
- Modify: `bridge/memory_store.py`
- Test: `tests/test_durable_memory.py`

**Interfaces:**
- Consumes: existing `fail_job` / `claim_jobs` state.
- Produces: jobs at/over the retry ceiling remain pending but are not autonomously claimed.

- [ ] Write a failing test proving a repeatedly failed job is parked.
- [ ] Run focused test and verify RED.
- [ ] Implement the smallest retry-ceiling predicate.
- [ ] Run focused tests and verify GREEN.
- [ ] Run the full test suite before PR creation.

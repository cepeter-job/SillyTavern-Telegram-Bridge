# Provider Resilience Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for native, sequential execution.

**Goal:** Ship all four approved provider improvements in separately reviewed PRs.
**Architecture:** Health values/monitor and execution policy sit above transport;
explicit callback injection renders diagnostics. Discovery remains independent.
**Tech Stack:** Python 3.11+, standard library, existing pytest/ruff/mypy and GitHub CI.
**Spec:** `docs/superpowers/specs/2026-09-30-provider-resilience-design.md`

## Global Constraints
No new dependencies, no live restart, no new Telegram command, no implicit Story
fallback, no private configuration committed, no remote paid inference tests.
Use only exact configured routes; retain provider-host security and actor binding.

## Review Focus
- Out-of-order request completion cannot erase a newer blocking failure (wave 1/2).
- Partial streams and cancellation never start another model (wave 2).
- Invalid fallback routes/credentials never redirect or leak a primary key (wave 2).
- Malformed one-provider config/cache cannot hide working providers (wave 3).
- Concurrent diagnostics, corrupt files and restarts cannot wedge requests (wave 4).

## Task 1: observation foundation
Files: `provider_health_values.py`, `provider_runtime_health.py`,
`provider_execution_policy.py`, `provider_port.py`, `port_contracts.py`, `main.py`,
`tools/static_analysis.py`, `tests/test_provider_runtime_health.py`.
Interfaces: HealthSnapshot; HealthAttempt; monitor begin/succeed/fail/cancel/
snapshot; policy candidates/begin/succeed/fail/cancel. Port accepts optional policy.
- [x] Add tests for unknown/success/error scope, cancellation, app isolation, stale success.
- [x] Run focused tests and observe missing-feature failures.
- [x] Implement monitor and optional port integration with canonical model identities.
- [x] Run focused + full tests, static analysis, formatting/lint and mypy.
- [x] Commit, push `feat/provider-health-foundation`, create and merge green PR.

## Task 2: circuit policy and bounded fallback
Files: health/policy/port/errors above; `codex_transport.py`; new resilience tests.
Consumes Task 1 monitor/attempt interfaces. Produces guarded admission, Retry-After
metadata and explicit fallback candidates while preserving ProviderGenerate.
- [x] Test three failures/60s, single half-open request, backoff, cancel release,
  rate-limit/date parsing, auth/model scope, fallback usage/credentials/deadlines.
- [x] Observe failures; implement guards and one bounded ordered candidate loop.
- [x] Test streaming/no-output and request-local failure exclusions.
- [x] Run full verification; push `feat/provider-health-resilience`; merge green PR.

## Task 3: diagnostics and catalog maintenance
Files: `provider_discovery.py`, `provider_panels.py`, `provider_callbacks.py`,
`callback_dispatch.py`, health values/service, docs/help and panel tests.
Consumes monitor snapshots via optional policy injection. Produces per-provider
maintenance and compact paginated diagnostics without ambient runtime context.
- [x] Test isolated invalid endpoints, targeted refresh, malformed caches, stale
  metadata, merged catalog IDs, more than 50 models and no double refresh.
- [x] Test bound actions, early acknowledgments, read-only status paging.
- [x] Implement and document exact controls; retain compatibility of existing APIs.
- [x] Run full verification; push `feat/provider-health-panels`; merge green PR.

## Task 4: persistence, history and bounded manual probes
Files: new `provider_health_store.py`, monitor/diagnostics, startup, tests/docs.
Consumes sanitized snapshots and history. Produces private atomic JSON snapshots
and deterministic manual sweeps with at most three workers.
- [x] Test clean restore, TTL expiry, bounds, corruption, permissions, write failure
  isolation, concurrent writes, one sweep at a time and stable result order.
- [x] Observe failures, implement store adapter and bounded probes.
- [ ] Verify all four waves end-to-end; push `feat/provider-health-diagnostics`;
  merge green PR and synchronize the main VPS checkout without touching live.
- [ ] Update changelog/version, verify signed tag and ZIP checksums, publish release.

Implementation verification: 2308 tests + 778 subtests passed; coverage 78.88%. Publication completion is recorded
in the corresponding merged PR and signed GitHub release.

# Mini App Memory Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose PR #292 memory diagnostics in the Telegram Mini App with a lightweight Home summary and a read-only detailed System view.

**Architecture:** Extend the existing `MemoryDiagnostics` service with thread-safe read-only projections, promote one instance into `BridgeServices`, expose a bounded summary on `/status` plus detailed sanitized `GET /memory-diagnostics`, then render Home/System views without adding remote controls.

**Tech Stack:** Python 3.11+, stdlib JSON/path/threading, aiohttp Mini App API, vanilla ES modules, existing Mini App UI helpers, pytest, Node/jsdom smoke tests, Ruff, Mypy.

**Spec:** `docs/superpowers/specs/2026-10-01-miniapp-memory-monitoring-design.md`

## Global Constraints

- This branch is stacked on PR #292 / `feature/memory-diagnostics` until #292 merges.
- PR #292 core diagnostics behavior must not be duplicated or forked.
- Home uses `GET /status` only; it must never fetch `/memory-diagnostics`.
- Detailed monitoring is read-only; no enable/disable, threshold, capture, delete, or tracemalloc controls.
- `GET /memory-diagnostics` returns at most three reports and ten top sites per report.
- No absolute host path, prompt, response, credential, DB content, object value, arbitrary exception text, or raw report filename reaches the Mini App.
- Disabled monitoring may still expose retained sanitized historical reports.
- No schema/database migration and no production service environment change.

## Review Focus

1. A malformed retained report must not make all other retained reports disappear; Task 1 adds per-report isolation coverage.
2. A caller invoking `summary()` concurrently with sampling/capture must receive a coherent bounded snapshot without disk scans; Task 1 adds thread-safety/no-I/O coverage.
3. External allocation paths with Unix or Windows-looking separators must not leak parent directories; Task 1 adds sanitization coverage.
4. `GET /memory-diagnostics` must remain process-scoped and DB-free even under normal Mini App authentication; Task 3 adds a DB-factory trap test.
5. Optional detail-fetch failure must leave System usable and Home must never request the detail endpoint; Task 4 adds browser smoke coverage.

---

### Task 1: Read-only diagnostics projections

**Files:**
- Modify: `bridge/memory_diagnostics.py`
- Modify: `tests/test_memory_diagnostics.py`

**Interfaces:**
- Produces: `MemoryDiagnostics.summary() -> dict[str, object]`.
- Produces: `MemoryDiagnostics.recent_reports() -> tuple[dict[str, object], ...]`.
- Summary shape: `enabled`, `state`, `rss_kib`, `thresholds_kib`, `report_count`, `latest_incident`.
- Detailed report allowlist: `timestamp_utc`, `rss_kib`, `smaps_kib`, `traced_current_bytes`, `traced_peak_bytes`, `thread_count`, `safe_counters`, `top_sites`.

- [ ] **Step 1: Write failing summary tests**

Add tests for the exact disabled-like shape, enabled state, last sampled RSS, threshold values, report count, and latest incident timestamp/RSS. Patch file-reading helpers so `summary()` fails if it scans disk or samples `/proc`.

- [ ] **Step 2: Run summary tests and verify RED**

Run: `python -m pytest -q tests/test_memory_diagnostics.py -k 'summary'`

Expected: FAIL because `summary()` does not exist.

- [ ] **Step 3: Implement bounded in-memory summary state**

Track last successful RSS and initialize a bounded newest-three report index once during construction. Update the index after successful capture. Guard mutable summary/index state with the existing lock.

- [ ] **Step 4: Write failing sanitized-report tests**

Cover newest-first maximum-three output, unknown-key dropping, maximum ten `top_sites`, retained history while diagnostics are disabled, one malformed report not hiding valid siblings, and no raw report filename/path in output.

- [ ] **Step 5: Write failing path-sanitization tests**

For repository-local paths assert repository-relative output such as `bridge/generation.py`; for Unix external paths and Windows-looking paths assert only a basename/stable external label is exposed and parent directories are absent.

- [ ] **Step 6: Run report projection tests and verify RED**

Run: `python -m pytest -q tests/test_memory_diagnostics.py -k 'recent_reports or sanitize or retained or malformed'`

Expected: FAIL because report projection/sanitization is not implemented.

- [ ] **Step 7: Implement `recent_reports()` and sanitization**

Read only indexed retained files, isolate parse/read failure per report, apply a second explicit allowlist, cap sites to ten, and normalize site filenames without exposing absolute roots.

- [ ] **Step 8: Add concurrent snapshot consistency test**

Hold/advance diagnostics state around `summary()` from another thread and assert no partial shape, exception, or disk rescan occurs.

- [ ] **Step 9: Run Task 1 tests**

Run: `python -m pytest -q tests/test_memory_diagnostics.py`

Expected: PASS.

- [ ] **Step 10: Commit**

`git add bridge/memory_diagnostics.py tests/test_memory_diagnostics.py && git commit -m "feat: expose bounded memory diagnostic projections"`

---

### Task 2: Single-instance composition and lifecycle

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `bridge/runtime_lifecycle.py`
- Modify: `tests/test_composition.py`
- Modify: `tests/test_runtime_entrypoint_invariants.py`

**Interfaces:**
- Consumes: Task 1 `MemoryDiagnostics` API.
- Produces: `BridgeServices.memory_diagnostics: MemoryDiagnostics | None = None`.
- Startup composition constructs exactly one instance from `config.bridge_home` and `config.environ`.
- Runtime lifecycle starts/stops `services.memory_diagnostics`; it never constructs a second instance.

- [ ] **Step 1: Write failing composition test**

Assert `_build_startup_services()` places one `MemoryDiagnostics` object on `BridgeServices` and preserves it by identity.

- [ ] **Step 2: Run composition test and verify RED**

Run: `python -m pytest -q tests/test_composition.py -k 'memory_diagnostics'`

Expected: FAIL because `BridgeServices` has no composed diagnostics field.

- [ ] **Step 3: Add the optional composition field and construct one instance**

Keep the new field optional/defaulted so test/support service constructors remain source-compatible.

- [ ] **Step 4: Write failing lifecycle identity test**

Inject a recorder diagnostics object through `services.memory_diagnostics`; assert runtime calls that same object's `start()` before polling and `stop(timeout=1.0)` during shutdown, with no constructor call inside `run_bridge_runtime()`.

- [ ] **Step 5: Run lifecycle test and verify RED**

Run: `python -m pytest -q tests/test_runtime_entrypoint_invariants.py -k 'memory_diagnostics'`

Expected: FAIL while lifecycle still constructs its own object.

- [ ] **Step 6: Rewire runtime lifecycle to the composed instance**

Preserve best-effort startup/shutdown behavior and generic logging; do not alter polling semantics.

- [ ] **Step 7: Run Task 2 tests**

Run: `python -m pytest -q tests/test_composition.py tests/test_runtime_entrypoint_invariants.py tests/test_memory_diagnostics.py`

Expected: PASS.

- [ ] **Step 8: Commit**

`git add bridge/composition.py bridge/main.py bridge/runtime_lifecycle.py tests/test_composition.py tests/test_runtime_entrypoint_invariants.py && git commit -m "refactor: compose one memory diagnostics service"`

---

### Task 3: Mini App read-only monitoring API

**Files:**
- Modify: `bridge/miniapp_system.py`
- Modify: `tests/test_miniapp_system.py`
- Modify: `tests/test_miniapp_http.py`

**Interfaces:**
- Consumes: `services.memory_diagnostics.summary()` and `recent_reports()`.
- Produces: `GET /status -> memory_diagnostics: <summary>`.
- Produces: `GET /memory-diagnostics -> {"summary": <summary>, "reports": [...]}`.
- Fallback summary is exactly `enabled=false`, `state="disabled"`, `rss_kib=null`, thresholds 262144/327680/393216 KiB, `report_count=0`, `latest_incident=null`.

- [ ] **Step 1: Write failing `/status` summary tests**

Cover composed diagnostics, missing diagnostics, and a `summary()` exception. Assert unrelated status/database/session fields remain available and no diagnostic exception text leaks.

- [ ] **Step 2: Run status tests and verify RED**

Run: `python -m pytest -q tests/test_miniapp_system.py -k 'memory_diagnostics and status'`

Expected: FAIL because `/status` has no memory summary.

- [ ] **Step 3: Implement safe summary helper and attach it to `system_status()`**

Use one stable fallback function. Do not let summary failure fail the overall status request.

- [ ] **Step 4: Write failing detail endpoint tests**

Assert newest-first maximum-three sanitized reports, disabled-with-retained-history behavior, summary preservation when report loading fails, and exact GET route presence.

- [ ] **Step 5: Add the DB-free endpoint test**

Construct services with a DB factory/session helper that raises if touched, invoke the detail handler, and assert it succeeds using only the process-scoped diagnostics object.

- [ ] **Step 6: Add read-only route contract test**

Assert no POST/PATCH/DELETE `ApiRoute` exists for `/memory-diagnostics`.

- [ ] **Step 7: Implement `memory_diagnostics_status(services, who, values) -> dict`**

Return only summary plus sanitized reports; do not enter `session_scope` or touch the database.

- [ ] **Step 8: Run Task 3 tests**

Run: `python -m pytest -q tests/test_miniapp_system.py tests/test_miniapp_http.py`

Expected: PASS.

- [ ] **Step 9: Commit**

`git add bridge/miniapp_system.py tests/test_miniapp_system.py tests/test_miniapp_http.py && git commit -m "feat: expose memory diagnostics to miniapp"`

---

### Task 4: Home and System monitoring UI

**Files:**
- Modify: `bridge/miniapp_assets/system.js`
- Modify: `bridge/miniapp_assets/style.css`
- Modify: `tools/miniapp_ui_smoke.mjs`
- Modify: `tests/miniapp_browser_fixture.py`

**Interfaces:**
- Consumes: `/status.memory_diagnostics` summary and `GET /memory-diagnostics`.
- Home renders only summary.
- System renders summary plus optional detailed reports.
- Interpretation hint thresholds: traced current bytes >= 60% RSS bytes => Python significant; <= 35% => native/untraced significant; otherwise mixed.

- [ ] **Step 1: Write failing Home smoke assertions**

Assert the Bridge health grid has four icons/cards, Memory is fourth, state/RSS text renders for disabled/armed/warned/tracing/captured fixture states, and dashboard navigation never requests `/api/v1/memory-diagnostics`.

- [ ] **Step 2: Run Mini App smoke and verify RED**

Run: `MINIAPP_JSDOM_ROOT=$PWD/tests/miniapp-ui PYTHON=python node --experimental-vm-modules tools/miniapp_ui_smoke.mjs`.

Expected: FAIL because Memory health is absent.

- [ ] **Step 3: Implement compact Home Memory card**

Add formatting helpers for MiB/state/next threshold without changing existing Bridge/Telegram/Database cards.

- [ ] **Step 4: Write failing System detail smoke assertions**

Cover zero reports, retained reports while disabled, selected smaps/traced/thread fields, a collapsed Top Python allocations section capped at ten sites, and all three interpretation hint branches.

- [ ] **Step 5: Add detail-failure and path-privacy smoke assertions**

Force `/api/v1/memory-diagnostics` to fail and assert System remains usable. Assert rendered content never contains fixture absolute parent paths.

- [ ] **Step 6: Implement System Memory diagnostics card**

Fetch details only from System, catch optional detail failure locally, render empty/unavailable states without replacing the page, and show textual diagnostic hint only.

- [ ] **Step 7: Run Task 4 browser/API tests**

Run: `MINIAPP_JSDOM_ROOT=$PWD/tests/miniapp-ui PYTHON=python node --experimental-vm-modules tools/miniapp_ui_smoke.mjs && python -m pytest -q tests/test_miniapp_system.py`.

Expected: PASS.

- [ ] **Step 8: Commit**

`git add bridge/miniapp_assets/system.js bridge/miniapp_assets/style.css tools/miniapp_ui_smoke.mjs tests/miniapp_browser_fixture.py && git commit -m "feat: show memory diagnostics in miniapp"`

---

### Task 5: Documentation, stacked-branch reconciliation, and whole-repo verification

**Files:**
- Modify: `docs/miniapp.md`
- Do not add an Unreleased CHANGELOG entry while the current release-boundary invariant rejects it.

**Interfaces:**
- Documents Home summary versus System detail behavior.
- Documents retained history after monitoring is disabled.
- Documents read-only privacy boundary and lack of remote controls.
- Before PR creation, checks whether #292 has merged and rebases/retargets the implementation branch accordingly.

- [ ] **Step 1: Update Mini App documentation**

Document the fourth Home health card, System incident detail, sanitized paths, historical reports after disable, and that service/environment configuration remains operator-only.

- [ ] **Step 2: Run focused backend and browser tests**

Run: `python -m pytest -q tests/test_memory_diagnostics.py tests/test_composition.py tests/test_runtime_entrypoint_invariants.py tests/test_miniapp_system.py tests/test_miniapp_http.py && MINIAPP_JSDOM_ROOT=$PWD/tests/miniapp-ui PYTHON=python node --experimental-vm-modules tools/miniapp_ui_smoke.mjs`.

Expected: all pass.

- [ ] **Step 3: Run full Python suite**

Run: `python -m pytest -q -n auto --maxprocesses=4 --dist=loadfile --cov --cov-report=term-missing:skip-covered --cov-report=json:coverage.json --cov-report=xml:coverage.xml`

Expected: zero failures and `coverage.json` created.

- [ ] **Step 4: Run CI-equivalent static checks**

Run: `python tools/check_dependency_lock.py && python tools/static_analysis.py && python -m ruff check . && python -m ruff format --check . && count=$(python tools/static_analysis.py --print-type-target-count) && minimum=$(python -c 'import json; print(json.load(open("tools/type_surface_baseline.json"))["minimum_typed_files"])') && test "$count" -ge "$minimum" && python tools/static_analysis.py --print-type-targets | xargs python -m mypy`.

Expected: all exit 0.

- [ ] **Step 5: Run integrity/security checks**

Run: `git diff --check && python -m compileall -q bridge tests tools && python tools/check_security_coverage.py coverage.json`. Then search tracked `systemd/`, `config/`, installer templates, Mini App routes, and UI actions to verify there is no unconditional diagnostics enablement and no diagnostics write/control route.

- [ ] **Step 6: Reconcile stack with PR #292**

If #292 is merged, rebase/retarget the implementation branch onto current `main` and rerun focused tests. If #292 remains open, create the follow-up PR against `feature/memory-diagnostics` and clearly mark it stacked/dependent.

- [ ] **Step 7: Commit documentation**

`git add docs/miniapp.md && git commit -m "docs: document miniapp memory monitoring"`

## Final Review Gate

Generate a whole-branch review package against the correct merge base. Fix every Critical/Important finding with RED→GREEN regression coverage, rerun the full suite/static gates, then create the follow-up PR. Required GitHub CI and all CodeQL analyzers must be green before merge. Do not deploy or enable diagnostics on the live bridge as part of this feature.

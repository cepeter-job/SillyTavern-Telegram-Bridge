# Memory Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, low-overhead memory diagnostics that distinguish Python allocation growth from native/process growth without running `tracemalloc` from startup.

**Architecture:** Create an isolated `bridge.memory_diagnostics` process-local service. It samples Linux RSS cheaply, escalates through explicit incident states, starts `tracemalloc` only at 320 MiB, captures at 384 MiB, writes private bounded reports, then stops tracing and waits for recovery hysteresis before re-arming.

**Tech Stack:** Python 3.11+, stdlib `threading`, `tracemalloc`, `json`, `pathlib`, Linux `/proc`, pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-10-01-memory-diagnostics-design.md`

## Global Constraints

- Disabled unless `SILLYTAVERN_MEMORY_DIAGNOSTICS=1`.
- Sample interval is exactly 20 seconds.
- Warning threshold is 256 MiB RSS.
- Start allocation tracing at 320 MiB RSS.
- Capture at 384 MiB RSS.
- Re-arm only after three consecutive samples below 256 MiB.
- Retain at most three completed incident reports.
- Reports are allowlisted metadata only: no prompts, responses, credentials, object values, provider bodies, database contents, or arbitrary exception text.
- Report directory is mode 0700 and report files are mode 0600.
- Diagnostics are best-effort and must never change Telegram/provider/database behavior.

## Review Focus

1. Missing or malformed `/proc/self/status` must leave diagnostics armed without raising; Task 1 adds this test.
2. Threshold oscillation around 256/320/384 MiB must not create duplicate incidents or repeatedly toggle tracing; Task 1 adds boundary/hysteresis tests.
3. Pre-existing `tracemalloc` ownership must never be stopped by diagnostics; Task 1 adds ownership tests.
4. Counter callbacks that raise or return unsupported/sensitive values must be reduced to safe scalar output without escaping; Task 2 adds callback sanitization tests.
5. Symlinks/special files or rotation errors in the diagnostics directory must fail closed without affecting the bridge; Task 2 adds filesystem safety tests.

---
### Task 1: Sampling and Incident State Machine

**Files:**
- Create: `bridge/memory_diagnostics.py`
- Create: `tests/test_memory_diagnostics.py`

**Interfaces:**
- Produces: `DiagnosticState(str, Enum)` with `DISABLED`, `ARMED`, `WARNED`, `TRACING`, `CAPTURED`.
- Produces: `MemoryDiagnostics(bridge_home: Path, environ: Mapping[str, str], safe_counters: Callable[[], Mapping[str, object]] | None = None)`.
- Produces: `MemoryDiagnostics.start() -> bool`, `sample_once() -> DiagnosticState`, `stop(timeout: float = 1.0) -> bool`, and read-only `state`.
- Internal readers: `_resident_memory_kib(path: Path = Path("/proc/self/status")) -> int | None` and `_smaps_rollup_kib(path: Path = Path("/proc/self/smaps_rollup")) -> dict[str, int]`.

- [ ] **Step 1: Write failing tests for disabled/default behavior and Linux memory parsing**

Add tests asserting diagnostics are disabled without the environment flag, enabled with it, parse `VmRSS` KiB correctly, return `None` for missing/malformed status files, parse numeric `smaps_rollup` KiB aggregates, ignore malformed/unknown-unit fields, and return `{}` when `smaps_rollup` is unavailable.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest -q tests/test_memory_diagnostics.py -k 'disabled or rss or smaps'`

Expected: FAIL because `bridge.memory_diagnostics` does not exist.

- [ ] **Step 3: Implement the enum, constants, constructor, and RSS/smaps readers**

Use fixed constants from Global Constraints. For `smaps_rollup`, allow only `Rss`, `Pss`, `Pss_Anon`, `Pss_File`, `Private_Clean`, `Private_Dirty`, `Shared_Clean`, `Shared_Dirty`, `Anonymous`, and `Swap`; ignore other keys and non-KiB/malformed values. Missing smaps returns `{}`.

- [ ] **Step 4: Write failing state-transition tests**

Cover 255 MiB staying `ARMED`, 256 MiB -> `WARNED`, 320 MiB -> `TRACING`, 384 MiB -> `CAPTURED`, no duplicate capture while high, and exactly three below-warning samples to re-arm.

- [ ] **Step 5: Run transition tests and verify RED**

Run: `python -m pytest -q tests/test_memory_diagnostics.py -k 'transition or hysteresis or threshold'`

Expected: FAIL because `sample_once()` does not implement the state machine.

- [ ] **Step 6: Implement `sample_once()` and `tracemalloc` ownership**

Start tracing with one frame only when crossing the tracing threshold. Record whether diagnostics owns tracing. Stop it after capture or `stop()` only when diagnostics started it.

- [ ] **Step 7: Add and pass pre-existing-tracing ownership tests**

Assert an already-running `tracemalloc` session remains active after capture and stop.

- [ ] **Step 8: Implement the daemon sampler lifecycle**

`start()` starts one daemon thread only when enabled; the loop waits with `stop_event.wait(20)` before each sample, including the first, rather than using `sleep`. `stop()` signals and joins for at most the requested timeout.

- [ ] **Step 9: Run Task 1 tests**

Run: `python -m pytest -q tests/test_memory_diagnostics.py`

Expected: PASS.

- [ ] **Step 10: Commit**

`git add bridge/memory_diagnostics.py tests/test_memory_diagnostics.py && git commit -m "feat: add bounded memory diagnostic state machine"`

---
### Task 2: Private Reports, Safe Counters, and Rotation

**Files:**
- Modify: `bridge/memory_diagnostics.py`
- Modify: `tests/test_memory_diagnostics.py`

**Interfaces:**
- Consumes: Task 1 `MemoryDiagnostics` state machine and memory readers.
- Produces internal report helpers that write versioned JSON under `bridge_home / "diagnostics/memory"`.
- Report keys: `schema_version`, `timestamp_utc`, `pid`, `state`, `rss_kib`, `smaps_kib`, `traced_current_bytes`, `traced_peak_bytes`, `top_sites`, `thread_count`, `safe_counters`.

- [ ] **Step 1: Write failing privacy/report-shape tests**

Allocate a private sentinel string, capture a report, and assert the sentinel is absent. Assert every top-site entry has only `file`, `line`, `size_bytes`, and `blocks`.

- [ ] **Step 2: Run privacy tests and verify RED**

Run: `python -m pytest -q tests/test_memory_diagnostics.py -k 'report or object_values or privacy'`

Expected: FAIL because report generation/persistence is not implemented.

- [ ] **Step 3: Implement allowlisted report construction**

Use `tracemalloc.get_traced_memory()` and top 30 `snapshot.statistics("lineno")` entries when tracing. Use `threading.active_count()`. Never serialize traceback locals, object reprs, arbitrary exceptions, or callback values other than safe scalars.

- [ ] **Step 4: Write failing safe-counter tests**

Accept at most 32 entries with short string keys and values of exact type `bool` or bounded `int`; drop strings, containers, floats, oversized integers, malformed keys, and all values if the callback raises.

- [ ] **Step 5: Implement safe-counter sanitization**

Callback failure must yield `{}` and log only a generic diagnostics warning without exception text. Allow keys matching `[A-Za-z0-9_.:-]{1,64}`, exact `bool` values, and signed 64-bit integers only; cap output at 32 entries.

- [ ] **Step 6: Write failing filesystem/rotation tests**

Assert directory 0700, file 0600, atomic temp cleanup, refusal of symlink destination/temp paths, and retention of newest three reports after four captures. Add a rotation-failure test showing the newest report survives.

- [ ] **Step 7: Implement atomic private writes and rotation**

Use same-directory temporary files opened with `O_NOFOLLOW` when available, `os.replace`, and filenames `memory-YYYYMMDDTHHMMSSZ-<pid>-<incident>.json`. Reject symlinks/special files before writing.

- [ ] **Step 8: Run Task 2 tests**

Run: `python -m pytest -q tests/test_memory_diagnostics.py`

Expected: PASS.

- [ ] **Step 9: Commit**

`git add bridge/memory_diagnostics.py tests/test_memory_diagnostics.py && git commit -m "feat: persist private memory incident reports"`

---
### Task 3: Runtime Lifecycle Integration

**Files:**
- Modify: `bridge/runtime_lifecycle.py`
- Modify: `tests/test_runtime_entrypoint_invariants.py`
- Modify if needed: `tests/test_runtime_architecture_guards.py`

**Interfaces:**
- Consumes: `MemoryDiagnostics(services.config.bridge_home, services.config.environ)`.
- Runtime must call `start()` once before polling and `stop(timeout=1.0)` during normal shutdown.
- First release passes no application/database callback; reports still contain `safe_counters: {}` and the module-owned `thread_count`.

- [ ] **Step 1: Write failing lifecycle contract test**

Patch the runtime diagnostics constructor with a recorder and assert one `start()` call precedes Telegram polling and one `stop(timeout=1.0)` call occurs during shutdown.

- [ ] **Step 2: Run lifecycle test and verify RED**

Run: `python -m pytest -q tests/test_runtime_entrypoint_invariants.py -k memory_diagnostics`

Expected: FAIL because runtime does not construct diagnostics.

- [ ] **Step 3: Add the minimal runtime hook**

Import `MemoryDiagnostics` in `runtime_lifecycle.py`, construct from immutable application settings, start before the polling loop, and stop in the existing shutdown sequence. Diagnostics start/stop errors must not escape.

- [ ] **Step 4: Add architecture/import guard if required**

The new module may import only stdlib plus `bridge.settings` types if needed; it must not import database, provider, Telegram, conversation, or job application layers.

- [ ] **Step 5: Run lifecycle and architecture tests**

Run: `python -m pytest -q tests/test_runtime_entrypoint_invariants.py tests/test_runtime_architecture_guards.py tests/test_memory_diagnostics.py`

Expected: PASS.

- [ ] **Step 6: Commit**

`git add bridge/runtime_lifecycle.py tests/test_runtime_entrypoint_invariants.py tests/test_runtime_architecture_guards.py && git commit -m "feat: integrate memory diagnostics with runtime lifecycle"`

---
### Task 4: Operator Documentation and Whole-Repository Verification

**Files:**
- Modify: `docs/operations.md`
- Modify: `docs/configuration.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Documents the single opt-in switch `SILLYTAVERN_MEMORY_DIAGNOSTICS=1`.
- Documents fixed thresholds, report path, three-report retention, privacy boundary, and enable/restart/collect/disable workflow.
- Must state that diagnostics are disabled by default and are not automatically added to the production service.

- [ ] **Step 1: Add operator documentation**

Document report interpretation: compare RSS/smaps totals with traced-current/peak bytes to distinguish Python-traced growth from native/untraced growth.

- [ ] **Step 2: Run documentation/static checks**

Run: `python -m ruff check . && python -m ruff format --check . && python tools/static_analysis.py`

Expected: all commands exit 0.

- [ ] **Step 3: Run the complete test suite**

Run: `python -m pytest -q -n auto --maxprocesses=4 --dist=loadfile`

Expected: zero failures.

- [ ] **Step 4: Run repository integrity checks**

Run: `git diff --check && python -m compileall -q bridge tests tools`

Expected: both exit 0.

- [ ] **Step 5: Verify rollout remains opt-in**

Search tracked service/config templates and assert there is no unconditional `SILLYTAVERN_MEMORY_DIAGNOSTICS=1`.

- [ ] **Step 6: Commit**

`git add docs/operations.md docs/configuration.md CHANGELOG.md && git commit -m "docs: document memory diagnostics workflow"`

## Final Review Gate

Request a whole-branch review against the spec. Fix all Critical/Important findings, rerun the complete verification commands, then create a PR. Do not enable diagnostics on the live bridge as part of merge/deployment.

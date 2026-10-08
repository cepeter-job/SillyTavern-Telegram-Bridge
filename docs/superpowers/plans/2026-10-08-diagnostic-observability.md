# Diagnostic Observability Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans task-by-task. Record verification and decisions in the work ledger.

**Goal:** Content-free troubleshooting from user action through provider, workers and delivery, with an authenticated diagnostic timeline/export.

**Architecture:** Extend Python logging with bounded scalar event context and JSON formatting; instrument existing boundaries; reuse Mini App authentication/session ownership for bounded on-disk log reads.

**Tech Stack:** Python 3.11 standard library, existing aiohttp and vanilla JavaScript. No new runtime dependencies.

**Spec:** ../specs/2026-10-08-diagnostic-observability-design.md

## Global Constraints
No live deployment, provider calls, unbounded storage, credential/plaintext story capture, compatibility facades, or weakened CI. New Python modules remain below 500 lines.

## Review Focus
Context must not cross users or retain unrelated ContextVars. Recovered jobs must retain correct source identity. Disk errors and malformed records must not break work or authorization. Rotated files must stay private under permissive umasks. A fallback or absent usage must never appear as a successful primary or zero cost.

### Task 1: Logging foundation
Files: bridge/diagnostic_events.py, bridge/diagnostic_logging.py, bridge/runtime_logging.py; tests/test_diagnostic_events.py, tests/test_diagnostic_logging.py; environment and operations docs.
Interfaces: diagnostic_scope(**fields), diagnostic_context(), bind_diagnostics(callable), event(name, **fields), scope_reference(kind, value), JSON formatter and secure handlers.
- [ ] Write behavioral tests for isolation, bounded events, privacy, file rotation and disk failures.
- [ ] Confirm expected failures on the baseline.
- [ ] Implement and run focused regressions, lint and format.
- [ ] Commit and open checkpoint PR.

### Task 2: Boundary instrumentation
Files: provider_port.py, background.py, scheduler_safety.py, job_service.py, update_routing.py, Mini App HTTP/session boundaries, memory workers, turn/delivery and Director/tracker owners.
Interfaces: consume Task 1 scope and event helpers, preserve existing application return values and exceptions.
- [ ] Write failures for correlated provider fallback/accounting and queued/recovered work.
- [ ] Instrument lifecycle, causal identity, failures and recovery without content.
- [ ] Run provider/job/delivery/memory tests and the complete suite.
- [ ] Commit and open checkpoint PR after dependency is merged.

### Task 3: Diagnostic timeline and export
Files: bridge/diagnostic_reader.py, bridge/miniapp_diagnostics.py, bridge/miniapp_http.py, bridge/miniapp_assets/diagnostics.js, bridge/miniapp_assets/system.js, tests and operations docs.
Interfaces: bounded read_events(log_file, authorized scope, filters), authenticated GET diagnostics and export routes; no client-controlled paths.
- [ ] Write failures for bounded/malformed/rotated records, foreign-session isolation, export privacy and UI filters.
- [ ] Add read-only timeline, incident filtering, trace lookup and export.
- [ ] Run full Python/browser/static/type/security verification and review all diffs.
- [ ] Open PR and merge only exact verified heads; record merged commits.

# Mini App Memory Monitoring Design

Date: 2026-10-01

## Intent

Expose the memory-diagnostics results from PR #292 in the Telegram Mini App without making the dashboard heavy, leaking host details, or turning monitoring into a remote control surface.

The Home dashboard should answer "is memory healthy right now?" at a glance. The System page should answer "what did the latest memory incidents show?" without requiring SSH or reading JSON files manually.

## Success criteria

- Home adds a fourth Bridge Health card for Memory.
- `/status` remains lightweight and returns only a bounded diagnostics summary.
- Detailed incident history is fetched only from a dedicated read-only endpoint.
- At most the three retained incidents can be returned.
- No prompt, response, credential, database content, object value, arbitrary exception text, or absolute host path reaches the Mini App.
- Mini App cannot enable, disable, or change thresholds for diagnostics.
- Missing/disabled diagnostics never break Home or System rendering.
- PR #292 remains unchanged; this work ships as a separate follow-up PR.

## Dependency and branch model

This feature depends on the `MemoryDiagnostics` subsystem introduced by PR #292. Development is stacked on `feature/memory-diagnostics`.

After #292 merges, the implementation branch should be rebased or retargeted to `main` before its own PR is merged. The follow-up must not duplicate or fork the core diagnostics implementation.

## Architecture

Promote the process-local `MemoryDiagnostics` instance into the root `BridgeServices` composition so both runtime lifecycle and Mini App status can read the same in-memory object.

Ownership remains separated:

- `bridge.memory_diagnostics` owns sampling, incident state, reports and report sanitization.
- `bridge.runtime_lifecycle` owns diagnostics start/stop.
- `bridge.miniapp_system` exposes read-only status/detail API views.
- `bridge/miniapp_assets/system.js` renders Home and System monitoring UI.

The Mini App does not read diagnostic files directly and does not receive a filesystem path from the server.

## Diagnostics service interface

Add a read-only snapshot API to `MemoryDiagnostics`:

`summary() -> dict[str, object]`

It returns only: enabled, state, RSS, fixed warning/tracing/capture thresholds, report count, and latest incident timestamp/RSS when available.

The object keeps the last successfully sampled RSS in memory. Calling `summary()` must not force a `/proc` read, scan report files, trigger a sample, start tracing, or write a report. On construction, it initializes a bounded report index from at most the three retained reports; successful captures update that index in memory.

Add a second bounded API:

`recent_reports() -> tuple[dict[str, object], ...]`

It returns newest-first sanitized views of at most the three retained reports. Retained reports remain readable when active monitoring is disabled, so an operator can disable diagnostics after collection without losing Mini App visibility. Reading failures omit only unreadable reports and are logged without sensitive text.

## Report sanitization

Detailed Mini App reports are a second allowlist over the already-private disk report. The API may expose timestamp, RSS, selected `smaps_rollup` numeric aggregates, traced current/peak bytes, thread count, safe counters, and top allocation sites.

Allocation-site paths must be normalized before API return. Prefer a repository-relative path when the site is inside the loaded bridge root, for example `bridge/generation.py:251`.

For sites outside the bridge root, expose only the basename or a stable `external:<basename>` label. Never return `/home/...`, virtualenv paths, user home directories, environment values, or raw arbitrary paths.

Unknown report keys are ignored rather than forwarded.

## Mini App API

Extend `GET /status` with a `memory_diagnostics` object containing only `MemoryDiagnostics.summary()`.

Exact summary shape:

```json
{
  "enabled": false,
  "state": "disabled",
  "rss_kib": null,
  "thresholds_kib": {"warning": 262144, "tracing": 327680, "capture": 393216},
  "report_count": 0,
  "latest_incident": null
}
```

`latest_incident`, when present, contains only `timestamp_utc` and `rss_kib`. When diagnostics are unavailable from composition, use the same disabled fallback shape.

Add `GET /memory-diagnostics` returning `summary` plus newest-first sanitized `reports`, maximum three. Each report contains only `timestamp_utc`, `rss_kib`, `smaps_kib`, `traced_current_bytes`, `traced_peak_bytes`, `thread_count`, `safe_counters`, and `top_sites`. The API itself limits `top_sites` to ten entries per report.

The endpoint is authenticated exactly like existing Mini App API routes and is read-only. It does not accept POST, PATCH, or DELETE methods. It is process-scoped and must not open the application database or acquire a session/database lock.

## Composition and lifecycle

Add `memory_diagnostics: MemoryDiagnostics | None` to `BridgeServices`.

Startup composition constructs one instance from `config.bridge_home` and `config.environ`.

`run_bridge_runtime()` must reuse `services.memory_diagnostics` rather than constructing another instance. It continues to start it before polling and stop it during graceful shutdown.

This makes the Mini App and runtime observe the exact same process-local state. There must never be two samplers for one bridge process. Tests must pin single-instance ownership.

## Home dashboard

The existing collapsed Bridge health panel grows from three cards to four: Bridge, Telegram, Database, Memory.

Memory card content is deliberately compact.

Disabled: Diagnostics Disabled; current RSS when known, otherwise Not sampled.

Armed: RSS, Armed badge, and Warning at 256 MiB.

Warned/Tracing: RSS, state badge, and the next boundary such as Capture at 384 MiB.

Captured: RSS, Captured badge, latest incident time, and retained report count.

The Home card must not fetch `/memory-diagnostics`; Home uses `/status` only.

## System page

Add a dedicated Memory diagnostics card after the normal runtime health grid.

The card first renders the lightweight summary from `/status`, then requests `GET /memory-diagnostics` for details. A detail request failure must not blank the System page.

Detailed content includes monitoring enabled/state, current RSS, thresholds, latest selected smaps totals, Python traced current/peak bytes, thread count, recent incident timestamps/RSS, and a collapsed Top Python allocations section per selected incident.

Limit top allocation sites displayed per report to ten even if the disk report contains more. The UI must remain useful when there are zero reports.

## Interpretation hint

The System page may show a conservative diagnostic hint derived only from the latest report. Convert RSS KiB to bytes before comparison:

- If `traced_current_bytes >= 60%` of RSS bytes: Python-traced allocations are a significant share of process memory.
- If `traced_current_bytes <= 35%` of RSS bytes: Native or otherwise untraced memory appears significant.
- Otherwise: The report is mixed; inspect allocation sites and process-memory totals.

This is explicitly a hint, not a root-cause verdict. Because `tracemalloc` starts only after the 320 MiB threshold, untraced memory may include pre-existing Python allocations as well as native allocations. Do not label a module, provider, model, or dependency as the leak source automatically.

The UI shows only the textual hint; it does not display a precision score.

## Privacy and security

The monitoring surface is read-only.

Do not add Mini App controls for enabling/disabling diagnostics, changing thresholds, deleting reports, forcing captures, or starting/stopping `tracemalloc`. Those remain operator-controlled process/service settings.

The API never exposes filesystem report names or paths. It returns sanitized data objects only.

The existing Mini App identity/authentication, rate limiting, CSP, and same-origin rules remain unchanged.

Optional status/detail failures must degrade to Unavailable or No reports, not leak exception text.

## Error handling

If `MemoryDiagnostics.summary()` fails unexpectedly, `/status` uses the disabled fallback rather than failing the entire status request.

If report reading/parsing fails, `/memory-diagnostics` still returns the current summary and omits malformed reports or returns an empty report list. Server logs use generic error categories without raw report content.

If diagnostics are disabled, the detail endpoint remains valid and returns any retained historical reports. If none exist, it returns an empty list. This lets the UI use one stable rendering path and preserves post-incident review after monitoring is turned off.

No report read should hold application or database locks.

## Testing

Backend tests:

- composition creates exactly one `MemoryDiagnostics` instance;
- runtime lifecycle starts/stops the composed instance;
- `/status` contains bounded summary and stable disabled fallback;
- `/memory-diagnostics` returns at most three newest reports;
- unknown report fields are dropped;
- absolute allocation paths are sanitized;
- malformed/unreadable reports do not break `/status` or the detail endpoint;
- no write route exists for diagnostics.

Frontend/browser smoke tests:

- Home health grid contains Memory as the fourth card;
- disabled, armed, warned, tracing and captured states render concise labels;
- Home does not request `/memory-diagnostics`;
- System renders incident details and top-site collapse;
- zero reports render an empty-state message;
- failed detail fetch leaves System status usable;
- no raw absolute path is rendered.

Full repository CI, CodeQL, Mini App smoke tests, Ruff, Mypy, dependency graph, security coverage, compileall, and diff checks must remain green.

## Rollout

The follow-up is display-only and must not change the production service environment.

After merging, operators who intentionally enable `SILLYTAVERN_MEMORY_DIAGNOSTICS=1` will see live state and incident results in the Mini App after restart.

When diagnostics are disabled, the dashboard reports Disabled while still showing the count/latest timestamp of retained incidents, and the System page can still review those retained reports.

No migration is required. Existing report files remain private and compatible; the UI consumes only sanitized server-side projections.

## Non-goals

- historical graphing or long-term metrics storage
- Prometheus/Grafana integration
- remote configuration controls
- automatic OOM remediation or service restart
- automatic leak-source attribution
- exposing full raw report JSON

# Memory Diagnostics Design

Date: 2026-10-01

## Intent

The bridge has experienced process OOM kills. The goal is to capture enough
diagnostic evidence to identify whether growth is primarily Python allocations,
native/private memory, thread growth, or accumulating runtime work without
meaningfully increasing steady-state memory pressure.

The existing allocation-profiler WIP is not deployed because it starts
`tracemalloc` at process boot and leaves it active until RSS reaches 384 MiB.
That can materially change memory behavior during the incident being measured.

## Success criteria

- Diagnostics are opt-in and disabled by default.
- Normal enabled overhead below the warning threshold is a cheap RSS sample every 20 seconds.
- `tracemalloc` is enabled only during a bounded high-memory diagnostic window.
- One incident cannot continuously allocate diagnostic data.
- Reports contain process/runtime metadata only, never prompts, responses,
  credentials, Python object values, provider bodies, or database contents.
- Diagnostic failure never stops Telegram polling or changes application state.
## Scope

Add a new isolated `bridge.memory_diagnostics` module and a small runtime
lifecycle hook. The module owns sampling, thresholds, private file output,
incident rotation, and temporary allocation tracing.

The first version uses one environment switch:

`SILLYTAVERN_MEMORY_DIAGNOSTICS=1`

No threshold configuration is exposed initially. Fixed thresholds keep the
surface small and make reports comparable across incidents.

### Fixed policy

- sample interval: 20 seconds
- warning threshold: 256 MiB RSS
- allocation-tracing threshold: 320 MiB RSS
- capture threshold: 384 MiB RSS
- retain: newest 3 completed incident reports
- one allocation snapshot per process incident
- stop `tracemalloc` immediately after capture when diagnostics started it

A process that drops below 256 MiB after an incident may arm a new incident
only after a recovery hysteresis period; repeated samples above a threshold
must not create repeated files.
## Architecture

`MemoryDiagnostics` is a small process-local service with no database access
and no provider or Telegram dependencies.

`run_bridge_runtime()` starts it after application settings are available and
stops it during normal shutdown. The sampler runs in one daemon thread.

The service accepts an optional safe-counter callback. The runtime layer may
supply only integer/boolean operational counters such as:

- Python thread count
- durable job counts by coarse state
- background executor/backlog counts when cheaply available
- provider-runtime-health entry count

The diagnostics module must not import higher application layers to obtain
these values. If a counter cannot be obtained cheaply and without side
effects, omit it.

Data flow:

1. sampler reads current RSS;
2. at 256 MiB it records an incident warning snapshot;
3. at 320 MiB it starts one-frame `tracemalloc` if not already tracing;
4. at 384 MiB it reads process/native memory detail and captures top allocation sites;
5. it writes one private incident report atomically;
6. it stops tracing if it owns the tracing session;
7. it enters captured state until recovery hysteresis re-arms it.
## Memory sources

On Linux, current RSS comes from `/proc/self/status` (`VmRSS`).

At warning/capture boundaries, diagnostics also attempt
`/proc/self/smaps_rollup` and retain only numeric aggregate fields useful for
distinguishing anonymous/private/shared/native growth. Missing permissions or
unsupported kernels are non-fatal.

The allocation snapshot includes only aggregate allocation-site metadata:

- filename
- line number
- allocated bytes
- block count

It never serializes local variables, object reprs, trace values, strings held
by application objects, stack locals, or heap object contents.

## Report storage

Reports live under:

`$SILLYTAVERN_BRIDGE_HOME/diagnostics/memory/`

Files are mode 0600 and the directory is mode 0700. Writes use a same-directory
temporary file plus atomic replace. Symlinks and special files are rejected.

Each report contains a versioned schema, UTC timestamps, process ID, RSS,
smaps aggregates when available, traced current/peak bytes when available,
top allocation sites, thread count, and safe operational counters.

Retain at most the newest three completed reports. Rotation failure is logged
without deleting the newly captured report.
## Privacy and security

The report format is allowlisted rather than dumping generic runtime state.
No exception message from arbitrary application/provider code is written into
the report.

Paths in allocation-site records may identify repository module filenames but
must not include environment values or file contents. The module never reads
the bridge database, .env file, provider catalog, Telegram payloads, model
responses, character cards, memories, or RAG documents.

All failures are logged using exception type/category only where sensitive
text could otherwise be exposed.

## Failure behavior

Diagnostics are best-effort. Any sampler/read/write/tracing error:

- does not terminate the bridge;
- does not retry in a tight loop;
- disables only the failing incident stage when needed;
- never leaves a temporary file intentionally exposed;
- stops `tracemalloc` if diagnostics started it and can safely stop it.

If another component already owns `tracemalloc`, diagnostics may read it but
must not stop it.
## Lifecycle and state

Use explicit states rather than independent booleans:

- `DISABLED`
- `ARMED`
- `WARNED`
- `TRACING`
- `CAPTURED`

State transitions are monotonic within an incident. Re-arming after
`CAPTURED` requires RSS below the warning threshold for at least three
consecutive samples. This prevents threshold oscillation from creating files
or repeatedly starting tracing.

Shutdown requests the sampler thread to stop through an event; shutdown never
waits indefinitely for diagnostics.

## Service configuration cleanup

The old custom user service line:

`Environment=SILLYTAVERN_ALLOCATION_PROFILE=1`

is removed immediately because current `main` does not implement that flag.
The replacement flag is not added to the production service by default.
Operators enable it explicitly only during diagnosis.
## Testing

Development follows red-green TDD.

Required unit coverage:

- disabled-by-default behavior;
- 20-second policy represented without real sleeps;
- threshold/state transitions and hysteresis;
- start/stop ownership of `tracemalloc`;
- report contains allocation-site aggregates but not object values;
- `smaps_rollup` parsing and missing-file fallback;
- private atomic file permissions and symlink refusal;
- maximum-three-report retention;
- diagnostic failures do not escape into runtime;
- safe-counter callback is bounded to allowlisted scalar values.

Integration coverage verifies runtime lifecycle start/stop hooks without
performing real high-memory allocation.

Full repository tests, Ruff, formatting, type-surface checks, architecture
checks, security-critical coverage, and `git diff --check` must pass before
merge.

## Rollout

Merge the feature disabled by default. Do not automatically add the new flag
to the production service.

For an OOM investigation, enable `SILLYTAVERN_MEMORY_DIAGNOSTICS=1`, restart
the bridge, collect up to three incident reports, then disable the flag and
restart. Compare Python traced bytes with RSS/smaps totals to distinguish
Python allocation growth from native/untraced growth.

Once this replacement is merged and verified, delete the obsolete
`pre-sync-main-2026-10-01 allocation-profiler-WIP` stash.

# Operation timing and memory queue diagnostics

Set `SILLYTAVERN_PERF_LOG=1` to enable the existing optional timing logger.
Timing records contain a fixed phase name, milliseconds and an opaque
`operation_id`. Durable workers use `job-<numeric ID>`; direct reply calls use a
random identifier. A context-local scope keeps concurrent workers separate and
restores the caller's identity after nested work. Correlation never changes
provider or delivery arguments. Numeric and Boolean extra fields are allowed;
string fields, chat/session identifiers, prompts, replies and credentials are
excluded from these timing records. Existing unrelated logging is unchanged.

When disabled, the timing helpers do not read clocks, create identifiers, format
fields or log. The durable worker does not perform its additional queue-timestamp
query. If its optional timestamp read fails, execution retains correlation and omits
queue wait. This instrumentation is observational and does not introduce a timeout.

## Timing boundaries

| Phase | What it measures |
| --- | --- |
| `queue_wait` | Durable enqueue to worker invocation. First attempts use the job's creation timestamp; retries use its last queued transition timestamp, captured before scheduling changes it. |
| `operation_execution` | The entire guarded worker invocation, including its child phases. |
| `context_assembly` | Initial history, retrieval, memory and NPC/simulation context through initial message assembly. Later action adjudication and final request budgeting remain outside this boundary. |
| `history_load` | SQLite execution, complete row materialization and reversal into chronological order. |
| `rag_retrieval` | The existing retrieval bundle call. |
| `memory_context` | The memory service's prompt-context coordination call. |
| `prompt_assembly` | The initial chat-message builder, within context assembly. |
| `provider_stream` | The provider call including stream callbacks. This includes any preview delivery performed by those callbacks. |
| `reply_delivery` | Final reply delivery, including delivery checkpoints. |

These durations are inclusive. Do not add child spans to their parent, or treat
`provider_stream` as pure network/provider latency. The timing records are phase
observations rather than an exhaustive, nonoverlapping latency accounting.
Wall-clock changes can affect queue age; negative durations are clamped to zero.

## Memory job queue counters

Memory incident reports collect these counters through a separate read-only
SQLite connection. They do not initialize schema, create a missing database,
recover leases, claim work or change queue timestamps. Collection happens only
when an enabled memory diagnostic report is built, not on each ordinary turn.
An unavailable/old database reports `memory.jobs.available=false`.

The scheduler and counters share current-session, recent-source, retry and
per-chat mode predicates. Eligibility uses the exact session incarnation and the
existing 24-hour recent-message rule. Hindsight keeps its exemptions from that
recent-message rule and the repeated-failure parking rule. Hindsight and curator
still require `memory_mode:<chat>=on`, defaulting to on; other layers retain their
existing behavior. Manual claims retain their existing autonomous-rule bypass.

| Counter suffix under `memory.jobs.` | Meaning |
| --- | --- |
| `pending` | Jobs with dirty work beyond their completed version. |
| `leased` | Pending jobs with a live lease. |
| `inactive` | Unleased jobs without the current session incarnation, or without recent source activity where required. |
| `disabled` | Unleased current-session Hindsight/curator jobs whose per-chat memory mode is off. |
| `parked` | Otherwise active jobs stopped after repeated work/retain failures, with the existing Hindsight exemption. |
| `backoff` | Otherwise eligible jobs whose next attempt is in the future. |
| `eligible` | Pending jobs passing the normal autonomous readiness predicates, including expired leases recoverable by the dispatcher. |
| `eligible_age_unknown` | Eligible jobs whose pending-cycle origin is historically unknown. |
| `oldest_eligible_age_ms` | Age since the oldest currently eligible job became pending; zero for no eligible jobs, or -1 if any eligible origin is unknown. |

Categories are mutually exclusive in the table's priority order. Eligible backlog
excludes live leases but does not imply free executor capacity: the existing
shared concurrency limit still applies, and retirement/discovery work can take
priority. These counters cover `memory_jobs`, not the separate cleanup queues;
existing background counters remain available in the same incident report.

Migration 31 records a nullable `pending_since` at a real enqueue. It preserves
the beginning of a continuous pending cycle through coalesced transcript changes,
retries, parking and idle periods. After the cycle completes, a subsequent enqueue
starts a new timestamp. Historical pending rows stay unknown through coalescing;
the migration does not infer their age from session creation, source-message time
or the retry deadline. This metric describes currently eligible jobs' pending
age, not accumulated time spent eligible.

The canonical per-chat filter also corrects a dispatcher corner case: an off-mode
job remains untouched when another chat has the same layer enabled. Previously
that layer-wide prefilter could consume a claim and fail it as disabled.
Hindsight retirement, cleanup and archival recovery continue while mode is off.

Regression tests use temporary SQLite databases and synthetic provider/delivery
boundaries. No production database, configuration, credential or live model call
is needed to verify these contracts.

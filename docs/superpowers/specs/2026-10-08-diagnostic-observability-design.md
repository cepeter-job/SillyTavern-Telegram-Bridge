# Diagnostic observability design

Approved scope: implement all troubleshooting improvements from the conversation, create PRs, and merge after required checks. No live deployment or paid provider calls.

## Goals and boundaries
Trace Telegram and Mini App actions through durable/background work, provider attempts, saved turns, delivery, Director/tracker decisions and memory work. Preserve the existing Python logging, health, usage and job infrastructure. Do not introduce a new service, dependency, database, unbounded event queue, or raw-content capture.

## Design
Use a small ContextVar containing allowlisted scalar diagnostic fields. Capture only that variable across thread/ordered-queue boundaries, never arbitrary application contexts or payloads. Reconstruct durable job identity from existing job rows on recovery. Separate a request ID, durable job ID, provider call ID and attempt number. Pseudonymize chat/session values; never export private user names or story names. Recovered derived memory retains its own durable identity and source boundary, not a fabricated original request identity.

Use JSON Lines through the existing rotating log, with UTC timestamps and bounded fields. Redact configured credentials, authorization material and exception messages. Legacy messages retain their templates, not arbitrary arguments. Tracebacks contain code locations without source lines or locals. Secure initial creation and every rollover, including permissive umasks. Keep INFO defaults and bounded configurable rotation. Mirror managed records to stderr for systemd. Diagnostic failures must not alter story delivery.

Read only a bounded tail of the configured log and its numbered rotations. Never accept client file paths. Revalidate event fields before returning them. Authenticated Mini App diagnostics use the existing server-side session ownership boundary; filter by the authorized chat/session before grouping requests or exporting. Exports contain metadata, events, incidents and reported usage subtotals, not legacy logs, prompts, replies, environment, credentials or database rows. Missing/unreported usage must remain explicit.

## Verification
Tests must demonstrate context isolation, cross-thread continuity, durable recovery, fallback reasons and suppressed fallback, provider role accounting, queue rejection, delivery/recovery events, secret and exception privacy, rotation permissions, bounded reads/retention, cross-session authorization and safe UI rendering. Run focused tests locally; full regression, browser, lint, type, secret and security gates are required on the exact PR head before merge. Do not bypass protection. Preserve unrelated work and the live process.

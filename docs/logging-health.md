# Logging health and source locations

[Operations and troubleshooting](operations.md#structured-runtime-logs) ·
[Timing diagnostics](operation-telemetry.md)

Open **Mini App → System → Troubleshooting diagnostics → Load diagnostics**.
The **Logging health** section warns when diagnostic event delivery, file writes
or journal writes have failed. It also identifies missing file/journal handlers.
The same warning flags are included in the authorized diagnostic JSON export.
No additional environment setting is required after deploying this code.

## Read the warning correctly

The warning covers the current process, not only the active story. It shares
Boolean failure flags, never another session's events or failure counts. The
health snapshot reads existing counters only: it adds no disk probes, timers,
model calls or logging framework. A configured handler is not proof that its
destination is currently writable. File-handler presence and failure flags apply
only to the configured log file being exported; a handler writing to another path
does not establish capture for this view. Event and journal flags remain
process-wide. No server path is included in the snapshot or export.

A file-write or event-delivery failure makes usage completeness unknown, even
when every retained start has a matching finish: an entire attempt may be missing.
Known token totals remain visible, including subtotals by purpose. A journal-only
failure warns separately without invalidating successfully retained file usage.

Check free disk space, log permissions and the service journal when logging is
degraded. Failures remain flagged after later successful writes. File/journal
counters cover the attached handlers' lifetimes; event failures cover the current
process. Restart resets this state, not historical omissions. No observed failure
is not proof of complete capture, especially for logs retained across restarts.
See [service controls](operations.md#service-controls) for the journal command.

## Find the failing code

Trusted bridge records in the private log and journal include a bounded source
basename, function and line number. Structured events identify their emitting
caller rather than the generic logging helper. Absolute source paths and external
caller names are not added. The session export continues to exclude source paths
and legacy free-form messages.

The Mini App event timeline displays severity, exception type, contract rejection
codes, output-cap and retry flags when present. Raw exception messages, prompts,
replies and private plans remain excluded. Existing rotation, redaction, ownership
checks and opt-in performance/request instrumentation are unchanged.

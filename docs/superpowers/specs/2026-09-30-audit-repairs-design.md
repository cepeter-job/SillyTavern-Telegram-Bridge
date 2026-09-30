# Audit repairs design

## Authorization and goal

The maintainer approved fixing the complete 30 September 2026 audit, using the
Hermes VPS repository, creating fix branches and pull requests, and merging and
synchronizing them without another approval round. The audit covers F01-F11 and
four associated hardening observations. Its source revision was `90edc7f`.
Implementation starts from `3cfd311`, which also contains the merged character
restore repair from PR #275. The existing checkout and other worktrees are preserved.

The goal is to make credential selection, conversation state, derived memory,
input ownership, parsing and durable recovery satisfy the audit's demonstrated
contracts. Existing service boundaries remain authoritative.

## Constraints

- Python 3.11 remains the supported target; use the committed hashed locks.
- Preserve immutable AppSettings, explicit ports, and actor/session context.
- Repository modules remain SQL-only and require caller-owned transactions.
- Network I/O must not run inside an application SQLite write transaction.
- Add forward migrations; do not change historical migration declarations.
- Preserve existing data, message row references, indexes and session isolation.
- Keep changes within the audited behaviors; no unrelated installer or UI redesign.
- Use synthetic records and intercepted external adapters for regressions.
- VPS worktree is canonical for integration, full verification, commits and pushes.
- Review mirrors must match its source tree; remote tool calls remain sequential.
- Do not deploy, restart a live service, publish a release or change private data.

## Provider and ingestion repairs

F01: An explicit image api_key_env is exclusive. Missing or empty values fail
before network I/O; the default text key is used only when no separate source
was deliberately selected. Preserve successful explicit and default routes.

The image JSON response is bounded before JSON/base64 decoding. Allow the
base64 representation of IMAGE_MAX_BYTES plus 64 KiB of JSON overhead; reject
oversized responses without reading the unbounded body. The decoded image limit
still applies independently.

F05: Replace repeated-suffix regex scanning with a linear markup scan. Preserve
the current plain-text extraction contract, including entities and malformed
unterminated text. Test worst-shaped markup in a killable child process rather
than allowing the test runner itself to hang. No new parser dependency is needed.

F11: A pending World Info upload belongs to its initiating actor and session.
Capture both from RequestContext. Atomically validate and consume the prompt;
another actor or stale queued session cannot clear it. Expired and legacy unowned
states fail closed and can be reopened. World Info itself remains shared.

Expired owner-bound memory-setting panels must not become usable by another
actor when the binding disappears. Preserve established callback identifiers.

## Memory repairs

F03: Curator work captures a durable invalidation revision before model I/O.
Reset, deletion and manual curator changes invalidate previous work. Completion
checks revision, session existence and relevant transcript identity under a
short write scope before storing state. Coordinate acceptance and remote retain
with the same per-session Hindsight lock used by purge; do not introduce a second
lock owner. No SQLite transaction remains open during model or backend I/O.

F06: List append/remove may preserve an existing NPC field audience but may not
implicitly change it. Reject an incompatible audience without changing value,
audience or field history. Keep whole-field replacement as the explicit way to
change audience. A union of audiences is not safe. Alice/Bob are story characters.

F07: Summary coverage reflects fully represented source messages. Process bounded
whole-row segments and persist only the last completed segment. A forced summary
must process successive segments or explicitly report that completion was not
possible. Handle prior-summary overhead and a single oversized message without
marking omitted text as covered. Preserve the original transcript on failure.

## Transcript, input policy and delivery repairs

F02: Delete alternatives for discarded downstream turns transactionally. Give
messages non-reusable row identity with a forward migration that preserves
existing row IDs and all columns/indexes. Keep pre-edit alternatives tied to
their prompt revision or remove incompatible alternatives; unrelated prompts
must never inherit discarded answers. Include reset and branch replacement.

F04: All conversational media use the existing GroupService.user_turn_allowed
policy at ingress and execution. Validate before downloads, transcription,
choice invalidation or generation; recheck the durable actor after queueing.
Already committed delivery-only recovery remains recoverable without generating
another turn. Do not re-resolve a queued session to the current session.

F08: Persist acknowledged Telegram chunk IDs as delivery progresses and distinguish
partial delivery from complete delivery durably. Recovery resumes the original
answer without sending acknowledged chunks again or calling the model again.
Cleanup can see every acknowledged chunk. Preview replacement follows the same
contract. Existing nonempty-ID historical replies remain complete unless a new
explicit partial state says otherwise.

F09: A locally committed edit is not complete until delivery is complete. Preserve
the committed branch and recover delivery under the original operation ID for
typed transport failures, including URLError and timeouts. Exhausted retries
produce an accurate actionable partial-success notice rather than claiming that
the old branch was preserved. Never use exception text to infer commit success.

F10: A transient SQLite error while claiming a job reaches durable requeue handling.
It must not be classified as a provider/business failure. Preserve bounded retry
and terminal failure behavior for actual business execution. Verify the claim
boundary for message, image, callback, native edit and other guarded workers.

## Governance and completion

Correct the stale coverage descriptions to the configured 76% floor. Align
documented merge checks with observed CI check names and the enforced branch
policy, preserving other protection settings. Require the existing test,
dependency and static checks plus secret scan and the observed CodeQL analyses.
Do not invent a required status context that never reports.

Acceptance requires failing regressions for the defects, passing focused tests,
the complete suite on the unprivileged VPS, locked dependency checks, architecture,
Ruff, formatting, mypy, security coverage floors and Mini App DOM smoke. Independent
review must cover the combined changes and migrations. Check the exact final head
before merging; integrate concurrent main changes safely if needed.

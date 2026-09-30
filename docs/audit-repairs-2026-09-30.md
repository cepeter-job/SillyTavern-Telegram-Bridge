# Audit repairs — 30 September 2026

This record maps the eleven findings from the audit of revision `90edc7f` to
their repairs. The work follows the approved [design](superpowers/specs/2026-09-30-audit-repairs-design.md)
and [implementation plan](superpowers/plans/2026-09-30-audit-repairs.md).

## Finding coverage

| Finding | Repair | Regression boundary |
| --- | --- | --- |
| F01 — image credential fallback | An explicitly selected image credential must exist and be nonempty. Failure occurs before a request can carry the text-provider key. | Missing, empty, explicit and default credential routes through real request construction. |
| F02 — transcript and variant identity | New message identities cannot reuse deleted row IDs; branch edits discard incompatible response variants transactionally. | Populated database migration, rollback, highest-row deletion, edit followed by an unrelated prompt, and variant selection. |
| F03 — stale curator completion | Durable revision and source-identity checks reject obsolete results. The existing session lock orders acceptance and backend retention with reset and deletion. | Separate database connections, blocked provider completion, reset, manual change, session recreation and identical transcript recreation. |
| F04 — media turn-policy bypass | Conversational photo, image-document and voice paths check the canonical group policy before external work, both at ingress and in the queued worker. | Wrong actor, ownership changes after queueing, no download/transcription/provider effects, and successful owner input. |
| F05 — malformed markup cost | A linear scan replaces repeated suffix matching while preserving extraction behavior. | Valid tags/entities and a bounded subprocess processing a large unterminated input. |
| F06 — NPC audience broadening | Append/remove reject incompatible visibility or audiences without modifying the field or its history. Explicit replacement can change the audience. | Restricted audience mismatch, compatible append/remove and deliberate whole-field replacement. |
| F07 — false summary coverage | Bounded segments contain whole source rows. Coverage advances only after each successful segment; Telegram and the Mini App report incomplete work. | Prior-summary overhead, multiple segments, oversized rows, later failure, old-summary fallback and actual Mini App result consumption. |
| F08 — lost delivery acknowledgements | Each acknowledged Telegram chunk is checkpointed. Canonical completion state distinguishes partial delivery from complete delivery. | Three-chunk failures, preview replacement, greetings, continuation and persistence failure. |
| F09 — committed edit misclassified as complete | A committed edit remains recoverable until delivery completes. Typed transport failures retain the original operation and answer. | Native edit failures after local commit, bounded retry, original-session recovery and no repeated model call. |
| F10 — claim failure misclassified as business failure | Claim errors reach durable worker retry handling before business-error classification. | Transient SQLite claim failure across all seven guarded worker families, with business work executed once. |
| F11 — unowned pending World upload | The one-use prompt records its initiating actor/session and is atomically consumed only by that context. | Competing uploads, another actor's non-JSON file, wrong queued session, expired/legacy prompts and caller-owned transactions. |

## Delivery and queued-session behavior

Delivery state records the exact rendered answer and its acknowledged chunk IDs.
Retry uses the original operation, actor and session, without generating another
answer or selecting another response variant. Automatic recovery is bounded to
three total delivery attempts, including interrupted attempts and restart
recovery. The existing `/retry` command can recover a
terminal committed delivery when its stored target and answer are still valid.
Deleted, superseded or mismatched targets fail closed.

Ordinary text, image, voice and conversational-document replies bind their exact
user/assistant identity and rendered payload to the original job in the reply
commit. That binding survives transcript deletion. Recovery checks it before
generation, media processing or turn-state changes; a removed reply cannot be
recreated by automatic recovery or by a fallback from `/retry`. Greeting recovery
preserves its original expression and TTS behavior, including manual retries.

Bound recovery checks the original input, answer and rendered payload again in
the short transactions that prepare and acknowledge delivery. A superseded source
or checkpoint is rejected instead of being rebound to an old answer. Network
calls remain outside those transactions.

Long-running commands keep the session recorded when they were queued. Changing
the active session does not redirect a queued edit, regeneration or continuation.
Legacy recovered commands also use their durable session; an ambiguous committed
target is rejected rather than applied to another conversation.

Historical replies with nonempty Telegram IDs retain their completed status
unless an explicit new partial-delivery record says otherwise. Acknowledged
chunks are visible to cleanup even when a later chunk fails. Telegram delivery
and SQLite cannot commit atomically: if the process loses a successful Telegram
response before it can persist the acknowledgement, the application cannot prove
that delivery occurred. The repair preserves acknowledgements it receives and
successfully checkpoints; it does not claim exactly-once delivery across that
external failure window.

## Input and memory behavior

PNG documents can be either character cards or conversation images. The bridge
must download them to distinguish those forms, so manual group-turn policy now
gates ambiguous PNGs before downloading, including octet-stream uploads. This
also restricts out-of-turn PNG character-card uploads. JSON management uploads
remain available under their existing policies.

World upload authorization commits before download or installation. An importer
called inside an existing SQLite transaction rejects the call without committing
or rolling back the caller's work. A failed external installation requires the
owner to reopen the consumed one-use prompt.

Curator model calls run outside the session lock and database transaction. Accepted
state commits before external backend retention, while the existing session lock
keeps retention ordered with purge. Summary failures preserve source messages and
the last successful summary, and report the incomplete result to the user.

## Additional hardening

- Image JSON reads are limited to the encoded image-size ceiling plus 64 KiB of
  response overhead, with one overflow-detection byte. The decoded image-size
  limit remains independently enforced.
- Expired owner-bound memory panels fail closed and can be reopened normally.
- Coverage documentation agrees with the existing 76% statement/branch floor.
- Merge requirements include the observed secret-scan and CodeQL result/analysis
  checks in addition to tests, dependency audit and static analysis. Exact check
  names are documented in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Schema and verification

Migration 7, `message_identity`, gives messages an explicit `INTEGER PRIMARY KEY
AUTOINCREMENT` ID while preserving existing row IDs, transcript columns and
indexes. Existing `rowid` queries continue to use that identity. It removes
response variants whose source user turn is missing or no longer matches.
Migration 8, `assistant_delivery_progress`, adds the exact source content,
rendered payload, acknowledged IDs and completion state, with deletion cleanup.
Migration 9, `job_delivery_intents`, adds the immutable job-bound delivery record
described above. Job deletion cleans that record; transcript deletion leaves a
record that can reject stale recovery. Historical migration declarations remain
unchanged. Migration regressions cover populated upgrades, repeat initialization
and atomic rollback.

Legacy delivery records are backfilled only when the job and source can be
associated exactly and uniquely. When surviving incomplete-delivery evidence is
ambiguous, migration preserves a cancellation reason for already-attempted
ordinary/media jobs in the same chat and session. This conservative choice can
cancel another previously attempted job in that ambiguous scope, requiring a
fresh user request. Unattempted jobs and other chat/session scopes are preserved.
If both the original transcript and its checkpoint were erased before upgrade,
the migration cannot reconstruct that lost history; it does not guess which
job had committed. New replies bind their delivery intent atomically.

The pull request records the actual independent review and final verification
results: Python 3.11 on an unprivileged Linux user, hashed dependency locks, both
dependency audits, Ruff and formatting, architecture, mypy, full pytest coverage,
security coverage floors, shell syntax and the committed Node 24 Mini App DOM
suite. Test transports use synthetic credentials and intercepted external APIs.

Source integration is separate from deployment. This repair does not restart a
running service, run migrations against a production database, or publish a
release. Operators should use the existing [backup and deployment procedure](operations.md#database-migrations-backup-and-restore) when
adopting the repaired revision.

# Story memory runtime

Story memory is derived from canonical SQLite messages and explicit assertions.
The accepted reader scope includes session incarnation, source and rewrite
watermarks, historical boundary, and the complete set of active character
readers. That same scope is reused for local recall, remote ranking, scene,
summary, and NPC state.

## Indexed evidence

Migration 22 adds a rebuildable SQLite FTS5 index over episode summaries, with
backfill and insert/update/delete triggers. Retrieval searches the full corpus,
rather than a latest-message or latest-episode window. SQL filters ownership,
incarnation, source validity, historical bounds, and character audience before
ranking and limiting candidates. Exact source/payload validation still runs
before an item becomes prompt evidence.

A query contains at most 24 literal lexical terms. Expansion uses only the
already authorized scene block and the resolved active readers. Each search
admits at most 48 lexical candidates and six explicit assertions. FTS5
`unicode61` normalizes Unicode case and diacritics; it does not provide stemming
or language-specific word segmentation. Hindsight remains the optional semantic
ranking path.

Remote document IDs can rank eligible local facts; remote response text never
grants authority. Local and remote rankings are fused, duplicates removed, and
the resulting prompt evidence is limited to six items. Short evidence pointers
identify the canonical message and character range, or the local assertion ID.
Summary and current scene remain independently classified channels.

## Complete source-part extraction

Summary, scene, curator, and NPC extraction use the existing durable job leases.
A source part is at most 12,000 characters, and a claim handles at most eight
parts. Every character in an accepted part is supplied to extraction. A row
longer than one part remains incomplete until its final part is accepted.

Private drafts and source segments are committed together. Public payloads,
classification sidecars, NPC history, and coverage advance in the same
transaction only when an entire canonical row is complete. Model/network calls
run outside SQL write transactions. Acceptance compares the exact source,
lease, previous draft, and current published state so a late result cannot
replace a newer accepted publication.

Drafts are limited to 262,144 serialized bytes. The most recent eight complete
row checkpoints are retained per layer. A rewrite restores the newest valid
complete prefix and replays the suffix; when no checkpoint proves that prefix,
work replays from the layer's source floor. A bounded call can therefore return
incomplete progress while the durable backlog remains available for later claims.

Manual refreshes use the same claims, parts, drafts, and publication path.
NPC replay preserves accepted field history before the actual invalidated row,
even if no accumulator checkpoint remains. Each extraction part sees existing
NPC state only through the preceding source row, so retained later state cannot
be attributed to an earlier replay part.

Summary, scene, and curator Clear retire the affected private draft, checkpoints,
and accepted source segments, fence in-flight results, and schedule canonical
replay. Summary Clear includes its scene hook in the same transaction. A later
manual refresh can rebuild without an unrelated message append and cannot
restore an accumulator captured before Clear. Curator also retains its existing
explicit revision fence.

Migration 23 adds this private progress state. Previously published legacy
panel values may remain visible during rebuilding, but old summary/scene
classification is retired and old aggregate coverage does not prove complete
source consumption. The four native derived layers are deliberately replayed.
Migrations 20 and 21 retain their original definitions.

## Final request budgets

Ordinary story messages, edited turns, image input, regeneration, and continuation
assemble their final prompt before the shared budget gate. This includes the
Light Novel response contract and the actual generation settings. The gate runs
before provider dispatch and saves redacted diagnostics for both successful
admission and failure. Ordinary streaming creates its progress message only
when visible output arrives, so a later attempt-budget rejection cannot leave
an empty generation placeholder.

Input allowance is the model window minus the actual requested output allowance
and a safety margin. Explicit output settings are never silently reduced to make
an allocation fit. A valid remaining input budget can be smaller than 2,048
tokens; impossible allocations fail explicitly.

Compaction first removes optional history and evidence. Exact assembly spans
identify optional blocks so lookalike tags in current user text or fixed
instructions cannot make that text removable. Fixed instructions, current user
text, image data, the independently bounded scene block, and the final Light
Novel contract remain protected. Continuation also protects the exact assistant
answer being extended.

Every actual provider attempt rechecks its selected model, output allowance,
and normalized payload. This includes smaller fallback models, empty-response
recovery that increases output as high as 12,000 tokens, and appended automatic
continuations. If an automatic continuation no longer fits, already visible text
is preserved. Muse's minimum output allowance of 3,000 tokens and its mandatory
tool definitions are included. Attempt observations are persisted by the
application after generation, without adding database access to ProviderPort.

The estimator uses the configured characters-per-token ratio consistently for
both counting and trimming. It includes message overhead and a fixed allowance
of 1,024 tokens per image instead of treating base64 data as ordinary text.
These are admission estimates, not model-tokenizer or image-size guarantees.
Provider tokenizer behavior and image accounting may differ.

The Codex transport has no supported output-limit field in its existing wire
contract. Its selected output is reserved locally for admission and reported as
a reservation; no unsupported wire field is added. A local reservation does not
guarantee the provider will stop generation at that number.

Diagnostics contain counts, flags, model identity and request stage, not prompt
text, credentials, or image data. They reflect the last actual attempt when a
fallback, recovery, normalization, or continuation changes the request.

This change does not deploy the worktree, change live configuration or story
data, or restart production services.
# Local-first reset and deletion (migration 27)

Reset and session deletion commit their canonical SQLite changes together with
durable Hindsight cleanup obligations. Neither operation constructs a Hindsight
client, calls the provider, nor waits for an in-flight provider response. A local
SQLite failure rolls back both the content changes and cleanup queue. Successful
completion means the local operation committed and remote erasure was queued.

The application binds `QueueSessionMemoryCleanup` through
`MemoryService.queue_cleanup(db, chat_id, session_id)`. This SQL-only port requires
an active caller transaction and queues exact recorded raw/native/mapped document
IDs, known legacy IDs, dispatch uncertainty and old-generation discovery. Operation
deduplication happens before advancing the epoch. A replay of `local_committed`
does not reset again; the older `memory_purged` phase continues through the new
transactional queue. The epoch survives deletion and visible session-key reuse.

The standard Mini App purge route also uses this queue and returns `queued: true`.
It preserves the local transcript and accepted facts while revoking their old
external index authority. The integer-returning synchronous `purge_session`
compatibility helper remains synchronous and serialized for explicit compatibility
callers; reset, session deletion and the Mini App do not use it.

## Provider races and uncertainty

Native fact retain calls commit a unique attempt token before dispatch; historical
raw attempt tokens retain the same uncertainty guarantees.
Client construction, HTTP and closure run outside session lifecycle locks. Known
completion is recorded separately; a short acknowledgment transaction then checks
the live incarnation, source/fact validity, external epoch, mode and owned claim.
A late result cannot restore old prompt authority. A successful retry resolves
only its own completed token. A timeout or deadline never expires an older unknown
request. Migration 27 conservatively records uncertainty for old native index rows,
including pending rows whose dispatch cannot be established; these inferred watches
have no known start time.

Exact deletion also releases the lifecycle lock across the provider call. Its
acknowledgment checks the owned lease and captured retirement revision. Reopening
debt increments the revision without stealing the active lease, so an older DELETE
cannot mark the new obligation complete or postpone it. A dispatch outstanding at
the start of DELETE prevents terminal acknowledgment even if it finishes before
the DELETE response. Repeated invalidation of an already-invalid raw source does
not by itself reopen a settled retirement.

## Legacy discovery and scheduling

New native documents carry `st-memory-v2` and a hash of the chat, session,
incarnation and external epoch. Background discovery accepts only the exact old
generation or unmarked legacy documents with proven session-tag/prefix ownership.
Missing provenance is deferred. Discovery never invokes broad session deletion;
every selected object goes through the exact-ID outbox and current-source guard.

A worker performs at most two list calls of 1,000 items each, 16 exact deletes per
deletion batch, or 16 due dispatch reconciliations. One claimed memory operation is
admitted at a time. An enumerate pass pauses deletion for that scope and persists
its pagination cursor. Cleanup then runs, and verification starts from offset zero
until a complete pass finds no old objects or known pending obligations. Deferred
discovery resets its cursor and allows deletion of known IDs. Recurring discovery
and unresolved watches alternate with ordinary memory work so they cannot starve
new native ingestion. Executor rejection and process restart preserve the debt.

`/memory off` disables indexing and recall, while exact erasure and discovery
continue. Remote erasure is eventually retried when workers run and the provider
works. Recorded ambiguous calls remain pending indefinitely without upstream proof
of completion; an arbitrarily late unrecorded historical dispatch cannot be
reconstructed or conclusively erased by a finite scan. Cleanup rows do not contain
transcript/fact text. Their destination is the existing deterministic chat bank;
changing provider settings is not a supported migration of outstanding debt.

The unused unversioned curated remote export has been retired because it bypassed
durable dispatch tracking. Its Mini App action is removed, and stale API clients
receive HTTP 410 with guidance about automatic native fact indexing. Local curator
editing, saved reviewed items and native accepted facts remain available.

Tracked Telegram message and panel IDs are captured before reset removes local
rows, then deleted best-effort after the local commit. Telegram failures do not
restore a session; durable Telegram deletion retries are outside this checkpoint.

## Facts-only Hindsight and local branch readiness (migration 28)

The Hindsight worker indexes accepted native summaries only, with at most eight
retains per claim. It never creates source_segment mappings or Hindsight transcript
parts. Complete 12,000-character segmentation, extraction, provenance, drafts and
lexical retrieval continue in SQLite. A transcript-only dirty job can acknowledge
without any remote call; accepted native indexes arriving during a claim remain
pending on a later version. Cleanup outages do not block current native indexing.

Migration 28 records exact raw retirement targets, invalidates historical raw
segments and removes raw mappings. Its Hindsight claim fence changes the rewrite
identity, preserving native epochs, source floors, generation tags and accepted
native documents. It does not compact unresolved attempts or authority tombstones.
The raw retirement trigger ignores already-invalid rows on subsequent rewrites.

Alternate Ending initializes locally: canonical target validation, restored proof
and durable future processing. The retained memory_seeding operation stage and
memory_status field describe this local work. Production ready status is
independent of memory mode and provider availability; degraded means interrupted
or invalid local initialization. Initialization owns one short transaction and
retries do not repeatedly increase every layer's dirty version. Deleted targets
are rejected before completing their operation.

For native indexing diagnostics, count memory_fact_index rows by state for the
exact session incarnation (pending, retained or retired). Hindsight covered_id
is historical source state and is not a remote transcript-ingestion watermark.

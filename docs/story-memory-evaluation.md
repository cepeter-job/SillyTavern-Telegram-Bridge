# Story memory: architecture, operations and offline evaluation

This repository keeps accepted story text in SQLite and uses Hindsight to rank
locally authorized native facts. The deterministic evaluator exercises the real
session, accepted-turn, durable-worker, extraction, provenance, eligibility,
retrieval and final-request code with synthetic external adapters. Its scores
describe pipeline contracts under scripted classifications. They do not measure
a generation model's answer accuracy, prose quality or ability to classify
character knowledge.

Repository integration and production rollout are separate actions. The memory
upgrade and evaluator do not deploy code, restart services, migrate a live
database or change real provider configuration.

## Architecture and changes from the old windows

| Concern | Earlier behavior | Implemented behavior |
| --- | --- | --- |
| Accepted transcript | SQLite canonical history | SQLite remains canonical; accepted message triggers also create durable memory work in the same transaction |
| External transcript ingestion | Latest-message snapshots, then complete raw source parts | Removed: Hindsight receives only locally accepted native fact summaries; complete source evidence remains local |
| Native retrieval | Scan latest 200 events | Indexed lexical candidates from the corpus, followed by local scope and source validation |
| Semantic retrieval | Session-tagged Hindsight output | Direct world/experience native-fact document IDs rank locally accepted facts; rendered text comes from SQLite |
| Extraction coverage | Layers could share progress or truncate a message at 1,800 characters | Independent layer coverage, complete 12,000-character parts, resumable private drafts and complete-source publication |
| Character knowledge | A deduplicated audience union could grant knowledge backward | Independent source/audience attestations, explicit-event anchors and historical reader boundaries |
| Prompt budget | Compact before some appended instructions; reserve default output | Finalize after appended instructions, reserve requested output and recheck actual normalized provider attempts |

The accepted turn route writes canonical user/assistant rows, binds committed
operations, commits a NovelTurn when applicable and records response variants
before memory retention. Six layers own durable state: episodes, Hindsight,
summary, scene, curator and NPC. A missed executor submission therefore leaves
recoverable work. A successful summary does not acknowledge episode extraction.

Native facts need valid provenance, a source digest and explicit shared or
restricted classification. A remote document does not grant authority. Reader
scope includes session incarnation, accepted row boundary, principals, consumer,
rewrite journal watermark, explicit-event watermark and external epoch.
The same captured scope feeds episodic, semantic, summary, scene and NPC reads.
Returning candidates from slow recall does not refresh that request's knowledge
boundary.

Local lexical retrieval uses an index to find bounded candidates beyond the
old latest-200 limit. Hindsight supplies a separate semantic ranking path with
session and native-fact tags. Unknown documents, raw transcript documents,
observations, enrichment and legacy facts without accepted provenance cannot
supply derived prompt text. A successful candidate ID is rehydrated locally;
untrusted remote answer text is discarded.

A public fact in a mixed-secret source row authorizes that fact's rendered text.
Its source pointer identifies the canonical part; it does not authorize dumping
the complete source row. Summary/scene blocks require matching classified
sidecars bound to the parent digest, coverage and block identity. NPC history
is also bounded by source and reader scope.

## Forward migrations and backfill

The implementation preserves the existing dependency/storage choices and all
applied migration history through migration 27. Migration 24 added the raw
archival attempt ledger; migration 27 added native uncertainty, generation
discovery and retirement revision fences. Migration 28 retires raw indexing. Opening an isolated
older database uses the ordinary forward migrations; the evaluator creates a
new temporary database through the production database factory.

Migration 24 conservatively seeds one unfinished legacy watch per recorded raw
identity from Hindsight source segments or explicit source_segment mappings.
Accepted, pending and invalid recorded segments are included. A mapping without
a source segment supplies no proven incarnation; its incarnation and dispatch
time remain unknown. These records prove an identity was recorded, not that a
historical request was dispatched, is active or finished. Unrecorded identities
cannot be reconstructed. This one-time backfill is linear in recorded raw IDs;
it does not change canonical history, accepted coverage or purge floors.

Migration 28 queues the exact IDs from every historical Hindsight source segment,
source_segment mapping and raw attempt before invalidating raw segments and
removing only raw mappings. It revokes old Hindsight leases and increments the
rewrite identity once, without advancing the native external epoch or source
floor. Existing accepted facts, local provenance, other layers and valid native
mappings remain intact. Pending native candidates receive durable work; a raw-only
job completes without uploading transcript text. Full source/fact validation
remains at the native worker's external-call boundary.

Migration 28 does not erase unresolved attempts, recorded invalid raw identities
or authority tombstones. Reopening uses retirement revisions, and the trigger
requires an actual transition from nonzero validity to zero. Later edits cannot
reopen already-invalid raw rows merely by writing zero again. This is a forward
migration executed once, not a per-poll transcript rescan.

Existing unclassified artifacts are fail-closed for character prompt authority.
Backfill/replay runs through the durable layer jobs and accepted canonical
parts. It does not retroactively authorize opaque historical prose. Empty valid
extraction results advance their own complete coverage; malformed responses,
stale sources and partial failures leave work retryable. Each layer can finish,
fail or resume independently.

Part offsets are half-open Python/SQLite character coordinates, not encoded
byte offsets. The evaluator records fixture spans, canonical message IDs,
canonical content hashes, actual memory IDs and source-part evidence. Assistant
formatting may add surrounding roleplay markup; fixture span mappings account
for its canonical offset. Fixture markers use plain delimiters because the
ordinary delivery path sanitizes HTML and normalizes quoted assistant prose.
They are synthetic transport annotations, not a recommended story format.

## Recovery and lifecycle operations

Worker admission is bounded to one active memory claim. Episode extraction and
native indexing each allow at most eight external calls per claim. Hindsight jobs
consume pending native indexes only. A transcript mutation with no accepted native
facts completes its indexing job with zero calls; another poll is idle. Hindsight
covered_id is no longer a transcript-upload progress measure. Native progress is
reported by pending/retained/retired rows in memory_fact_index.

Retirement cleanup is independent: at most 16 exact document deletions per batch,
16 due attempt reconciliations, and bounded generation discovery. Failed DELETEs
do not block current native indexing. Recurring watches yield to ordinary work;
finite cleanup debt can receive a bounded turn before a due native claim. Every
remote call and client close occurs outside SQLite write transactions and session
lifecycle locks. Claims retain their incarnation, rewrite, epoch and ownership
fences, and failures use bounded retry delays.

Every new native retain commits a unique attempt token before dispatch. A positive
synchronous response is recorded separately, then the worker atomically accepts
the index/mapping and resolves its own finished token if source, payload, mode,
epoch and claim still match. Losing only claim ownership leaves a still-valid
native index pending for the same-ID retry. A successor cannot discharge an older
unknown attempt. A timeout, expired lease or lost process does not prove that the
upstream request stopped.

Historical raw calls always reconcile as obsolete, even if an old segment or
session survives. Startup, ordinary dispatch and direct cleanup reconcile bounded
batches without requiring surviving jobs or source rows. Branch readiness is
local and does not run reconciliation or remote cleanup. Known completion may be
resolved only with durable stale retirement in the same transaction.

Before each remote DELETE, cleanup captures the retirement revision, owning lease
and whether any dispatch is outstanding. Terminal acknowledgment requires the
same revision/owner and no uncertainty at either boundary. A later completion
therefore cannot be erased by an old DELETE acknowledgment. Unresolved attempts
remain on 300-second watches; calls made before their due time do no remote work.
Exact erasure and discovery continue while memory is off.

There is no automatic ledger or tombstone compactor. An arbitrarily delayed old
request can recreate its remote object between cleanup opportunities, so metadata
lifetime and cumulative deletion traffic are not bounded by elapsed time. Local
prompt authority remains denied; continued remote removal depends on future
worker opportunities and a functioning provider.

Edits, regeneration, deletes and moves invalidate affected suffix authority and
journal source rewrites. An unrelated later mutation preserves accepted earlier
prefixes; a harmless append preserves captured unchanged source parts. Historical
requests exclude discarded outcomes and future classified scene/summary/NPC
context. Summary/scene/curator Clear also retires private progress, so stale drafts
cannot recreate cleared artifacts. NPC replay preserves valid history before the
actual invalidation boundary and supplies extraction only with preceding state.

Branch/checkpoint restore copies canonical rows, remaps source parts/explicit
anchors, rebuilds target digests and incarnation, and queues target indexing.
Local readiness validates the target and idempotently queues missing extraction
work without a provider call, even when memory is off. It means canonical target,
restored local authority and durable future processing; it does not wait for every
derived job. Origin document acknowledgments are not target authority. A later source-branch
rewrite cannot mutate the copied branch evidence. Snapshot size limits remain
explicit: at most 512 facts and 1,048,576 serialized bytes. A complete oversized
proof is rejected rather than silently truncated.

Deleting and recreating a session with the same visible key creates a different
incarnation. Old scope, provenance and remote source identities must fail
authorization in the new session.

- **/remember** records a selected-character local assertion durably, even when
  external indexing is unavailable. Its index job is asynchronous and retryable.
  Live requests may use a new assertion immediately. Historical requests must
  occur strictly after its accepted-after anchor and within the explicit-event
  watermark.
- **/memory off** blocks indexing and recall without destroying eligible
  local native facts; exact cleanup and discovery continue. Pending work remains recoverable. **/memory on** makes
  pending work due again.
- **External purge** advances epoch/floor, retires external index authority and
  queues session-attributable documents for background verification/retry. Automatic
  workers cannot dispatch a new pre-purge source/index retain. A previously
  dispatched raw request may still finish and remains covered by its durable
  watch. Raw retirements stay pending after bulk deletion verification for the
  normal owned per-document acknowledgment, at the cost of an extra bounded
  cleanup pass. Eligible native local facts remain usable; purge is not a
  promise to erase canonical history.
  Proven native curator drafts, checkpoints and coverage remain intact for the
  next publication; an overlapping curator lease is revoked without
  acknowledging its unfinished work.
- **Summary/scene/curator Clear** resets the associated published and private
  derived layer state. Use the application lifecycle API, not SQL that removes
  only a visible artifact parent.

## Final request budgeting

Generation assembles authorized memory/scene sections and raw history with
character, persona, world, RAG and user inputs. It then appends route-specific
instructions and finalizes the actual request. Requested output, model context
metadata, token-estimate ratio and safety margin determine the input budget.
Old optional history and retrieved sections can be reduced; protected fixed
instructions, current user text and required continuation/image content either
fit or raise ContextWindowBudgetError before a provider dispatch/generated turn.

Scene context has its own allocation. Images remain present in admitted image
requests. Token counts are production heuristics, including heuristic image
costs; they are not tokenizer measurements or vision-quality scores.

Provider transports recheck effective routed, normalized, fallback/recovery and
automatic continuation attempts where those paths exist. Diagnostics record
the actual last attempted model/output reservation. Provider-specific output
limits still depend on provider behavior; Codex output reservation is local
admission and does not establish universal server enforcement.

Character knowledge exclusions in derived memory do not filter separately
supplied raw transcript, user text, card/persona/world/RAG inputs or images.
The evaluator inventories the captured final text request independently from
its memory-channel exclusion assertion. Image preservation makes no OCR,
visual-secrecy or universal information-flow claim.

## Reproduce the deterministic evaluation

From a checkout with the existing project environment:

```sh
python tools/evaluate_story_memory.py --repeats 10 --output story-memory-results.json
```

The runner also writes the same structured JSON to stdout. It can run from an
unrelated working directory via an absolute script path. It creates and removes
a temporary home/database, passes explicit AppSettings and does not import test
factories or application startup. Synthetic default Persona/World Info and
consumed NPC Persona seams are installed before session creation. Unconfigured
native/provider/Codex/socket access raises before I/O.

The versioned fixture is tests/fixtures/story_memory/v1.json. The primary story
uses 150 successful real generate_and_store_reply calls: 300 canonical messages,
300 distinct survey facts and two restricted temporal attestations. The early
compass source is unique. Distant recall requires more than 100 later accepted
messages and more than 200 later distinct native episodes relative to that exact
target, not merely a large global corpus.

External extraction returns a marker fact only when the complete marker is
present in the actual submitted source part. It respects the six-event response
cap. The fake Hindsight client stores only successful production retain payloads
under supplied deterministic IDs, replaces documents on retry, supports
deletion/listing and ranks existing retained native document IDs. It deliberately
does not implement local reader/as-of/branch authorization. SDK-shaped recall
responses include adversarial remote text and unmapped/observation candidates.

The long scroll puts markers after character 1,800, 74,000 and 150,000. The
synthetic context window is 65,536 tokens so this protected current user message
can be accepted through the ordinary route. Production workers then extract
complete bounded parts over multiple claims. Scheduler/lease and narrow rewrite
SQL mutations are labelled fixtures; they do not replace primary acceptance or
seed derived expected answers.

To prove the detector rejects source loss:

```sh
python tools/evaluate_story_memory.py --repeats 3 --inject-failure drop-tail
```

This intentionally removes the delivered tail marker at the fake extraction
transport. The source coverage may still be complete, but the required tail
fact must be absent, the long-source case fails and the CLI exits 1. Parser/CLI
errors also exit nonzero. This is a harness failure-control, not a production
configuration option.

## Coverage boundaries

CLI JSON counts only cases executed by the CLI. Pytest-only integration evidence
is separate, even when it covers the same architectural boundary.

| Requirement | CLI evidence | Additional direct production regressions |
| --- | --- | --- |
| Canonical acceptance/provenance/distance | acceptance.primary; distant.prerequisites; evidence.canonical-mapping; distant.direct | tests/test_story_memory_evaluation.py |
| Semantic aliases/adversarial text | semantic.alias; knowledge.mixed-closure | tests/test_story_memory_index.py |
| Temporal character grants/mixed source | knowledge.before; knowledge.at; knowledge.mira; knowledge.mixed-closure | tests/test_story_memory_scope.py; tests/test_story_memory_artifacts.py |
| Continue/numbered choice expansion | Not counted as CLI execution | tests/test_memory_indexed_retrieval.py (actual continue/numbered-choice query expansion) |
| Fork/remap and source branch mutation | Not counted as CLI execution | tests/test_story_memory_snapshot.py; alternate-ending checkpoint tests |
| Historical edit/regeneration and future derived context | rewrite.capture-append-and-edit is a narrow source fixture | tests/test_story_memory_routes.py; tests/test_story_memory_review_fixes.py |
| Delete/recreate incarnation | incarnation.delete-recreate | tests/test_story_memory_scope.py; tests/test_story_memory_retirement.py |
| Long head/middle/tail and bounded work | coverage.complete-long-source; transport.input-sensitivity | tests/test_memory_complete_parts.py |
| Native-only remote boundary and idle drain | facts-only.remote-boundary | tests/test_facts_only_hindsight.py; tests/test_facts_only_migration.py |
| Rejection/outage/lost ACK/lease/restart | retry.* | tests/test_durable_memory_workers.py; tests/test_story_memory_index.py |
| Raw crash/reopening, overlapping requests, DELETE/purge ACK and recorded-identity migration | These race/recovery assertions are pytest-only | tests/test_raw_retirement_recovery.py; tests/test_memory_final_integration.py |
| Same-session recurring-watch progress and finite cleanup failure/retry | Executed-worker assertions are pytest-only | tests/test_raw_retirement_fairness.py |
| Empty/malformed/stale acceptance | Marker-free sources/parts processed in later CLI scenarios; explicit empty/malformed/stale assertions are pytest-only | tests/test_story_memory_index.py; tests/test_memory_complete_parts.py; tests/test_memory_final_integration.py |
| Purge/native facts | purge.floor | tests/test_story_memory_scope.py; tests/test_memory_final_integration.py (native curator continuation and raw late retirement) |
| Five route final payloads, appended contracts/images/scene | budget.accepted-final-request; budget.protected-overflow (ordinary accepted route) | tests/test_final_generation_budget.py; tests/test_final_generation_budget.py |
| Actual normalized/fallback/recovery/continuation attempts | Not counted as CLI execution | tests/test_provider_attempt_budget.py; tests/test_provider_attempt_budget.py |
| Derived complete publication, Clear and NPC replay | Not counted as CLI execution | tests/test_memory_complete_parts.py; tests/test_memory_stage3_lifecycle.py |

The suite gate runs the evaluator once for normal standalone success and once
for the deliberate tail-loss failure. It tests JSON equality/serialization,
cleanup, guard behavior, identity/span mapping and bounded CLI arguments. No
separate duplicated long-story CI job is added: existing pytest runs these tests.
Complete-suite coverage, strict types, lint, format and direction checks remain
the normal CI gates.

## Measurement methodology and recorded observations

Measurements use perf_counter_ns. Setup includes guard/service/database/session
construction, ingestion includes production final assembly and acceptance of
150 turns, and primary drain covers complete local episode extraction plus
native-fact Hindsight indexing. Query samples use the full MemoryService.prompt_context
path: one excluded warm-up followed by the requested repetitions. Median is the
sample median; p95 uses nearest rank. Default 10 samples remain a small offline
observation, not a population latency estimate.

tracemalloc measures peak traced Python heap during the whole evaluation.
It does not measure process RSS, SQLite/native allocation, OS page cache or idle
production service memory. Process RSS is explicitly unmeasured. SQLite page
bytes are page_count times page_size; WAL bytes are observed before cleanup.
Stub counts and JSON UTF-8 payload bytes measure this fake adapter boundary,
including metadata. They are not real wire billing/token usage.

The report records fixture version/hash, git revision, Python/SQLite versions,
synthetic settings, stable IDs, per-case assertions, counts and metrics. Its
source inventory hashes all three evaluator modules: evaluate_story_memory.py,
story_memory_eval_support.py and story_memory_eval_checks.py. Bounded retained
payload observations identify native summaries, document IDs and generation tags.
Timings, heap and WAL sizes do not gate correctness; deterministic contracts
and work/request bounds do. There is no fabricated old/new improvement score.

The saved facts-only observations and identity are in
story-memory-evaluation-results.json, using report schema version 2. See its
measurement_identity and measurement_boundary fields before comparing runs. Development attempts that
failed are documented in the implementation report, not combined with the
successful measured run.

## Recorded run (previous checkpoint source)

The saved JSON remains the checkpoint 3 measurement of source c5bf5ac7583b5139ed7be0b9b00b8d7f6b5089d7.
Checkpoint 4 changes foreground transport ownership and explicitly injects the
evaluator's synthetic identity adapter. The saved source hashes and timings do
not describe checkpoint 4; no new performance or semantic-quality measurement
is claimed here. Reproduce the evaluator on a clean selected revision for a new
comparison. The original artifact and its measurement identity are preserved.

The saved standalone invocation used 10 measured queries and one excluded
warm-up, launched directly from an unrelated empty working directory after the
facts-only source was published. It passed **24 of 24 executed cases and all 82
assertions**. The JSON records the clean measured source revision
c5bf5ac7583b5139ed7be0b9b00b8d7f6b5089d7, an empty git status, and SHA-256 of
all three evaluator source files. Its stdout and saved JSON were byte-for-byte
equal. The separate result/documentation commit records the measurement of that
exact source revision; production, tests and evaluator source stayed frozen
throughout the invocation.
Python was 3.12.14; SQLite was 3.53.1. Fixture SHA-256 was
6bd668c68d946775991fdda3890a9c839a125c75a7139df3b92a7118ce810d6c.
The local full pytest suite was running concurrently on this host, which is part
of the conditions behind the observed timings.

| Observation | Measured value |
| --- | ---: |
| Primary canonical messages | 300 |
| Distinct accepted native episodes | 302 |
| Messages later than the unique target | 295 |
| Distinct episodes later than the unique target | 297 |
| Setup | 320.569 ms |
| Production turn ingestion | 5,811.434 ms |
| Primary local episode/native index drain | 1,733.267 ms |
| Query median, 10 samples | 13.389 ms |
| Query nearest-rank p95, 10 samples | 14.614 ms |
| Peak traced Python heap, whole evaluation | 41,003,835 bytes |
| SQLite pages before purge/deletion | 1,863,680 bytes |
| SQLite WAL at that observation | 0 bytes |
| Stub requests, all scenarios | 1,567 |
| Largest serialized stub request | 165,541 bytes |
| Provider / retain / recall / delete calls | 470 / 307 / 174 / 616 |
| Native-only retain payload observations | 307 |
| Largest delivered extraction source part | 12,000 characters |
| Largest observed worker external-call count | 8 |
| Unexpected unconfigured I/O attempts | 0 |

All 307 retain attempts carried native-fact tags and exact summaries accepted
locally for their document IDs. This includes retry attempts; the count does not
mean 307 distinct successfully retained facts. No new raw source segment or raw
mapping was created. The facts-only case also confirmed full local coverage,
distant and alias recall, no pending native indexes and an idle extra drain.

All 150,054 characters of the long source were covered by 13 contiguous local
source parts. Its head, middle and tail facts were present. Later CLI scenarios
also processed marker-free assistant sources and source parts without inventing
facts. The pytest gate on this same evaluator source ran the drop-tail
control and confirmed CLI exit 1 with only coverage.complete-long-source failing.
That fault-injected report is separate from the saved successful invocation.

The historical artifact at source 24a21e68f1c4426550364177f042558803702ecc
recorded 623 retain attempts and 1,892 total stub requests. The checkpoint 3 counts
are 307 and 1,567. These are observations at the scripted adapter boundary;
changes to indexing and asynchronous cleanup affect the request mix. The source,
report schema and Python environment differ, so the timing values do not support
a baseline/candidate speedup claim. No real provider cost or semantic-quality
change is measured.

The mixed-secret historical memory channel excluded the phrase. A separately
captured earlier ordinary generation request contained that phrase once in raw
history, with zero occurrences in its other prompt inputs. Thus this run
demonstrates a real limitation: derived memory authorization cannot establish
secrecy for an independently supplied canonical transcript.

This ordinary scripted workload does not measure the accumulating metadata or
lifetime cleanup traffic of indefinite ambiguous/legacy watches. The separate
pytest regressions exercise those recovery and ownership contracts; their
assertions are not added to the CLI's 24-case score.

These observations describe this run's synthetic workload, host conditions and
traced process. They are not a
baseline/candidate speedup, a stable host capacity estimate, real provider
latency, whole-process RSS or idle production resource measurements.

## Optional real-provider comparison protocol

This evaluator never calls real providers. A separate explicitly opted-in study
could measure answer behavior, using fixed synthetic stories only:

1. Freeze baseline/candidate revisions, identical accepted stories, fixture
   truth, model/provider/version, generation settings and query boundaries.
   Record all input surfaces that independently expose forbidden facts.
2. Run a fixed panel of at most 12 paired queries per story/model, with 3
   repetitions (36 answers per revision/model). Disable free-form retries.
   Set explicit output/request-time/cost caps before dispatch. Do not include
   live user stories or secrets.
3. Score exact required facts, forbidden facts and correct usable citations
   independently from blinded prose ratings. Save complete synthetic inputs,
   provider outputs, cost/usage and error denominators.
4. Report paired differences and uncertainty intervals over story/query units,
   including failed requests. More repetitions on one story do not establish
   general performance.
5. Keep real provider_answer_fact_accuracy and provider_answer_forbidden_fact_rate
   separate from deterministic_contract_pass_rate. Scripted ranking and
   classifications cannot serve as real-model scores.

No real-provider comparison, answer-quality claim or deployment is part of
the offline evaluation.

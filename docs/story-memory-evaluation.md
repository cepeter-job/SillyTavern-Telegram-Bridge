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
| External transcript ingestion | Replace a latest-100-message snapshot | Bounded complete source parts with deterministic IDs, immutable digest/offset provenance and retry fences |
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
applied migration history through migration 23. Forward migration 24 adds the
raw archival attempt ledger and document/due-work indexes. Opening an isolated
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

Worker admission is bounded to one active memory claim. Extraction, raw
archival and native index work use the existing eight-source/external-call
allowance per run; native fact indexes reserve part of the Hindsight allowance.
Retirement cleanup has a separate batch of at most 16 document deletions, and
raw recovery inspects at most 16 due attempt rows per invocation. Eight is not
a combined ingestion-plus-cleanup API-call bound. Claims carry lease ownership,
accepted target, incarnation, rewrite identity and purge epoch. Failure uses
bounded retry delay rather than busy looping. Pending jobs and private drafts
survive process restart.

An expired lease can be recovered; an active lease must not be stolen. Startup
recovery assumes one owning application runtime. External retain can succeed
before a local acknowledgment is lost. Retrying the same deterministic document
ID replaces the external document and commits a current local acknowledgment.
It must not re-extract a still-valid native fact merely to repeat its index call.

Each actual raw retain gets a unique durable attempt token in the same short
transaction as its source reservation, before the remote call. A pending raw
part uses segment validity 2, while accepted source/offset readers require
validity 1; reservation does not advance successful coverage. Current failed
attempts reuse the deterministic document ID, including after an unrelated
suffix rewrite, but each dispatch owns a separate token.

A positive synchronous retain result is committed as known completion before
the source acceptance or stale-retirement transaction. Successful source,
coverage, mapping and owned-token resolution remain atomic after the existing
source, claim, mode and incarnation checks. Failed reopening or process loss
therefore leaves an independently discoverable obligation. Completion of a
successor cannot remove an older request's token. A timeout, failed transport,
expired lease or lost process is not proof that the upstream request stopped.

Startup/ordinary dispatch, manual seed and direct cleanup reconcile bounded
due batches without requiring a surviving session, job or pending source.
Current accepted/pending exact-incarnation sources remain protected. Stale or
orphan attempts reopen retirement even after an earlier successful deletion.
Known completion can be reclaimed atomically with that reconciliation.

Before each remote DELETE, cleanup records whether any raw attempt was
outstanding. Its local acknowledgment may finish retirement only when none was
outstanding at DELETE start, none remains at acknowledgment, and the cleanup
lease still belongs to that caller. This prevents an older DELETE response from
erasing a later retain/reopening. Unfinished watches use a 300-second retry
interval; the dispatcher gives a due ordinary claim a turn between
recurring-watch batches. The actual Hindsight worker uses a finite-cleanup
prerequisite: both its selected batch and its remaining-debt query exclude
unfinished raw watches. A failed finite due deletion still fails the claim and
keeps the existing retry delay, mode/current-source checks and lease protection.
Earlier watched IDs or their failures cannot consume that prerequisite batch.

Dedicated retirement workers and direct cleanup still service all due debt.
The ordinary turn can therefore advance native indexing and current raw
coverage without waiting for every recurring watch to become simultaneously
non-due. The same-session regression executes the submitted worker and checks
accepted source/mapping/coverage, native indexing, remote objects and job
acknowledgment while later retirement turns continue the durable watches.

Ordinary successful new attempts add an intent INSERT in the reservation
transaction, one short known-completion commit and an owned-token DELETE in
finalization. They leave no recurring watch unless an older or legacy
uncertainty exists. Ambiguous or legacy attempts have no automatic expiry:
per-poll work is bounded, but cumulative metadata and lifetime deletion traffic
are not hard-bounded. Arbitrarily delayed upstream writes can recreate an old
object between cleanup opportunities. Continued removal requires enabled
memory, future cleanup opportunities and a functioning provider; local source
authority remains denied throughout.

Edits, regeneration, deletes and moves invalidate affected suffix authority and
journal source rewrites. An unrelated later mutation preserves accepted earlier
prefixes; a harmless append preserves captured unchanged source parts. Historical
requests exclude discarded outcomes and future classified scene/summary/NPC
context. Summary/scene/curator Clear also retires private progress, so stale drafts
cannot recreate cleared artifacts. NPC replay preserves valid history before the
actual invalidation boundary and supplies extraction only with preceding state.

Branch/checkpoint restore copies canonical rows, remaps source parts/explicit
anchors, rebuilds target digests and incarnation, and queues target indexing.
Origin document acknowledgments are not target authority. A later source-branch
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
- **/memory off** blocks external work and recall without destroying eligible
  local native facts. Pending work remains recoverable. **/memory on** makes
  pending work due again.
- **External purge** advances epoch/floor, retires external index authority and
  deletes session-attributable documents with verification/retry. Automatic
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
150 turns, and primary drain covers episode extraction plus Hindsight indexing/
raw archival completion. Query samples use the full MemoryService.prompt_context
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
synthetic settings, stable IDs, per-case assertions, counts and metrics.
Timings, heap and WAL sizes do not gate correctness; deterministic contracts
and work/request bounds do. There is no fabricated old/new improvement score.

The final recorded observations and identity are in
story-memory-evaluation-results.json. See its measurement_identity and
measurement_boundary fields before comparing runs. Development attempts that
failed are documented in the implementation report, not combined with the
successful measured run.

## Recorded run

The saved standalone invocation used 10 measured queries and one excluded
warm-up, launched directly from an unrelated empty working directory after the
same-session worker fairness correction was committed. It passed **23 of 23
executed cases and all 73 assertions**. The JSON records the clean measured
source revision 24a21e68f1c4426550364177f042558803702ecc, an empty git status,
and SHA-256 of both evaluator source files. The separate result/documentation
commit records the measurement of that exact source revision. Earlier afaebaf
and b19da007 measurements remain historical; source and tests stayed frozen
during this refresh.
Python was 3.11.16; SQLite was 3.53.1. Fixture SHA-256 was
6bd668c68d946775991fdda3890a9c839a125c75a7139df3b92a7118ce810d6c.

| Observation | Measured value |
| --- | ---: |
| Primary canonical messages | 300 |
| Distinct accepted native episodes | 302 |
| Messages later than the unique target | 295 |
| Distinct episodes later than the unique target | 297 |
| Setup | 2,714.069 ms |
| Production turn ingestion | 16,024.291 ms |
| Primary episode/index/archive drain | 57,127.064 ms |
| Query median, 10 samples | 43.945 ms |
| Query nearest-rank p95, 10 samples | 57.300 ms |
| Peak traced Python heap, whole evaluation | 40,875,817 bytes |
| SQLite pages before purge/deletion | 2,019,328 bytes |
| SQLite WAL at that observation | 0 bytes |
| Stub requests, all scenarios | 1,892 |
| Largest serialized stub request | 164,799 bytes |
| Provider / retain / recall / delete calls | 470 / 623 / 174 / 625 |
| Largest delivered extraction source part | 12,000 characters |
| Largest observed worker external-call count | 8 |
| Unexpected unconfigured I/O attempts | 0 |

All 150,054 characters of the long source were covered by 13 contiguous
source parts. Its head, middle and tail facts were present. Later CLI scenarios
also processed marker-free assistant sources and source parts without inventing
facts. The historical injected control, recorded before the final integration
fix, kept complete source coverage but removed the tail fact; its CLI exited 1
with only coverage.complete-long-source failing. This artifact refresh did not
rerun that control. The full CI regression executes the nonzero detector again.

The mixed-secret historical memory channel excluded the phrase. A separately
captured earlier ordinary generation request contained that phrase once in raw
history, with zero occurrences in its other prompt inputs. Thus this run
demonstrates a real limitation: derived memory authorization cannot establish
secrecy for an independently supplied canonical transcript.

This ordinary scripted workload does not measure the accumulating metadata or
lifetime cleanup traffic of indefinite ambiguous/legacy watches. The separate
pytest regressions exercise those recovery and ownership contracts; their
assertions are not added to the CLI's 23-case score.

Device disk waits varied between development attempts. These observations
describe this run's synthetic workload and traced process. They are not a
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

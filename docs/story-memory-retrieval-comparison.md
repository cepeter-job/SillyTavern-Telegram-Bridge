# Reproducible story-memory retrieval comparison

`tools/compare_story_memory_retrieval.py` compares the production SQLite FTS5
candidate path, an evaluation-only exact cosine scan over float32 SQLite BLOBs,
and an explicitly enabled Hindsight HTTP study. It creates an isolated temporary
application home and synthetic story. It does not migrate the production memory
backend, deploy a service, or read an existing story database.

## Checkpoint status

Task 5 implementation and measured artifacts are complete. Integration and
required checks are tracked in
[PR409](https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/409).

The measurements below used clean source
[`caea8e7fb05d60e98981c53c3e598798cf4559d4`](https://github.com/cepeter/SillyTavern-Telegram-Bridge/commit/caea8e7fb05d60e98981c53c3e598798cf4559d4),
tree `440d5df930fac336a39a0186aa3e612e1f7db32d`. Their source SHA256 is
`1e7bf65f878ab8cc97719b55949966eff7a76ca9f2884f704e2b6558decfe6e4`.
A later documentation or merge commit is not the measured revision.

CP4 merged in PR406 as `b9b11ebca5a0e7fc162ce3e4f5a0844df22a9b05`.
Production continues to use facts-only Hindsight as a semantic supplement to FTS.
See [the architecture and contract evaluation](story-memory-evaluation.md) for
runtime operation. This checkpoint introduces no production backend migration.
Related [issue410](https://github.com/cepeter/SillyTavern-Telegram-Bridge/issues/410)
item 4 tracks this retrieval work through PR409. Answer quality from real generation
and Telegram Android/iOS acceptance remain outside this study.

## Observed results: 7 October 2026

The default offline baseline completed with zero HTTP. One explicitly bounded
live study acquired all genuine embeddings, then failed during Hindsight's first
retain attempt. A subsequent offline replay completed with zero HTTP using that
saved embedding cache. All three reports preserve their own status and authority
bindings.

### Preserved artifacts

Every measurement artifact below is a byte-for-byte copy of the original file.
The derived [artifact index](story-memory-retrieval-comparison-artifact-index.json)
records the archive mapping, byte lengths and SHA256 hashes. Original paths,
source identity, timestamps and historical receipt fields remain intact.

| Artifact | Contents |
| --- | --- |
| [Offline baseline](story-memory-retrieval-comparison-results.json) | Completed default run; zero HTTP; FTS results and contract-vector checks. |
| [Live results](story-memory-retrieval-comparison-live-results.json) | Failed overall study; available FTS/genuine BLOB results and explicit Hindsight failures. |
| [Genuine embedding cache](story-memory-retrieval-comparison-embeddings.json) | All 66 ordered inputs, 2048-dimensional vectors, profile, hashes and acquisition provenance. |
| [Offline cache replay](story-memory-retrieval-comparison-replay-results.json) | Completed zero-HTTP reproduction against fresh local authority. |
| [Owned-bank manifest](story-memory-retrieval-comparison-owned-bank.json) | Actual generated bank identity, attempted mutation and unresolved cleanup obligation. |
| [Live invocation](story-memory-retrieval-comparison-live-invocation.json) | Exact arguments and pre-dispatch source/configuration identity; credential variable name only. |
| [Live completion](story-memory-retrieval-comparison-live-completion.json) | Exit 1, 218.259s process interval and original output hashes. |
| [Live process log](story-memory-retrieval-comparison-live-process.log) | Sanitized terminal study status. |

The invocation's `status=running` records the start snapshot. The completion
record supplies the terminal `study_failed` status; the measurement processes
have finished. Successful cache replay does not change the live result or its
cleanup obligation.

### Retrieval quality and candidate policy

FTS and genuine BLOB each completed all 24 cases: 22 positive cases and two
no-answer cases. The live and replay runs have identical ordered canonical
candidate/admitted lists, selected sets, quality, category summaries and
denominators. FTS quality also matches the baseline.

| Component | Macro Recall@1 | Macro Recall@3 | Macro Recall@6 | MRR | No-answer cases returning candidates |
| --- | ---: | ---: | ---: | ---: | ---: |
| Production FTS | 56.818% | 72.727% | 72.727% | 0.696970 | 0/2 |
| Genuine cached BLOB | 88.636% | 100% | 100% | 1.000000 | 2/2 |

BLOB reaches the panel's 88.636% Recall@1 ceiling because five positive cases
require two facts. On jointly successful cases, its gains over FTS are
31.818 percentage points at Recall@1, 27.273 points at Recall@3/6, and 0.303030
MRR. The production-selected FTS-only set has Recall@6=72.727%; FTS+BLOB has
Recall@6=100%, a 27.273-point gain. Selected sets have no global MRR.

FTS retrieves all six direct lexical cases. Its six fixed paraphrases Q07–Q12
produce zero candidates; BLOB places a relevant fact first for all six. Both
paths have full Recall@3/6 on the other ten positive scope/branch cases. On Q16,
the relevant fact moves from FTS rank 3 to BLOB rank 1. Full declared categories,
including failures and expected/successful denominators, remain in the reports.

Across one observation of each of the 24 cases, FTS has 34 raw/candidate/admitted
occurrences and BLOB has 456; repeated appearances across queries are counted
separately. Neither path has late invalidations or postfilter-starved cases.
FTS has zero raw candidates on Q07–Q12 and the two no-answer cases.
BLOB scores the entire eligible pool of 13–21 facts for every case.

The BLOB adapter has no abstention threshold. It admits 20 and 19 eligible
candidates on the two no-answer cases; production selection retains six in each.
That is a measured candidate-return tradeoff, not an answer-generation or
hallucination result. The small panel, repeated texts, uniform importance and
narrow alternate-branch coverage limit generalization. These results do not
establish a production migration decision.

All reports pass the authority invariants: zero forbidden admissions, no missing
or stale vectors, at most six production-selected facts and unchanged measured
source. Hand-authored contract vectors continue to omit semantic-quality
aggregates at both overall and category levels.

### Local timing and embedding acquisition

These are local component or selected-pipeline timings with tracing active,
one excluded warm-up per query and ten measured repetitions: 240 samples per
path. Values are milliseconds; p95 uses the nearest-rank definition. Cached
BLOB measurements exclude the earlier embedding inference.

| Local path | Live-run median / p95 | Offline-replay median / p95 |
| --- | ---: | ---: |
| FTS component, including final validation | 5.993 / 13.928 | 5.839 / 14.016 |
| Genuine BLOB component, including final validation | 142.395 / 156.354 | 148.309 / 159.101 |
| FTS-only production selection | 6.664 / 13.214 | 6.457 / 12.666 |
| FTS+BLOB cached production pipeline | 156.309 / 173.384 | 162.857 / 178.190 |

The baseline FTS component median/p95 is 5.416/14.442ms; its FTS-only selected
pipeline is 6.375/13.295ms. Repeated cases from one fixed panel are not independent
service-load observations or an SLA estimate.

Genuine acquisition completed 66/66 inputs in five batches of 16/16/16/16/2.
Its separately recorded acquisition interval was 15.264131548s, including local
handling. Individual HTTP attempts ranged from 2.602s to 3.241s. Batch three mixes
facts and queries. No isolated uncached query latency, provider cost estimate or
1.5-second foreground projection can be derived by amortizing those batches.

The profile is `yuyu-embedding`, 2048 dimensions, with operator-assigned revision
`vm148-config-20261007`; the relay endpoint is
`http://127.0.0.1:8891/v1/embeddings`. This is external-provider inference.
The complete cache SHA256 is
`732173d9d49c04d76028b8810a34b12a1895783e6c2a2a5b3607b95cbb1fc52b`.
Replay reuses those same vectors while rebuilding all 42 fact bindings to fresh session
incarnations and evidence from production lifecycle operations. It verifies local
determinism and fresh-authority rebinding; it is not a second model replication.
The cache's old acquisition receipts remain provenance, while replay records
`embedding_acquisition={}` and zero new HTTP calls.

Measured runtime: CPython 3.11.16, SQLite 3.53.1, aiohttp 3.14.3, Linux x86_64.
The installed Hindsight SDK was 0.10.0 and unused; the study targets the verified
public HTTP 0.10.2 contract. The live report records 344,400 vector payload bytes
(344,064 genuine document-vector bytes plus 336 contract bytes), 389,120 logical
temporary-SQLite page bytes and a 35,717,167-byte traced Python peak. Those scopes
are distinct from RSS and running Hindsight service memory.

### Hindsight failure and remaining cleanup

The single live run made 11 HTTP attempts: five successful embedding calls and
six Hindsight calls. For Hindsight, the sequence was absence GET 404, explicit
creation PUT 200, configuration GET 200 verifying observations disabled, first
retain POST timeout, cleanup DELETE 200, and cleanup verification GET 404.

The manifest binds 42 planned canonical documents. Only the first 12-document
batch was attempted; it timed out after an observed 10.015255122s under the
configured 10-second deadline. There are zero acknowledged retained batches and
zero recall attempts. All 24 Hindsight cases are failed
`ingestion_or_setup_incomplete` cases with `attempted=false`: 22 positive and
two no-answer cases, with zero successful cases. Hindsight quality and service
recall latency are unmeasured. The report's tiny failed-row
`canonical_finalize`/`observed_component_total` samples time local empty
finalization; they are not Hindsight recall performance.

Creation was confirmed, but retain outcome remains uncertain. DELETE 200 followed
by one GET 404 cannot prove that the timed-out synchronous retain has finished.
The archived manifest therefore retains `uncertain_ingestion=true` and
`cleanup_pending=true`, and the live process exited 1. The generated bank identity
in that manifest remains the concrete cleanup obligation for operator
investigation. No further live calls or retries were made. Offline replay skips
Hindsight and cannot resolve that obligation.

## Fixed corpus and judgments

The versioned inputs are
`tests/fixtures/story_memory/retrieval_v1.json` and
`tests/fixtures/story_memory/retrieval_vectors_v1.json`. The first fixes all texts,
scopes, relevance judgments and embedding slots before inference. The second is
explicitly `contract_only` and has no semantic-quality aggregates.

| Quantity | Fixed value |
| --- | ---: |
| Canonical documents | 42 |
| Distinct summaries | 26 |
| Query cases / distinct query texts | 24 / 20 |
| Positive cases / relevance annotations | 22 / 27 |
| Cases requiring two relevant facts | 5 |
| No-answer cases | 2 |
| Expected authorized pool per case | 13–21 |
| Ordered embedding slots | 66 |

The main story has sixteen prefix facts and six later facts. A real alternate
ending copies the sixteen-fact prefix and adds four branch facts. Prefix
acceptance is M01–M12, M16, M13, M14, M15; document embedding order is M01–M22,
A01–A16, A23–A26, then Q01–Q24. Every fact has importance 0.9 and a short summary.
The alternate branch has narrow positive coverage; this small panel cannot
establish general performance over long stories or other genres.

Facts come from actual accepted turns, production source segmentation and the
episodic extraction worker. The scripted provider acknowledgment remains exactly
`Rowan records the observation.` Production transport normalization determines
its stored form. The harness checks that result and records actual row IDs and
hashes. If the production post-retain hook reconciles a finale before acceptance
returns, the scripted provider identifies the exact committed resolution assistant
row in the supplied transcript payload. It uses that observed identity immediately;
no predicted offset or later caller-side stage change grants resolution. Marker-free
reconciliation packets drive the real checkpoint, ending, epilogue and delivery bookkeeping before `create_alternate_ending` executes.
There are no direct derived-fact or provenance inserts. The clone records actual
source remapping and new session incarnation; local memory readiness is `ready`.
Historical cutoffs use captured assistant row IDs, never arithmetic offsets.

## What is compared

FTS calls production `search_fact_ids`, including its token builder and explicit
assertion fallback. It preserves BM25, importance and descending memory-ID tie
order. The lexical cap is 48 and explicit fallback can add at most six IDs; the
fixed panel contains zero explicit assertions. No synonyms or stemming are added.

The BLOB path inventories **every current eligible fact** before looking up its
vector. It has no latest-200 cap or lexical prefilter. A temporary SQLite table
stores normalized little-endian float32 vectors, dimensions, profile, canonical
payload hash, current authority fingerprint and norm. It rejects invalid lengths,
zero/nonfinite vectors, mixed profiles, changed payloads and stale authority.
Cosine uses all eligible, matching rows; ties use ascending canonical memory ID.
Every component rechecks current scope and evidence immediately before producing
canonical summaries. Vectors and remote enrichment text cannot grant authority.

Hindsight sends strict study/session/native-fact tags and world/experience types.
It ranks a session-filtered remote pool before local audience and historical
postfilters. FTS and BLOB prefilter those constraints. Differences therefore
compare these pipelines, not only their models. Admission applies production's
first **64 raw records before filtering or deduplication**. At most 256 raw records
are accepted for bounded ID-only diagnostics; overflow beyond 64 is labeled and
cannot fill a vacancy caused by a private or invalid earlier result. Returned tags
must be an array of strings. Reports retain raw, candidate, admitted, truncation
and starvation counts. Remote prose never enters output context.

The harness separately reports production-selected sets for FTS-only,
FTS+Hindsight and FTS+BLOB. It calls `read_episodic_block`, `ranked_fact_block` for
verified Hindsight document identities, and `validate_memory_blocks` in the
merged production order: **semantic/recall, episodic, summary, scene**. Summary and
scene inputs are empty for this controlled comparison. Hindsight index readiness
is acknowledged through the actual production worker only after complete,
verified synchronous retain receipts. BLOB uses an evaluation-only local channel
with empty external document IDs. Output retains channel membership and the
canonical rendered blocks; its sorted display IDs are not a global ranking.

## Metrics and measurement boundaries

Components with curated FTS or genuine model evidence report macro Recall@1,
Recall@3, Recall@6 and MRR over successful positive cases. Recall divides by the
number of relevant facts in each case, then averages cases. With five two-fact
queries, the panel's true macro Recall@1 ceiling is **88.636%**, not 100%.
The two no-answer cases report candidate-return rate; they do not measure
hallucination. Failed/skipped query IDs and expected/successful denominators are
explicit. Each observation retains its predeclared category, and category summaries
separately report successful, failed and skipped denominators plus applicable
component or selected-set quality. Paired differences use only jointly successful
IDs. Contract vectors omit quality aggregate fields both overall and within every
category, even when their adapter succeeds.

Production-selected sets report Recall@6, selected/relevant counts and forbidden
counts, with no global fused MRR. Zero forbidden evidence, complete current vector
coverage, the six-fact selection bound and unchanged source are invariant gates.
No historical 85% quality claim or 2 ms latency claim is used as an acceptance
threshold. CLI success means the report and invariants pass; default optional
skips are allowed. A failed enabled backend or unresolved cleanup is nonzero.

Offline paths use `perf_counter_ns`, one excluded warm-up per query and ten
repetitions by default. Reports preserve samples and nearest-rank p95, with
eligibility, decoding, scoring, final validation and pipeline boundaries. Python
allocation tracing remains active during these timings. Cached BLOB latency
excludes previously acquired embeddings and is labeled accordingly.

The five embedding batches are **16/16/16/16/2**, including both facts and queries
in batch three. Acquisition has separate elapsed time, batch identities and failed
input denominators. Dividing these totals by 24 is not an uncached per-query
latency estimate and cannot support a 1,500 ms foreground projection.

Hindsight has one study attempt per fixed query, at most 24. Its 10-second HTTP
study timeout differs from production's 1.5-second caller budget. The report shows
successful HTTP calls completed within 1.5 seconds, alongside failures and all
24 expected cases. This narrow count excludes local validation, scheduling,
in-flight admission and the production 30-second cooldown. It does not reconstruct
foreground behavior or run extra calls. Twenty-four fixed cases are a small
observed latency sample, not a service-level estimate.

Source revision/tree, dirty status, source/fixture/cache hashes, Python, SQLite,
platform, model profile and transport version are recorded. SQLite logical page
bytes, vector payload bytes and database/WAL/SHM file sizes are distinct from
traced Python heap. Neither is RSS or proof that a running Hindsight service
released memory.

## Offline reproduction

Use the repository's existing locked environment. Run from the repository root:

```bash
MYPY_CACHE_DIR=/dev/null .venv/bin/python -X dev -W error::ResourceWarning \
  tools/compare_story_memory_retrieval.py \
  --output /tmp/story-memory-retrieval-offline.json
```

The default performs zero HTTP, leaves native/provider guards installed, and
reports FTS plus BLOB contract checks; genuine BLOB and Hindsight are `skipped`
with `capability_or_budget_unverified`. `--backends fts` selects a smaller run.
`--require-backends blob,hindsight` makes their absence nonzero. `--repeats 1` is
useful for a schema test, not the final default-repetition artifact. Dirty-source
runs are labeled `development_unfrozen`; publish measurements from a clean,
committed checkout.

Replay a previously acquired complete cache without HTTP:

```bash
MYPY_CACHE_DIR=/dev/null .venv/bin/python -X dev -W error::ResourceWarning \
  tools/compare_story_memory_retrieval.py \
  --embedding-artifact docs/story-memory-retrieval-comparison-embeddings.json \
  --require-backends fts,blob \
  --output /tmp/story-memory-retrieval-cached.json
```

Caches use normalized float32 little-endian base64 and contain all 66 ordered
slots, text hashes, source/query bindings and embedding profile. Loading verifies
completeness and identity, then rebinds vectors to the freshly generated canonical
facts. Old session incarnations and document IDs never become current authority.
A `contract_only` cache cannot be upgraded with a CLI label.

## Explicit bounded live study

Run only after reviewing the frozen source, caps and isolation. The verified VM
has Hindsight HTTP API 0.10.2; its deployed Python 3.11.16 interpreter reports SDK
0.10.0 and aiohttp 3.14.3. This study uses the public HTTP API and **does not use
the SDK**. It does not install or upgrade dependencies. The loopback embedding
relay invokes an external provider and is not free local inference.

The operator supplies `ST_MEMORY_STUDY_EMBEDDING_TOKEN` in the process environment
from the already authorized service configuration without printing its value.
Run the following from the isolated, frozen VM source checkout, using the existing
verified interpreter; this command has no credential values or arbitrary bank ID:

```bash
MYPY_CACHE_DIR=/dev/null \
  /home/punzme/sillytavern-telegram-bridge/.venv/bin/python \
  -X dev -W error::ResourceWarning tools/compare_story_memory_retrieval.py \
  --require-backends fts,blob,hindsight \
  --enable-live-embeddings \
  --embedding-endpoint http://127.0.0.1:8891/v1/embeddings \
  --embedding-model yuyu-embedding --embedding-dimensions 2048 \
  --embedding-revision vm148-config-20261007 \
  --embedding-request-budget 5 \
  --embedding-token-env ST_MEMORY_STUDY_EMBEDDING_TOKEN \
  --save-embedding-artifact /tmp/story-memory-retrieval-embeddings.json \
  --enable-live-hindsight --hindsight-endpoint http://127.0.0.1:8890 \
  --hindsight-request-budget 33 \
  --owned-bank-manifest /tmp/story-memory-retrieval-owned-bank.json \
  --output /tmp/story-memory-retrieval-live.json
```

The profile revision is an operator-assigned configuration label, not an asserted
provider model revision. Endpoint, model, dimension, revision and source hashes
pin the observation. Cache and manifest paths must be new and distinct. A valid
complete cache is saved before Hindsight runs and survives a later failure.

| Boundary | Enforced cap |
| --- | --- |
| Embedding inputs | 66 fixed; hard maximum 72, 1,000 characters each, 72,000 total |
| Embedding calls | 5 attempts, batch 16, 10 s absolute request, 120 s total |
| Hindsight documents | 42 fixed; hard maximum 48, 48,000 characters total |
| Retains | 4 synchronous batches, 12/12/12/6, no retry |
| Recall | 24 attempts, `budget=low`, `max_tokens=1024`, concurrency one |
| Hindsight calls | Explicit 33–38 total; 33 suffice for the successful path |
| Hindsight time | 10 s absolute request, 300 s total, 20 s reserved for cleanup |
| Readiness | At most four reserved reads; currently zero polling calls |
| Cleanup | At most three allowed calls; currently one DELETE and one verification GET |

The aiohttp transport bounds headers and streamed bodies with an absolute
deadline, disables proxy inheritance, redirects and automatic retries, caps
responses at 8 MiB, and uses cancellable/reaped DNS resolution. A budgeted attempt
does not create an abandoned retry thread. Frontend caps do not exactly bound
Hindsight's internal LLM/embedding requests or provider charges. Token counters,
when returned, are partial observations rather than a complete cost statement.

An atomic ownership manifest is saved **before creation**. The temporary file is
fsynced before replacement, and the parent directory is fsynced after replacement,
before any creation or retain request can dispatch. A directory-sync failure stops
dispatch. The generated bank ID uses `story-memory-eval-`, a random UUID and the fixture hash. The harness checks
that exact bank's `/config` returns 404, explicitly creates it with
`enable_observations: false`, and verifies the resolved setting before ingestion.
It never lists or adopts another bank. Retains include only canonical summaries;
no raw story, reflective answer generation, scene, curator or mental-model input
is sent.

Cleanup deletes only that owned ID and checks its dedicated config endpoint.
An ambiguous creation or synchronous retain can still be running after a timeout
or disconnect. **DELETE plus one observed 404 does not settle that uncertainty.**
The manifest retains `uncertain_creation`/`uncertain_ingestion` and
`cleanup_pending=true`, and the command exits nonzero. Do not rerun it with that
manifest or add unbounded retries. The saved sanitized ownership record is the
remaining cleanup obligation for the operator to investigate.

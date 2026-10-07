# Reproducible story-memory retrieval comparison

`tools/compare_story_memory_retrieval.py` compares the production SQLite FTS5
candidate path, an evaluation-only exact cosine scan over float32 SQLite BLOBs,
and an explicitly enabled Hindsight HTTP study. It creates an isolated temporary
application home and synthetic story. It does not migrate the production memory
backend, deploy a service, or read an existing story database.

## Checkpoint status

The source, fixtures, transport contracts and report schema are implemented.
The source is under review in
[PR409](https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/409).
**Frozen empirical results are pending corrected-source review and CI.**
Test-server responses and hand-authored vectors are contract fixtures; they are not
evidence of model retrieval quality. The follow-up measured artifact
will be `docs/story-memory-retrieval-comparison-results.json`, with its exact
measured commit and source hashes. Genuine cache and live results will be retained
if the authorized bounded study produces them. A skipped or failed optional run
will remain visible.

CP4 merged in PR406 as `b9b11ebca5a0e7fc162ce3e4f5a0844df22a9b05`.
Production continues to use facts-only Hindsight as a semantic supplement to FTS.
See [the architecture and contract evaluation](story-memory-evaluation.md) for
runtime operation; this comparison adds retrieval observations, not answer generation.

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
  --embedding-artifact /tmp/story-memory-retrieval-embeddings.json \
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

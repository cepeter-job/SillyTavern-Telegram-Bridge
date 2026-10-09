# Read-only prompt prefix and instruction profiler

Issue #421 now has a diagnostic alternative to another lossy history candidate.
This is an STTB-native implementation of the useful distinction in
[Roleplay Slim's prefix optimizer](https://github.com/lucifergzsz414/roleplay-slim/blob/65bfe5dae2c0cef65ef2fa004661cdf3f1d02e08/src/roleplay_slim/optimizer.py):
being a leading system message does **not** make every part of that message
stable across requests. No Roleplay Slim package, proxy or lossy strategy is
installed. There are no new dependencies.

## What it does, and does not do

`bridge.prompt_prefix_profile.PrefixProfiler` observes explicitly supplied
assembled requests. It never changes them, calls a provider, reads a database,
installs a runtime hook, logs a prompt, or enables context selection. The existing
source/reader/branch/dispatch safety fences are unchanged. PR #477 and all frozen
experiments remain independent.

It reports the common leading system/developer-message prefix **across samples
in the same cohort**, including a conservative partial prefix inside a mixed
system message. The partial boundary is rounded down to complete 128-character
blocks. Whole matching messages count in full. A changing request envelope
(including model, tools or generation settings) prevents a prefix-stability claim.
It does not normalize whitespace, alter timestamps, reorder messages or recommend
moving instructions past another role.

Stable instructions *after* a changing message are reported but cannot extend
the contiguous prefix. Presence in only some samples is not stable. One sample
is insufficient. Identical conversation turns are never instruction-deduplication
candidates.

Repeated complete system/developer messages are grouped only when all visible
message fields match, including role, name, cache metadata and other attributes.
These are **observations, not safe-to-remove instructions**. Their position,
provenance, priorities and reader semantics still require review. Their repeated
text estimates are not realized token savings and are never subtracted from the
reported prompt size.

## Explicit local capture format

The CLI accepts one JSON object per line:

```json
{"scope":{"session":"chat/session/incarnation","character":"card-and-persona-digest","reader":"reader-policy-revision","branch":"branch/rewrite-revision","provider":"account-and-provider-route","model":"resolved-model-id","stage":"provider"},"request":{"model":"resolved-model-id","messages":[{"role":"system","content":"Stable instructions"},{"role":"user","content":"Current input"}]},"usage":{"input_tokens":100,"cached_tokens":80,"output_tokens":20}}
```

The example text is synthetic. Real captures may contain sensitive prompts: keep
those input files private and local. Do not upload them to GitHub. The tool only
reads an explicitly selected regular file; it does not find or collect live
traffic. Symlink inputs, oversized records, duplicate JSON keys, invalid scope,
invalid usage and ambiguous span metadata are rejected without echoing content.

Every scope field is required. Include the session's incarnation, character AND
persona configuration, reader/audience policy and branch/rewrite revision in the
corresponding identity. Cohorts are separated by the **entire** scope. Valid stages
are `builder`, `provider` and `partial_replay`. The stage and identity are caller
declarations, not an authentication or source-validity proof. Never label a
partial reconstruction as a captured final provider request.

Usage is optional and is accepted only as bounded nonnegative integer fields:
`input_tokens`, `cached_tokens`, `output_tokens`. Normalize provider-specific
usage in the caller first. Cached input cannot exceed logical input. Missing
input remains unknown, not zero. Only observations declared `provider` contribute
to provider-usage summaries. The profiler neither verifies provider receipts nor
claims that a recent window represents total accepted-work accounting.

## Running it

From the repository root, use the application's Python environment:

```bash
python tools/profile_prompt_prefix.py --demo --output /private/new-prefix-demo.json
python tools/profile_prompt_prefix.py --samples /private/requests.jsonl \
  --window 16 --output /private/new-prefix-profile.json
```

The output must be a **new** path in an existing trusted directory. Output uses
exclusive creation with mode `0600`; an existing file, source file or symlink is
never overwritten. These filesystem protections require the POSIX platform used
by STTB's VPS. Standard output contains only counts and fixed status fields.

The synthetic demo uses the real `build_chat_messages()` path with fixed context,
changing Summary context, and an explicitly artificial duplicate-instruction
control. It has no production transcript or model call. It illustrates diagnostic
behavior, not production savings, quality improvement or traffic distribution.

The library API provides the same behavior without an input or output file:

```python
from bridge.prompt_prefix_profile import PrefixProfiler

profiler = PrefixProfiler(window=16, max_cohorts=16)
profiler.observe(request_body, scope, usage=normalized_provider_usage)
report = profiler.report()
```

Use a new profiler for a new measurement epoch. There is deliberately no
production hot-path integration in this change. Any future instrumentation needs
its own explicit opt-in, source-validity checks and deployment review.

## Native section attribution

When passed the builder's deferred messages, validated `_context_optional` spans
identify Summary, recall (`memory`), Episodes, NPC, Simulation and RAG payloads.
The profiler records their keyed fingerprints and lengths separately. It does
not infer sections from heading text, which can occur inside a story.

Everything outside these construction-owned spans is explicitly named
`instruction_remainder` or `dialogue_or_task`, **not** “immutable persona”. Scene,
World Info and other components without supplied spans are not independently
attributed. After transport removes construction metadata, only message-level
and unclassified-remainder observations remain. Never reconstruct provenance by
parsing a prompt heading. Section ordinals are occurrence indices within a kind;
shifting optional sections can appear unstable and are not semantically aligned.

## Privacy, memory and interpretation

Snapshots retain only HMAC-SHA256 fingerprints, bounded counts and allowlisted
labels. A fresh random key is created per profiler and is never exported. This
prevents ordinary dictionary guessing against public unkeyed text hashes and
cross-run identity correlation. Report fingerprints intentionally change across
runs. Equality/counts are reproducible for the same samples; raw report digests
are not. Equality patterns and lengths still reveal metadata: keep real reports
private unless deliberately reviewed for publication.

Cohorts are reported in first-encounter order without raw identifiers. The default
recent window is 16 samples; the maximum is 32, with at most 16 cohorts. Evictions
are counted explicitly. At most 65,536 fingerprint units can be retained globally;
a new observation that would exceed that budget is rejected **before** updating
existing observations. Each request has at most 512 messages, 1,024 section stamps,
500,000 text characters and 2,000,000 canonical-JSON bytes. CLI input is limited to
32 MB and 512 records, with bounded line reads. These are safety limits, not a
promise about exact process RSS.

Token estimates use the configured characters-per-token ratio (default 4). They
exclude message framing, images, tool schemas and other non-text costs. Prefix
text share is **not** provider cache-hit rate, bill reduction, quota savings or
input-token reduction. The report always leaves `provider_savings_fraction`,
`accepted_work_savings_fraction` and `cache_hit_rate_prediction` unknown and
`optimization_authorized` false.

The next decision is based on observed prompt shape: verify genuine structural
redundancy and provider cache behavior before proposing a guarded change. This
profiler by itself cannot close #421's 20% matched-provider and independent
narrative-quality gates.

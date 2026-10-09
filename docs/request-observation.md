# Opt-in final provider-request observation

The observer measures the generation requests the bridge actually constructs,
including automatic continuations, output-budget recovery, provider fallbacks,
and Codex's authentication retry. It does not compress, reorder, truncate,
modify, or resend any prompt. It makes no extra model requests.

## Enable deliberately

The default is **off**. In the application's existing private environment file:

```ini
SILLYTAVERN_REQUEST_OBSERVATION=on
```

This setting takes effect on the next normal application startup after deploying
code containing this feature. Deployment and opt-in are separate operations; a
source merge does neither. Invalid values are rejected rather than silently
enabling capture. Do not enable additional logging on a disk-full host.

Each generation send attempt emits one `provider.request_observed` event into the
existing private, rotating JSON diagnostic log. There is no extra raw capture
file, SQLite table, background polling task, or global environment mutation.
Logger rotation and disk-failure handling remain owned by the existing logging
system. Telemetry errors cannot change the generation outcome or trigger retry.

## What is observed

Hooks run after adapter normalization and budget checks, immediately before the
HTTP opener. The four supported adapters are Chat Completions, Anthropic Messages,
OpenAI Codex Responses, and OpenCode Muse Responses. OAuth refresh requests,
provider probes, embeddings, and Telegram traffic are not generation observations.
A send attempt does not prove the provider received or accepted the request.

The record contains:

- Random per-request identifier and ordinal, parent call/request correlation,
  actual route/model keyed identifiers, purpose, transport, phase and fallback
  attempt; elapsed time and exact serialized request-body **byte length**.
- Text character counts for instruction, user, assistant and tool/function roles.
  These are `wire_roles_only`: a user message may contain memory and a system
  message may contain a changing Summary. Roles are not semantic section labels.
  Images and other non-text costs are explicitly unestimated.
- Reported input, cached and output usage from that exact physical response.
  Missing usage remains unknown; a reported zero remains zero. Cumulative stream
  updates produce one final reading. Interrupted streams stay incomplete, and
  failed attempts are retained rather than removed from the denominator.
- Conservative stable-prefix character count, comparison sample count, and exact
  repeated instruction-payload group count when native reader scope is available.
  No instruction is deleted, and no cache-hit rate or monetary saving is inferred.

Observation does not request extra streaming usage fields. It sees only what the
existing adapter/provider reports, preserving request bodies with observation
on or off. The existing usage ledger receives the same callbacks as before.

## Privacy, scope and bounds

Raw prompts, responses, credentials, URLs, character names and reader/session IDs
are not retained in snapshots or reports. The observer uses an ephemeral HMAC key
that is never exported. Keyed model/route references can be correlated locally
with existing `provider.start` events via call/request IDs. References reset at
restart and are not a cross-process tracking identifier.

Story, regeneration/continuation, edited-user and image paths bind the builder's
native `MemoryPromptContext` identity before finalization removes internal
metadata. Ownership, session incarnation, character/persona configuration,
reader/consumer and branch/rewrite information isolate comparisons. Routed model,
endpoint, purpose and recovery phase further partition observations. Changes in
wire instructions or envelope invalidate prefix stability independently.

The capture describes the **assembled** scope, not fresh database authorization.
It grants no new reader knowledge. Calls without that native identity still
contribute send/usage records but cannot claim prefix stability. This includes
many helper calls; their `prefix_scope=unavailable` is not evidence of no stable
prefix. Current provider usage and prefix comparisons are separate measurements.

The runtime retains at most 128 completion records by default, 32 active tickets,
16 prefix cohorts, eight recent samples per cohort, and the existing 65,536-unit
fingerprint budget. Each fingerprinted request is bounded to 2 MB and 512 messages.
Overflow/unavailable profiling is reported as unavailable, not zero opportunity.
These bounds limit retained diagnostic state, not the allowed story context.
Restart clears memory. Existing log rotation limits persisted event history.

## Read a private diagnostic report

From a checkout with this implementation, using its Python environment:

```bash
python tools/report_request_observations.py \
  --log /private/bridge.jsonl \
  --window 128 \
  --output /private/new-request-observations.json
```

The input must be an explicit regular JSONL file, not a symlink or pipe. Reads are
bounded to 32 MB, 100,000 input records and 64 KiB per line. The report keeps at
most 256 observations, validates/allowlists every exported value, and refuses to
overwrite an existing file. New output permissions are owner-only (0600).
Do not concatenate overlapping log copies: every input event is counted once.

The report separates purposes, routes, models and phases and sums only known
usage. `complete_input_tokens=null` means the selected window is not fully known.
Unavailable/rotated/dropped events prevent claiming complete production traffic
cost. `completed` describes transport execution, not narrative approval,
successful Telegram delivery or an accepted-work accounting result.

## Issue #421 remains open

The feature is instrumentation, not a 30% reduction. Collect representative
ordinary traffic only after deliberate activation, then determine whether stable
structure or verified redundancy warrants a separately reviewed optimization.
Keep pruning off, preserve failed research evidence, and require complete matched
provider accounting and independent narrative review before any lossy change.

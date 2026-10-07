# Token usage

[Back to README](../README.md) · [Mini App](miniapp.md) ·
[Model calls explained](user-guide.md#model-calls-and-token-use)

Open **Manage → Advanced settings → Usage** in the Mini App. Choose **Last
24 hours**, **Last 7 days**, or **Last 30 days**, and **Current session** or
**All my sessions**. Refresh after a request completes to see new activity.

This page helps you understand which models and helper tasks are using tokens.
A token is a unit of text used by a model; it is not always a whole word. The
tracker shows what providers reported to the bridge, not a bill or your remaining
subscription allowance. Use your provider's account page for billing.

## Telegram command

Send `/usage` for a plain-text summary of the **active session in the current
chat or Forum Topic over the last 7 days**. It shows input, output, total, cached
and reasoning tokens; reported/complete coverage and failed/cancelled calls;
daily UTC totals; and up to five top models and purposes. Missing counters
appear as **unknown**, not zero. Partial counts remain included with a coverage
warning. Cached and reasoning tokens are subsets, not additions to Total.

The command reads the existing ledger without calling a model, adding story
messages, or opening a Mini App or inline panel. It remains available before
`/start` and after a story ends. Provider-reported usage is not a billing
statement or remaining quota. Use the Mini App for other time windows or
all-session reports.

## Reading the numbers

The tracker records **provider-reported token counts**. The estimates in
`/prompt` → Budget serve a different purpose: planning what fits in a request.
Large totals use compact notation; expand **Exact token counts** for full numbers.
Daily figures are also available in a table.

- **Input** includes the prompt and context. Cached-input counts are a subset, not
  tokens to add again. Anthropic's separate cache-read/cache-creation input fields
  are included in the input total.
- **Output** includes reported reasoning tokens. Reasoning is a subset, not an
  additional total. Providers that omit these details show a dash.
- **Fully reported** means that all observed responses in the operation supplied
  input and output counters and finished normally. Missing usage, interrupted
  streams and failed continuations reduce this coverage. Known partial counts
  remain included; unreported tokens are not fabricated as zero.

A dash means **unavailable**, whereas a displayed zero is a reported zero.
A failed or cancelled request can still consume tokens. Each “call” is one
bridge generation operation; internal continuation/recovery requests are summed
within it. Separate Light Novel retry operations appear separately. The model
breakdown shows the top twelve models in the selected scope, and the task
breakdown separates story generation from choices and other utility work.

`SILLYTAVERN_CONTEXT_INPUT_CAP_TOKENS` defaults to 49,152 estimated input
tokens **per provider request**; set it to 32,768 for a lower-cost cap. It
compacts optional history and evidence before sending a request, but fixed
instructions and the current turn are preserved and can cause an over-cap error.
The cap is an admission estimate, not an exact bound on provider-reported tokens
or the sum displayed for a turn with retries or other model tasks. The generation
`max_tokens` setting reserves output space; it does not cap input. Check
`/prompt` → Budget for the last request estimate and this Usage view for
provider-reported totals.

Charts use UTC with a rolling time window, so the first calendar day may be
partial. Totals are not provider invoices, prices, subscription limits or remaining
quota. No money amounts or billing estimates are generated.

### A worked example

Suppose a provider reports **1,000 input tokens**, including **600 cached input
tokens**, and **200 output tokens**, including **50 reasoning tokens**. The total
is **1,200**, not 1,850. Cached input is already inside Input, and reasoning is
already inside Output. This example explains the counters; it does not imply a
price or that every provider reports both details.

If output usage is missing, a dash means **unknown**, not free. A partially
reported request can contribute its known input count while still lowering
Fully reported coverage. Do not compare a partially reported total with an
invoice as though every token had been counted.

### Why a short reply can use many tokens

Input can include the character card, instructions, lore and earlier messages,
not just the message you typed. Choices, summaries, image-prompt preparation and
Director work can add their own requests. Use the task breakdown and
[model-call guide](user-guide.md#model-calls-and-token-use) to see which feature
is responsible before changing settings.

## What is covered

Tracking starts after a bridge version with usage tracking is installed and
restarted. Old messages do not contain the provider's original counters, so the
bridge cannot reconstruct historical usage from them.

Covered session-owned calls include ordinary stories, edited and image replies,
regeneration/continuation, response-language rendering, Humanizer, Light Novel
choices, character ranking/optimization, continuity summaries,
curated/episodic-memory extraction, NPC-state extraction and scene-state
extraction. Usage follows the actual selected Story/Utility/Director route.
Unscoped administrative/provider-health work and embedding
requests are not included. The Mini App reports the authenticated private bot
chat only; it does not combine group/forum-chat counters with a personal view.

OpenAI-compatible JSON and streaming responses (including the supported `data`
wrapper), Anthropic Messages streams, OpenCode Responses and native Codex
completion usage are normalized. Stream counters are cumulative: repeated final
usage events are not counted twice. Providers that omit usage remain visible as
coverage gaps. For an OpenAI-compatible provider that rejects the standard
`stream_options.include_usage` extension, set `stream_usage: false` in that
provider's private catalog. Its unreported streaming counters remain unknown;
the bridge does not silently retry the story or estimate them.

## Privacy and retention

The local SQLite database stores a usage record with the session, model, task,
completion time, status, duration and any available numeric counters. It does
**not** store prompts, response
text, credentials, provider URLs or upstream request identifiers. Query scope is
derived from signed Telegram identity, not a client-supplied chat ID. All-session
reports remain within that private chat.

Deleting a session also deletes its usage. Entries older than 90 days are pruned
when another tracked operation completes. No telemetry is sent to an external
analytics service. A ledger write failure logs a content-free warning and leaves
response delivery unchanged; the tracker is therefore an operational view, not an
authoritative billing record.

## Upgrade and rollback

The usage ledger is created by a forward database migration. Historical token
counts are not reconstructed from old messages. If you roll back across a newer
schema boundary, restore a matching pre-upgrade database backup instead of
deleting migration records. See [backup and restore](operations.md#database-migrations-backup-and-restore).

### Director calls

Scene planning appears under `director`. Updating the helper record to match
saved story messages appears under `director_reconcile`. An automatic
reassessment runs only when an event or
cadence requires it. A malformed version-1 proposal may get one repair request; an
unsupported schema version is rejected without repair. Those requests remain
visible as actual usage, not a fabricated billing amount.

Group speaker selection consumes the accepted plan locally and makes no second
planning request. Opening Director Room, viewing its history or changing a manual
objective also makes no provider call.

### Ending calls

The separate planning brief uses `director_epilogue`; epilogue prose uses
`epilogue` on the Story model. Committed-story reconciliation continues to use
`director_reconcile`. Viewing a saved ending, a blocked closed-session message,
and retrying already committed delivery make no new model calls. Provider-reported
usage may still be incomplete; no subscription balance or cost is inferred.

Creating an Alternate Ending performs local initialization without a Story,
Director or Hindsight call. It restores available local evidence and queues
background processing. Later native indexing sends only accepted fact summaries
to Hindsight; its own model use is not necessarily reported by the bridge's
provider-usage ledger. Duplicate completed branch requests do not repeat work.

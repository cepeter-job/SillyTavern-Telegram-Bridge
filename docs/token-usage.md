# Token usage

Open **Usage** in the Mini App's bottom navigation (or desktop sidebar). Home also
shows a seven-day summary for its current session. Choose **Last 24 hours**,
**Last 7 days**, or **Last 30 days**, and **Current session** or **All my sessions**.
Refresh after a request completes to see new activity.

## Reading the numbers

The tracker records **provider-reported token counts**, not estimates based on
message length. Large headline figures use compact notation; expand **Exact token
counts** for the full integers. Daily figures are available in an accessible table.

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

Charts use UTC with a rolling time window, so the first calendar day may be
partial. Totals are not provider invoices, prices, subscription limits or remaining
quota. No money amounts or billing estimates are generated.

## What is covered

Tracking starts only after a bridge version containing the usage ledger is installed and restarted. Existing conversations do not contain historical provider usage, so there is no backfill.

Covered session-owned calls include ordinary stories, edited and image replies,
regeneration/continuation, response-language rendering, Humanizer, Light Novel
choices, character ranking/optimization, continuity summaries, memory curation and
scene-state extraction. Usage follows the actual selected Story/Utility route.
Unscoped administrative/provider-health work, group-director planning and embedding
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

The SQLite ledger stores session identity, model, task, completion time, status,
duration and optional numeric counters. It does **not** store prompts, response
text, credentials, provider URLs or upstream request identifiers. Query scope is
derived from signed Telegram identity, not a client-supplied chat ID. All-session
reports remain within that private chat.

Deleting a session also deletes its usage. Entries older than 90 days are pruned
when another tracked operation completes. No telemetry is sent to an external
analytics service. A ledger write failure logs a content-free warning and leaves
response delivery unchanged; the tracker is therefore an operational view, not an
authoritative billing record.

## Upgrade and rollback

Token usage is stored by forward migration **3 — token_usage_ledger**. Existing messages and session settings are preserved when that migration is first applied. Older bridge versions that do not know migration 3 reject the newer schema; rollback across that boundary requires restoring a matching pre-upgrade database backup. Do not delete migration records to bypass the guard.

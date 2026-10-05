# Story memory reliability and retrieval design

Date: 2026-10-05
Status: approved for implementation, testing, pull request creation and merge
Baseline: 0049d9ce52486990e3a7ba7eeb8ea300cba12235

## Goal

Make long-running stories remember accepted events reliably, retrieve older details with usable source evidence, respect branch and character knowledge boundaries, and fit the actual generation context. Preserve SQLite as canonical storage and Hindsight as the semantic memory backend.

## Scope and authorization

The user approved all four stages after reviewing the architecture comparison. Development takes place in an isolated worktree. The live checkout and provider configuration are not implementation targets. Repository merge is authorized; production deployment is a separate operational action.

## Current behavior and concrete gaps

Accepted messages already have monotonic IDs and transactional narrative clocks. Sessions provide independent story and alternate-ending namespaces. SQLite stores episodic events, summaries, NPC knowledge and current scene state. Hindsight uses a chat bank with strict session tags and protects purge/retain races.

The current Hindsight conversation document is replaced from a latest-100-message snapshot. Ordinary memory work can disappear when executor admission fails. Summary coverage can advance before episodic extraction fails, making that interval ineligible for later extraction. Local retrieval scans only the latest 200 events. Scene and curator extraction truncate each message at 1,800 characters. Hindsight and unstructured context do not consistently honor historical or character-knowledge limits. A fact's deduplicated known_by union lacks the time of each knowledge grant. Prompt compaction occurs before some final instructions and reserves a configured default rather than the actual requested output.

These are source/runtime audit findings, not measured model-quality scores.

## Architecture

1. Canonical accepted transcript and durable work live together in SQLite.
2. Each derived memory layer has independent progress and retry state.
3. Bounded source segments retain complete text and immutable provenance under deterministic IDs.
4. One eligibility contract controls branch, session incarnation, accepted source, as-of boundary and reader knowledge.
5. Local indexed retrieval and Hindsight semantic retrieval produce authorized, traceable evidence.
6. Final prompt assembly budgets the exact generation payload, protecting a compact current scene allocation.
7. A reproducible synthetic-story evaluation measures deterministic correctness separately from provider-dependent answer quality.

## Global constraints

- Keep existing SQLite and Hindsight infrastructure and provider choices; do not introduce another database or hosted memory dependency.
- Do not execute model or network calls inside a SQLite write transaction.
- Preserve canonical transcript atomicity, session locks, purge epochs, monotonic message IDs, per-session namespaces and deletion/recreation protection.
- At-least-once remote writes must be idempotent. A failed retain, executor rejection, stale extraction or process restart must not advance successful coverage.
- Appends do not invalidate earlier accepted evidence. Use rewrite/source validity, not equality to a history counter that advances on every append.
- Transcript edits, deletion, owner moves, regeneration and selected variants invalidate affected derived evidence before it can enter a prompt.
- No silent truncation of extraction input. Bound calls by processing complete windows or explicit parts, with durable progress that represents all processed text.
- Memory payloads are untrusted data, never higher-priority instructions.
- Branch and visibility claims must be supported by canonical provenance. External tags and model-generated metadata alone are not authority.
- Knowledge is temporal. A later visibility grant must not become visible at an earlier story boundary.
- Explicitly distinguish narrator context from character-visible knowledge. Unclassified mixed-knowledge source text must not be presented as authorized character knowledge.
- No promise to infer every unannotated secret correctly; document classification limitations and fail closed when eligibility cannot be established.
- Bound worker admission, request size, prompt evidence and retry work. Keep foreground generation usable during a Hindsight outage.
- Do not expose real story contents, credentials or private provider settings in fixtures, logs or reports.
- Forward schema migrations preserve existing canonical data. Rebuildable derived indexes may be backfilled.
- Respect existing production-host test guards; use targeted device tests and the required full CI gate.

## Stage 1: reliable coverage

Introduce a durable memory outbox and per-layer coverage. Enqueue work atomically for accepted transcript mutations, including SQL paths outside the ordinary reply handler. Workers use short leases, bounded retries and explicit success/deferred/stale/failure outcomes. Startup and normal polling recover pending work without stealing active leases.

Retain coherent bounded source segments with session incarnation, start/end message IDs, part offsets when needed, content fingerprint, rewrite identity, purge epoch and deterministic Hindsight document IDs. Repeated delivery of one segment must replace the same document. Backfill existing sessions and alternate-ending copies from their full canonical source.

Summary, episodic extraction and Hindsight progress are independent. Existing scene/NPC/curator behavior must remain recoverable through the dispatcher or equivalent durable work where invoked. A valid empty extraction can advance extraction coverage; stale or failed extraction cannot.

## Stage 2: story eligibility

Represent the retrieval scope explicitly: chat, session/branch, session incarnation, as-of message boundary and reader identity. Validate eligible evidence against current canonical source and use rewrite-aware invalidation. Historical edits/regeneration must pass the generation boundary before retrieval.

Persist source and knowledge timing. Preserve useful recall after a harmless append. Reject evidence from deleted sessions, discarded revisions, another branch or the future. Resolve duplicate facts without retroactively broadening visibility.

Apply the same policy to Hindsight results, local episodes and evidence passages, scene state, summaries and edits/regeneration. Direct Hindsight source results require a mapped authorized document. Consolidated observations require a complete authorized source closure or are excluded conservatively. Unclassified full transcript documents remain archival sources rather than character-visible semantic facts.

## Stage 3: retrieval and prompt integration

Index the complete local episode corpus using SQLite FTS5 and bounded ranked queries. Keep source provenance and include short authorized evidence when it adds specificity. Build useful retrieval terms from the user's message and current cast/goals/objects, including short continuation/choice turns. Hindsight supplies semantic matching; do not advertise local lexical search as embeddings.

Process scene/curator and other extraction windows without silently dropping the tail of long messages. Handle oversized rows as explicit parts with observable coverage.

Compact the final assembled request after all light-novel or other appended instructions. Reserve the actual requested output tokens and use model-specific token heuristics consistently. Keep a small independently budgeted current scene block. Record diagnostics for the final request. All generation paths touched by these contracts must remain within budget or fail explicitly before provider dispatch.

## Stage 4: evaluation and operational validation

Check in a deterministic, offline long-story fixture and evaluation command. Measure fact/evidence retrieval at distances beyond 100 messages and 200 episodes, exact source provenance, secret visibility, as-of queries, edited outcomes, alternate branches, long-tail extraction coverage, crash/retry idempotency and final prompt budget.

Report query counts, pass/fail results, latency/resource methodology and limits. Use fixed synthetic data and generation settings when offering an optional real-provider comparison. Deterministic pipeline tests are not evidence of improved prose or model-answer accuracy.

## Acceptance

All four stages have meaningful regression tests with initial failing evidence, task-scoped review, and documentation. Targeted device tests and lint/static checks pass. Full tests, dependency audit, static analysis, secret scanning and required CodeQL checks pass on the final PR revision. The approved PR is merged through normal protected-branch controls. No deployment is implied by the merge.

# Callback Decomposition Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan
> task by task. The maintainer requested continuous execution and protected merges.

**Goal:** Resolve #314 through independently verified callback ownership waves.
**Architecture:** Preserve ingress checks; route exact/prefix callback values to
named handlers using explicit tables. No generic runtime registry or leaf service
container is introduced.
**Tech Stack:** Existing Python 3.11, SQLite, pytest, Ruff and mypy; no new packages.
**Spec:** `docs/superpowers/specs/2026-10-02-callback-decomposition.md`.

## Global constraints

Preserve callback order/IDs, authorization, actor/session binding, single-flight,
transaction ownership and exceptions. No dependencies, schema, deployment or
release changes. No sibling callback imports or compatibility re-exports.

## Review focus

Exact menu tokens must precede broad selection prefixes. Shared upload proposals
must retain the same nonce-bound validation as optimizer proposals. Unknown tokens
must retain current handled/not-handled behavior. Moved callbacks must still pass
through common authorization. Coverage for moved code must not be hidden by rename.

## Task 1 — character workflows

Files: `bridge/character_callbacks.py`, new
`bridge/character_optimizer_callbacks.py`, new
`bridge/character_proposal_callbacks.py`, `bridge/panel_callback_routes.py`,
`tools/static_analysis.py`, and character/routing regression tests.

Interfaces: existing callback positional request fields; explicit `ProviderPort`
and request context for optimizer/proposal owners; `GroupService` stays with
character selection. Root router supplies collaborators. Each owner returns bool.

- [ ] Add failing architecture and exact/prefix dispatch tests.
- [ ] Observe failures before moving production code.
- [ ] Move optimizer/proposal branches to their owners; decompose remaining
      character branches into named handlers with explicit route tables.
- [ ] Migrate tests to actual dependency owners, retaining behavioral assertions.
- [ ] Run targeted and full verification; inspect original-versus-moved bodies.
- [ ] Commit, open PR, require eight passing checks, merge without bypass.

## Task 2 — feature and common dispatcher

Files: `bridge/feature_callbacks.py`, `bridge/callback_dispatch.py`, routing tests.
Consumes #313's explicit delivery injection and canonical domain panel senders.
Produces short feature-family routing and separate common validation/routing units.

- [ ] Pin feature prefix routing and expired/foreign-owner refusal in tests.
- [ ] Observe failing decomposition guards; extract named family handlers.
- [ ] Preserve common authorization and scoped lookup ahead of dispatch.
- [ ] Run all gates, review coverage and merge through a separate protected PR.

## Task 3 — remaining audited callback entrypoints

Files: enum/provider, persona/session, NPC/world/greeting callback owners and
matching tests. Split into multiple PRs; do not bundle all owners into one rewrite.

- [ ] For each owner, record branch vocabulary and initialization dependencies.
- [ ] Add failing route/decomposition tests before extraction.
- [ ] Extract specific operations with unchanged branch bodies and explicit routes.
- [ ] Run full verification per PR and preserve the callback coverage baseline.
- [ ] Merge each exact checked head and confirm the final audited-entrypoint guard.
- [ ] Record final PR/acceptance evidence in #314 and close it as completed.

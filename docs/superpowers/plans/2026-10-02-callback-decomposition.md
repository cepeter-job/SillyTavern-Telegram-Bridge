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

- [x] Add failing architecture and exact/prefix dispatch tests.
- [x] Observe failures before moving production code.
- [x] Move optimizer/proposal branches to their owners; decompose remaining
      character branches into named handlers with explicit route tables.
- [x] Migrate tests to actual dependency owners, retaining behavioral assertions.
- [x] Run targeted and full verification; inspect original-versus-moved bodies.
- [x] Commit, open PR, require eight passing checks, merge without bypass.

## Task 2 — feature and common dispatcher

Files: `bridge/feature_callbacks.py`, `bridge/callback_dispatch.py`, routing tests.
Consumes #313's explicit delivery injection and canonical domain panel senders.
Produces short feature-family routing and separate common validation/routing units.

- [x] Pin feature prefix routing and expired/foreign-owner refusal in tests.
- [x] Observe failing decomposition guards; extract named family handlers.
- [x] Preserve common authorization and scoped lookup ahead of dispatch.
- [x] Run all gates, review coverage and merge through a separate protected PR.

## Task 3 — remaining audited callback entrypoints

Files: enum/provider, persona/session, NPC/world/greeting callback owners and
matching tests. Split into multiple PRs; do not bundle all owners into one rewrite.

- [x] For each owner, record branch vocabulary and initialization dependencies.
- [x] Add failing route/decomposition tests before extraction.
- [x] Extract specific operations with unchanged branch bodies and explicit routes.
- [ ] Run full verification per PR and preserve the callback coverage baseline.
- [ ] Merge each exact checked head and confirm the final audited-entrypoint guard.
- [ ] Record final PR/acceptance evidence in #314 and close it as completed.

## Execution record

Canonical panel ownership (#313) merged through #320; shared transcript reads
(#315) merged through #321. Both issues are closed. Character routing merged in
#322, feature/common routing in #323, and enum/provider actions in #324. Each
merged head passed all eight protected checks without bypass.

Persona/session and NPC/world/greeting changes have focused regression coverage;
full verification and their protected merges remain required before closing #314.
The final closure record will be attached to #314 after those merges. The source
regression covers all ten audited entrypoints, alongside behavioral safety tests.

### Continuation checkpoint

Persona/session verification passed: 2,489 tests and 775 subtests, 79.66%
application coverage, all security floors and Mini App checks. PR #325 contains
that independently reviewed wave; protected merge is pending at this checkpoint.
The final NPC/world/greeting wave still requires its full verification and merge.

Ruling: use the existing saved worktrees and active verification process after the
stream interruption, rather than repeating completed work or touching the main
checkout. Extracted action bodies match the originals structurally. Greeting
operation resolution explicitly returns the recovered operation ID; refusal
returns to the public handler before any page/choice action. Final completion
and exact-head CI evidence will be recorded on #314 rather than claiming success
in advance here.

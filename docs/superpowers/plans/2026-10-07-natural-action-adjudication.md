# Natural Action Adjudication Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. The user requested native implementation and merge in this session.

**Goal:** Let ordinary roleplay actions acquire fair, durable checks before narration without requiring a separate command.

**Architecture:** Strict evidence-scoped proposals use Utility or the selected Director route. A leased SQLite preflight freezes the proposal, bridge-owned d20 and canonical modifier before generation, then binds the receipt to the committed user row. Task progress shares the existing reversible simulation state and NPC Utility extraction.

**Tech Stack:** Python 3.11, SQLite, existing ProviderPort, Telegram and Mini App.

**Spec:** `docs/superpowers/specs/2026-10-07-natural-action-adjudication-design.md`

## Global Constraints

No live deployment changes. No new runtime dependency or provider. One d20 per admitted action; no model-owned numeric modifiers. One bounded preflight request; no automatic second Director call. Preserve user agency, hidden-data boundaries, transaction ownership and the 500-line module ratchet. Only durable in-world user turns receive automatic preflight; narrator steering and group orchestration remain nonautomatic.

## Review Focus

- Retries after a provider failure must reuse the ready roll without repeating inference.
- An expired concurrent worker must never roll after losing its lease.
- A rewrite/reset during inference must reject the pending result without publishing a check.
- Regeneration/continuation and a manual check followed by narration must not roll again.
- Task views and branch snapshots must not reveal private NPC plans or copy pending work.

## Task 1: Contract and persistence

- [x] Add failing parser, reservation, race, retry, stale-source and transaction tests.
- [x] Implement `action_contracts`, `action_repository`, `action_schema` and migration registration.
- [x] Run focused tests; inspect public versus private receipt fields and source identity.

## Task 2: Before-story integration

- [x] Add failing real-generation ordering and durable-job tests.
- [x] Implement `action_adjudication` and the narrow turn adapter; integrate ordinary and image-backed actions and edits.
- [x] Inject immutable original receipts for regeneration/continuation; reuse an explicit manual check awaiting narration.
- [x] Add `/check mode auto|director|manual` and persist/copy/delete the session preference through existing owners.
- [x] Verify timeout, wrong-owner job, missing lease and provider routing behavior.

## Task 3: Canonical task tracking

- [x] Add failing task parse/publication/projection/rollback/checkpoint tests.
- [x] Add the task simulation kind, user-only factual task deltas, bounds and views; reuse existing source receipts/history.
- [x] Keep task stage/progress/consequences post-story; no completed quest or task inferred from dice alone.
- [x] Document cost, controls, actual scope, recovery and examples.

## Final gate

- [x] Remove temporary workspace-transfer workflow.
- [ ] Run full CI, architecture, formatting, type, resource, Mini App and security checks.
- [x] Perform author review, distinguish it from unavailable independent agent review, and address GitHub findings.
- [ ] Merge only the exact green reviewed head; verify main contains the merge. No release/deployment.

## Integration rulings

Main acquired memory migrations 27 and 28 during implementation. Natural action receipts use migration 29, preserving both memory upgrades. Overlapping unmerged preflight prototypes were consolidated into one SQL-only reservation store and ActionTurn owner; no duplicate model calls or competing mode keys are shipped. Their tests are covered by the action runtime/generation suites. Production was not modified.

Author review found that a queued manual receipt could survive a rewritten evidence prefix. A failing regression reproduced it; manual reuse now respects the same invalidation cutoff as historical locked-result reads. Routine pending backlog remains separate from invalidation. UI smoke now checks task fields, markup escaping and private-link omission.

CI review: Gitleaks flagged the historical SQL fragment `lease_token=excluded.lease_token` in prototype commit `23548fec70a94ca3da6aef5aeea21f51b5805d2c`, line 104. Inspection confirms a literal SQL column reference, not credential material (lease UUIDs are created at runtime). The exact historical fingerprint alone is ignored; no rules or paths are broadly excluded, and history is not rewritten. Added the missing typed task-check helper annotation. Native broader focused suite: 307 passed, 41 subtests; current-main feature/migration subset: 98 passed, 41 subtests.

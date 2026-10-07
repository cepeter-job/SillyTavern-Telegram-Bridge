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

- [ ] Add failing parser, reservation, race, retry, stale-source and transaction tests.
- [ ] Implement `action_contracts`, `action_repository`, `action_schema` and migration registration.
- [ ] Run focused tests; inspect public versus private receipt fields and source identity.

## Task 2: Before-story integration

- [ ] Add failing real-generation ordering and durable-job tests.
- [ ] Implement `action_adjudication` and the narrow turn adapter; integrate ordinary and image-backed actions and edits.
- [ ] Inject immutable original receipts for regeneration/continuation; reuse an explicit manual check awaiting narration.
- [ ] Add `/check mode auto|director|manual` and persist/copy/delete the session preference through existing owners.
- [ ] Verify timeout, wrong-owner job, missing lease and provider routing behavior.

## Task 3: Canonical task tracking

- [ ] Add failing task parse/publication/projection/rollback/checkpoint tests.
- [ ] Add the task simulation kind, user-only factual task deltas, bounds and views; reuse existing source receipts/history.
- [ ] Keep task stage/progress/consequences post-story; no completed quest or task inferred from dice alone.
- [ ] Document cost, controls, actual scope, recovery and examples.

## Final gate

- [ ] Remove temporary workspace-transfer workflow.
- [ ] Run full CI, architecture, formatting, type, resource, Mini App and security checks.
- [ ] Perform author review, distinguish it from unavailable independent agent review, and address GitHub findings.
- [ ] Merge only the exact green reviewed head; verify main contains the merge. No release/deployment.

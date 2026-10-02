# Callback decomposition design — issue #314

## Goal and authority

The maintainer approved implementation, protected PR merges and closure of issues
#313–#315 on 2026-10-02. This design implements #314 without altering callback
behavior. #313 separately establishes canonical panel senders; #315 separately
shares transcript reads. Neither is bundled into the character change.

## Invariants

- Authorization, actor/session binding and single-flight admission remain at the
  existing ingress/dispatcher boundary; no handler bypass is introduced.
- Preserve callback strings, first-match order, unknown-input behavior, response
  wording, durable operations, transaction ownership and exception propagation.
- Leaf handlers receive explicit request values and narrow collaborators, never
  `BridgeServices`; callback siblings do not import each other.
- No provider calls in tests, new dependencies, schema changes, deployment changes,
  release updates, compatibility re-exports or generic routing framework.
- Existing regressions stay meaningful; patch dependencies at their actual owner.

## Alternatives and selected approach

A repository-wide framework would increase indirection and risk. Merely slicing
functions without identifiable route ownership would hide rather than resolve the
problem. Use explicit ordered route tables and narrowly named private handlers,
retaining modules where their domain already has a coherent owner. Extract new
modules only for independently owned character optimizer and proposal workflows.

## Character ownership

`character_optimizer_callbacks` owns optimizer selection, automatic/manual
preparation and refinement. `character_proposal_callbacks` owns the shared native
upload/optimizer confirmation and preview lifecycle. These are peers of
`character_callbacks`, not callees imported by it. `panel_callback_routes` invokes
them before remaining character selection/info/restore/delete handling. Original
branch bodies are moved without changing their checks or persistence operations.
Within each owner, explicit exact/prefix tables choose one named handler.

## Later waves

After the character pattern passes review and protected CI, apply it to feature
families and simplify `callback_dispatch` into common checks followed by explicit
routing. Continue through the remaining audited callback owners: enum, provider,
persona, session, NPC, world and greeting dispatch. Provider HTTP transport and
message ingress are not callback dispatchers and remain outside #314.

Each wave is a separate reviewable PR against current main. #314 closes only when
all audited callback entrypoints have been checked and decomposed where needed.
Do not close it merely because the optimizer extraction landed.

## Proof

Pin exact and prefix routing, precedence, one-handler execution, unknown inputs,
actor/session refusal and single-flight behavior. Retain real workflow regressions
for proposal expiry/replay, restore/delete protection and provider errors. Compare
coverage of moved lines across old/new owners, not only a renamed file percentage.
Run full tests with coverage/security floors, Mini App smoke, lock validation,
Ruff, mypy, dependency audits, and all eight protected GitHub checks before merge.

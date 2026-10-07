# Natural action adjudication

## Intent and authorization

The user approved Utility/Director adjudication before Story generation, bridge-owned random rolls, durable receipts, and canonical task tracking, and explicitly requested implementation, pull request and merge. Ordinary RP should not require a separate /check command. This change does not authorize a production deployment or changing live private configuration.

## Ownership and flow

For a new in-world user action, a bounded Utility preflight decides no_check, auto_success, or check. It may propose one skill domain, a difficulty (1..20), factual stakes and a short task label. The bridge validates the proposal, computes modifiers from canonical established actor state, draws exactly one d20, and persists a receipt before Story inference. The model never supplies rolls or numeric modifiers. A session may select Director adjudication instead; it uses the same contract and evidence restrictions, not an additional model call on every turn.

Narrative steering, scene transitions, opening greetings, routine dialogue and actions without meaningful uncertainty must not acquire arbitrary dice checks. The model policy requires both uncertainty and a meaningful stake. Impossible actions are not made possible by a high die roll. Consent, another character's agency and the user's major choices cannot be overridden by a roll.

Story receives the admitted attempt and immutable mechanical result as mandatory runtime context, before output begins. It describes consequences without rerolling, printing a ledger, or turning proposed stakes into facts before they occur. Existing post-story extraction continues to update canonical NPCs, scene and trackers from committed prose. A task record distinguishes attempted mechanics from established progress.

## Durability and lifecycle

A preflight reservation belongs to one session incarnation, stable turn identity, actor, action digest and captured source boundary. Network calls run outside SQLite write transactions. Concurrent/repeated requests cannot choose a second roll. Invalid proposals or provider failures cannot become successful rolls or silently publish tracker coverage. Publication revalidates the captured session/history before binding the receipt to the committed user action. Delivery recovery reuses committed data.

Explicit /check remains usable and its result is not automatically rolled again on the following narration. Regeneration and continuation reuse the original admitted result. Rewritten actions invalidate their prior result; reset, deletion, history replacement and alternate-ending boundaries must not import pending work or future checks. Hidden plans are not evidence; private evidence is never exposed merely because it changed a difficulty.

## User controls and costs

Automatic adjudication is the natural-RP mode. Controls retain a manual-only option and a Director-routed option, with status visible from the existing check/tracker surfaces. Automatic adjudication can add one bounded model request before a new ordinary turn; it never adds a second request just to generate a random number. No new provider, background timer, runtime dependency or model-generated dice are introduced.

## Verification

Regression tests cover meaningful checks versus no-check, strict JSON/type/size bounds, missing evidence, no model-owned roll/modifiers, one RNG result across retry/race, rollback on stale source, manual-check reuse, original-result reuse on regeneration/continuation, post-story task progress, session isolation, reset/delete/branch behavior, hidden-data filtering and exact generation ordering. Run the full existing test suite, resource-warning checks, module-size ratchet, architecture/lint/type checks, Mini App smoke, dependency audit and protected GitHub checks before merging. Public documentation states the actual shipped scope and model-call cost.

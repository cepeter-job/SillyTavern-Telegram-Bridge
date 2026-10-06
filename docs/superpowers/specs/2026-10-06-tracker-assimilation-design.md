# Canonical tracker assimilation

## Purpose and recovery status

The approved direction is to move the useful Internal States trackers into the bridge's existing story-state architecture. Story replies contain prose and dialogue. Durable tracker values are extracted from committed evidence and supplied to subsequent model calls as bounded context. Users should not have to maintain a large tracker system prompt.

This document records the previously approved Architecture B and the implementation decisions needed to complete it. The user authorized implementation, a PR and merge, and subsequently requested recovery of unfinished VPS work followed by development from GitHub. Recovery commits `242ce46` and `6871a56` preserve the interrupted implementation; they are work in progress, not a completed feature. PR #377 remains closed and unmerged.

## Canonical owners

| Domain | Owner and resulting behavior |
| --- | --- |
| NPC identity, location, mood, secrets and ordinary status | Existing NPC Bank; aliases resolve to its canonical character names. |
| Physical scene, weather, objects and participants | Existing Scene State. No duplicate physics or location tracker. |
| Relationship mechanics | Typed relationship records: BOND, Sparks and Grudge. NPC relationship text is a readable projection once structured state exists. |
| Agenda progress | Typed agenda records extend the NPC agenda; NPC Bank receives their readable projection. |
| User inventory, skills and conditions | A bounded actor record; only explicit established changes enter it. |
| Quests | Typed progress/reward metadata linked to canonical Narrative Arcs. |
| Foreshadowing | Canonical Narrative Threads/Arcs, with tracker metadata as a projection/link rather than a competing plot engine. |
| Factions | Bounded canonical faction facts and relations. Private intel remains narrator data. |
| Checks | Bridge-owned d20 results with a durable action key and source boundary. |
| Plot direction, finale and ending | Existing Director and Ending Engine. Proposals do not become committed tracker facts. |

Storage uses individual validated domain records, not one model-controlled world-state blob. Existing canonical owners remain responsible for their fields. No new external service, database or runtime dependency is needed.

## Core invariants

- Every mutation belongs to an existing message in the same chat/session. Ordered publication rejects older source rows. Repeating a completed row is a no-op, including rows that caused no state change.
- Utility inference runs outside write transactions. The existing memory draft acceptance validates its lease, source digest, rewrite identity and session incarnation before publishing NPC and simulation changes in one transaction.
- Relationship decay/conversion and offscreen agenda advancement run once per committed assistant turn, never once per source fragment or user message.
- Invalidating any message immediately prevents its dependent tracker state from entering prompts. Re-extraction rolls back the invalidated suffix before applying corrected evidence.
- Reset and wholesale transcript replacement purge derived simulation state. Session deletion cascades. Edit, regeneration, continuation and swipe follow their existing canonical rollback boundaries.
- Alternate endings copy only state valid at the immutable checkpoint boundary. Restored source references use the target transcript IDs; origin changes and newly executed checks cannot mutate the branch.
- Read paths and source accumulators have enforced bounds before loading/formatting large collections. New Python modules stay at or below 500 lines. Existing module-size exceptions may only shrink.
- Stored values and extracted text are untrusted data. Canonical context grants no character knowledge of offscreen secrets, hidden agendas, faction intel or future payoffs.

## Tracker mechanics

The recovered implementation supplies the initial bounded mechanics. BOND ranges from -5 to 20. Positive BOND gains come from Sparks conversion; direct negative changes are limited to two per committed turn. Sparks changes are bounded to -2..2 and Grudge changes to -1..1. Every third assistant turn, Grudge of at least five lowers BOND by one and clears Grudge; a smaller positive Grudge decays by one. Every fifth assistant turn, seven Sparks (fourteen while Grudge is at least three) convert into one BOND; otherwise Sparks decay by one when that turn had no positive Sparks event. Neither a user-message publication nor a retry repeats these effects.

Agendas contain an objective, step, maximum steps (1..20), status and the associated NPC's location reference. A committed assistant turn can advance an active offscreen agenda by one step. Explicit evidence, an on-screen appearance or a paused/completed status prevents automatic advancement. Completion records an established scheduler result; it does not fabricate a consequential scene, inventory reward or character knowledge.

Inventory, skills and conditions preserve the chronological order of add/remove operations across source parts. Item modifiers are -2..2, and the effective actor total is bounded to -6..6. Factions and quest updates preserve unspecified fields. Invalid booleans, objects masquerading as text, oversized data and malformed partial responses fail safely without publishing a half-row.

The default aggregate limits are 64 records per domain, 64 actor entries per collection, 32 incoming updates per bounded source part, 16 KiB per stored record and 6,000 characters per simulation prompt section. Accumulation must stay below the existing memory draft size limit and must not discard an early update silently. Historical reconstruction selects the latest qualifying revision per entity in SQL rather than materializing the full journal.

Checkpoint format 1 retains the ordered tracker revisions, including identity-deletion markers, together with current records, locked checks and accepted-source receipts. Capture rejects more than 4,096 revisions or a payload beyond the shared 1 MiB checkpoint budget; it never truncates old revisions while retaining receipts. Restoration rebuilds records from those revisions and verifies the current-state summary before accepting receipts. Pre-feature checkpoints without simulation data remain valid; unreleased tracker snapshots without reversible history are rejected.

NPC checkpoints also retain bounded native field history, including before/after values, audiences, modes and source timestamps. Capture checks the encoded journal size in SQL before fetching it. Restoring both domains preserves the exact earlier projection fingerprint through a subsequent rewind, while real native clears and manual field overrides remain authoritative. Legacy NPC checkpoints without field history retain their previous baseline restoration behavior.

An NPC identity established later can adopt an existing unambiguous alias tracker at that accepted source. Earlier historical reads retain the alias, and rollback reverses the identity transition. Mechanically identical records can coalesce without counting their scores twice; conflicting records reject the complete publication until explicitly corrected. Native/manual field ownership remains authoritative. Future NPC identities cannot be projected into earlier backfill sources.

## Extraction and existing values

Reuse the NPC Utility call and durable `npc` job; do not add a new call or worker for every turn. Its JSON result contains ordinary NPC operations plus a validated `simulation` object. Only a complete, accepted source row may publish. A malformed simulation object rejects that source part rather than silently advancing coverage without its tracker changes.

The extractor receives the bounded canonical tracker state at the source boundary. It extracts established events, not possible actions, instructions embedded in source text, Director plans, invented inventory, or inferred private beliefs. Previous tracker values may be imported only when an exact source quote proves the value. Ambiguous legacy values stay unset; existing explicit canonical values take precedence. Migration does not invent state or silently reinterpret arbitrary old HTML as canonical facts.

## Prompt and output integration

All story generation paths, historical edit/regeneration calls, image-backed turns, separate Light Novel choices and Director input receive appropriate bounded simulation context. Historical reads use their captured source cutoff. Simulation descriptions are an optional context span participating in normal token-budget compaction; the fixed output/knowledge policy stays outside that span.

Character-scoped private trackers require every resolved prompt reader to be the owning NPC; mixed or unresolved group readers receive no such private state. Explicit narrator viewpoints retain their broader context. Director and separate-choice prompts keep their fixed JSON intact and append a separately registered optional tracker body in the same user message. Both routes compact against their selected model and actual output reservation before dispatch; Director repair checks preserve the original fixed context.

The final bridge-owned output policy says to write story/dialogue only and never emit Internal States, GM notebooks, private tracker blocks or mechanics unless the user explicitly requests a check result. Recognizable legacy Internal States output templates are excluded from the effective session prompt without editing the user's stored prompt file. Normal transport drops an accidentally emitted `<internal_states>` block, including incomplete streaming blocks; those discarded blocks are not a substitute for canonical state and are not retained as hidden assistant history.

The bridge does not remove unrelated custom prompt instructions, narrative prose, ordinary spoilers or other Telegram formatting.

## Check interface

Expose a small explicit `/check <domain> <DC> <action>` command so checks are usable without model-owned dice. DC is an integer from 1 to 20. The command admits a durable action, obtains applicable canonical modifiers, rolls once using the bridge RNG, records the result and makes it available to the next story turn. Re-delivery of the same admitted command returns the existing result. A second intentional command is a new action.

Natural 1/20 are critical failure/success. Otherwise a delta of at least 8 or at most -8 is critical; nonnegative is success, -1..-3 is near miss, and the remainder is failure. Model output never supplies the random roll or changes a locked result. Historical modifiers use the check's source cutoff. A discarded/replaced action loses its check; retrying delivery does not.

## Acceptance and rollout

Verification must cover replay and stale-source races; user versus assistant ticks; multi-part operations; aliases and canonical projections; reset/delete/edit/regen/continuation/swipes; invalidated context before worker repair; checkpoint isolation; check retry and source ownership; shared extraction and prompt boundaries; incomplete streaming blocks; context pressure and record bounds. Run the full repository test suite and all applicable existing static/security gates before merge.

Publish the completed implementation through the recovered branch and PR #380. Keep release publication and live deployment separate from this implementation task. The VPS is used only to preserve unfinished source work; subsequent development and tests use the GitHub-backed workspace.

# Preset compatibility decision

Decision date: 2026-10-09. Applies to the v0.3.019 native runtime and roadmap #468.

## Current decision

Keep native TXT/simple-JSON System Prompts and opt-in writing profiles. Do not implement a full SillyTavern Chat Completion preset interpreter as part of this validation/release-cleanup checkpoint. Reject exports identified by reserved prompts/prompt_order keys explicitly; never reinterpret their auxiliary strings as a prompt or silently flatten enabled and disabled modules.

This closes the architecture-choice task, not the provider-quality gate. The four profiles are optional writing guidance, not substitutes for native narrative policy, memory, state, dice or Telegram formatting. No profile winner, new default or general compatibility claim is established by deterministic assembly tests.

The current supported formats, installation and Off/reselection behavior are in [Native writing profiles](native-writing-profiles.md). Prompt ordering and provider consolidation limits are in [Prompt assembly](prompt-assembly.md). The benchmark protocol is in [Writing profile evaluation](writing-profile-evaluation.md).

## Why retain native ownership

The bridge owns accepted messages, source revisions, reader knowledge, canonical trackers, dice receipts, narrator/user control and the response envelope. A foreign prompt's ledger, dice instructions or user-impersonation mode must not create a second authority. More prose instructions cannot establish missing source evidence or turn a failed quality trial into successful validation.

The existing macro subset remains the implemented native subset, not an approximation of a foreign export. Unrecognized variables or conditionals are not silently evaluated, stripped, or treated as evidence that an import succeeded. Full export support requires its own reviewed implementation and golden requests.

## Contract required before any future importer

A future proposal must define all of the following before activation:

- A versioned supported subset with deterministic module IDs, enabled states, order, marker insertion positions and role mapping. Duplicate IDs, invalid roles or unsupported mandatory modules fail explicitly. Disabled modules must not leak text or execute state mutations.
- Bounded macro parsing with explicit evaluation order, variable scope, nesting/expansion limits and typed values. No arbitrary code, network, file access, external command execution or silent recursive expansion.
- A native-authority conflict report for trackers, random checks, user impersonation, HTML/scripts, output envelopes and private knowledge. A compatibility preview is a diagnostic, not a permission to override native contracts.
- Provider-specific golden payloads. An adapter that consolidates system instructions cannot claim identical interleaving; any difference must be reported before enabling the profile.
- Sampling translation by actual provider capability. Unsupported reasoning, stops, output limits and schema options must be reported, not silently substituted or advertised as equivalent.
- Source-size and expanded-request budgets using the real assembled payload. Account for mandatory context, history, all helpers/repairs/fallbacks and provider-reported usage; do not estimate cost solely from the export file size.

## Lifecycle and counter semantics

These are requirements for a future native mapping, not claims of implemented foreign-macro compatibility.

| Event | Required native semantics |
| --- | --- |
| Preview or catalogue discovery | Pure/read-only. No persistent variable or counter increments, dice roll, provider generation or story mutation. |
| Initial generation | Allocate one durable operation identity; evaluate against a frozen session/source/settings snapshot. |
| Network retry or delivery retry | Reuse the original operation's state and receipts. Do not increment a committed-turn counter or reroll an accepted check. |
| Regeneration | A new candidate operation from the same source boundary, not an extra committed story turn. Superseded candidates cannot publish state. |
| Accepted candidate | Atomically commit its narrative/state effects once, protected by source revision and lease/operation fences. |
| Cancellation or failed output | Record attempted work and its measured/unknown usage, but do not commit story variables, counters or fictional outcomes. |
| Edit or branch | Resolve from the native revision/snapshot boundary. Never import later state or private-reader grants into an earlier or different branch. |

A foreign preset's "increment on every generation" variable is not a committed-turn counter. The importer must either implement a separately named, documented request-local generation counter with tested retry behavior, or reject that construct. It must never silently rename or reinterpret it. Read-only preview remains read-only even when the source export expects side effects.

## Release and activation gates

An importer requires golden request tests, malformed-input and adversarial-macro tests, lifecycle/race tests, provider-adapter verification and a rollback path. New narrative defaults or history pruning additionally require representative matched provider measurements and independent blinded human review. Automated judges may assist, but must be labeled as automated and cannot attest to human acceptance.

Retain this native-only decision until a separate proposal satisfies those gates. Do not add an unused compatibility subsystem merely to mark a roadmap checkbox complete.

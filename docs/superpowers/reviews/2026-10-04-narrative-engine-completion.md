# Narrative Engine: five-phase completion review

Date: 2026-10-04

Status: all five implementation phases completed and verified locally. The feature is proposed for review, not deployed. Independent human/model review, merge into main, release, and production rollout have not been performed.

## Source and scope

The approved design is `docs/superpowers/specs/2026-10-03-narrative-engine-ai-director-design.md`; the five execution plans are in `docs/superpowers/plans/`.

Verified native implementation commit: `15a9e328996b45ae48a31bf6b2f87c3e4db6aacf`.

Verified native tree: `df2f2ccaa2be4602e03f174743eaa9446e2054c7`.

PR #359 targets `design/narrative-engine-ai-director-20261003`, whose Phase 1 base was integrated through #357. This review document adds no runtime changes to the verified native tree. The PR's current GitHub checks remain the authority for its final published head.

## Completed phases

| Phase | Delivered behavior | Principal regression coverage |
| --- | --- | --- |
| 1: Narrative foundation | Player-centric, Ensemble, World-driven, Observer and Custom; per-user setup defaults and session-owned settings; Narrative-first character setup; explicit scene/thread continuity and shared Story, Light Novel and Group policy. | Narrative policy, setup, panels, generation and reconciliation suites. |
| 2: Canonical Director | Independent Director model/reasoning route with Utility fallback; validated structured proposals; bounded adaptive cadence; Telegram/Mini App Director Room; scoped manual directions; one canonical Group objective. | Director routing, validation, service, cadence, panels, Mini App and group integration suites. |
| 3: Closed Story foundation | Evidence-backed arc state, revisioned Ending Goals, current-revision finale readiness, immutable atomic pre-finale checkpoints and inspection controls. | Arc, Ending State, Director ending and checkpoint suites. |
| 4: Separate epilogue and hard closure | Multi-turn finales; committed resolution reconciliation; dedicated Director epilogue brief and separate Story prose; durable recovery; fixed closed-session response; no fresh creative generation in the completed original. | Finale resolution, epilogue service, closed guards, ending runtime, delivery and ending panel suites. |
| 5: Alternate Ending | Independent target sessions restored from checkpoint-time transcript/configuration/local continuity; fresh IDs and operation identity; target-only optional Hindsight initialization; reusable immutable source checkpoint; Telegram/Mini App controls and bounded restart recovery. | Alternate-ending core, memory, recovery and panel suites, including a normal Story turn in the restored target. |

The existing `/branch` response-variant selector is unchanged. Alternate Ending is a separate action in Director Room.

## Fresh verification

The complete source tree was fingerprinted before and after the final full-suite run. The fingerprints matched; no source edits occurred during verification.

- Full repository suite: **3,203 tests passed; 802 subtests passed**.
- Coverage: **81.42%**, above the unchanged 76% floor.
- Python resource warnings were treated as errors.
- Ruff lint and formatting passed: 597 Python files were already formatted.
- Architecture analysis passed: 321 modules, 1,795 edges, zero cyclic modules and zero reciprocal pairs.
- Mypy passed on all 153 configured targets.
- Security-critical coverage gates passed without lowering their thresholds.
- Leak scan passed the unchanged high-severity gate; it reported one informational finding, not a proof that all possible leaks are absent.
- Mini App browser smoke passed all 14 pages with zero browser errors. It also exercised ending-mode and finale-confirmation changes and proved they remained bound to the rendered session even after another view changed global client state.
- Existing Mini App mutation, stale-session rejection, optimizer recovery and summary-recovery checks passed.

External providers were mocked in tests. No paid/live Story or Director calls were used for verification. Optional Hindsight behavior was tested with isolated fixtures. Disposable SQLite fixtures used private tmpfs on the migrated VPS; production database paths and settings were not changed. Recovery tests cover injected failures, transaction rollback and simulated restart boundaries, not physical host power-loss testing.

## Important invariants exercised

Committed story remains authoritative over Director intention. Stale proposals and reconciliation cannot overwrite newer history. Invalid structured proposals have bounded repair behavior, and an unavailable Director does not silently switch narrative style.

A committed resolution is not regenerated after epilogue failure. A committed epilogue is not regenerated after delivery failure. The session closes only after committed epilogue reconciliation. Telegram acknowledgement metadata can still advance after closure without changing the story.

Closed-session ingress rejects ordinary text, normalized commands, voice before transcription, image-backed story turns, choices and regeneration/edit/continuation paths before new provider work. Database guards reject late or unchecked changes to completed story facts. Read-only status/history and already-committed delivery recovery remain available. Recovery does not create new expressions, speech or images.

Alternate-ending local creation is transactional. A duplicate request reuses its target; a later explicit request may create another branch from the same checkpoint. The original is never reopened. Target creation remaps transcript references and does not reuse source Telegram messages, delivery progress, queued jobs, callback records or response variants. Deleting the source after local creation does not delete the independent target.

Hindsight continues to use one bank per chat. Branch isolation comes from target session tags and target document IDs, with strict recall filtering. A failed seed leaves the copied transcript and local memories usable and never redirects recall to the original's ending.

## Separate self-review and resulting corrections

The review was performed inline in a separate pass, not by an independent reviewer. Concrete regressions were written for findings before their fixes:

1. A stale manual scene direction could suppress Director scheduling after a style change or history rewrite. Scheduling now uses the same active-plan validity contract as Story guidance.
2. Prototype epilogue delivery assumptions did not match the actual delivery owner. Epilogue recovery now uses the existing acknowledgement contract rather than a second delivery implementation.
3. Model-supplied epilogue phase needed durable identity validation. Ordinary Story extraction cannot declare epilogue or closure without the corresponding committed ending state.
4. Reentrant delivery recovery could duplicate sends. The existing durable ending lease now coalesces recovery.
5. A finale reset could reach external deletion before a later database guard rejected the reset. Reset is now rejected before any Telegram or memory deletion.
6. Generic JSON remapping could alter a literal NPC value containing a row-like field. Only explicit evidence JSON is remapped; story values remain opaque.
7. Completed branch operation payloads could outlive transient operation retention. Completion now discards that payload atomically while retaining the small permanent provenance receipt needed for idempotency.

## Deliberate implementation refinements

Schema changes were added incrementally through migration 18 rather than rewriting migrations already used by published earlier phases. Legacy Group Director goal storage is retired after verified copying; there is no parallel compatibility runtime source.

The new branch-lineage table stores permanent provenance only. Transient admission, leases and recovery continue to use the existing operations framework. Transcript copying streams rows and retains only the bounded snapshot reference map instead of loading the entire story into memory.

## Publication boundary

Exact native Git history was published through checksum-pinned source bundles. A temporary exact-file endpoint exposed only committed public-repository source, and GitHub checked its SHA-256, native commit/tree and ancestry before a non-force feature-branch push. The endpoint was stopped after import. Disposable import workflows are not part of the proposed product tree.

No production dependency lockfile, normal CI workflow, provider credentials/configuration, main branch, release tag or live deployment was changed. The worktree remains available for PR review fixes.

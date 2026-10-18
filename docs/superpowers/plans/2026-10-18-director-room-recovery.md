# Director Room Recovery Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent backend and frontend changes, followed by whole-branch review.

**Goal:** Restore Director reassessment and expose prerequisite rejection accurately without changing accepted story evidence.

**Architecture:** Keep narrative reconciliation and Director planning as separate model tasks. Pass existing fixed rejection codes through the reconciliation prerequisite, then handle `accepted: false` as a rejected reassessment in the Mini App. Evaluate the configured helper against the current canonical input on an isolated database snapshot before changing its session override.

**Tech Stack:** Python, SQLite, JavaScript, pytest, Node test runner.

**Spec:** The user requested "Fix director room" after the latest reassessment log showed `narrative.reconciliation_rejected` at publication with `invalid_evidence`, followed by a generic Director `unavailable` result. Existing session authorization covers the PR, merge, deployment and signed release.

## Global Constraints

- Preserve strict committed-row evidence, atomic publication, stale-source checks, accepted state and existing repair bounds.
- Keep Utility inheritance and the separate Director model contract.
- Report fixed codes and fixed user messages; never persist source prose or raw provider exceptions in diagnostics.
- Use the existing deployment and release-signing process after required checks pass.

## Review Focus

- Semantic evidence rejection must stop before any planner request and must not invoke format repair.
- A newer source clock must not inherit the rejected older attempt's cause.
- Exhausted format repair and provider failure must preserve useful fixed causes.
- A resolved HTTP/job request with `accepted: false` must retain unsaved form values and display failure.
- Live recovery must use current source revisions, preserve accepted facts and finish with an accepted plan.

## Task 1: Preserve the prerequisite rejection

**Files:** `bridge/narrative_response.py`, `bridge/narrative_reconciliation.py`, `bridge/director_service.py`, and focused Python tests.

**Interface:** Add optional `on_rejection: Callable[[str], None] | None` to response generation and reconciliation. Extend `NarrativeStateUnavailable` with a default-empty `rejection_code`. Director failure reporting consumes this fixed code.

- [x] Add and run failing behavioral tests for real stale-state evidence rejection, format exhaustion, privacy and source revision change.
- [x] Hand off the existing classified reason only at terminal rejection; clear obsolete reasons when progress or a newer clock supersedes them.
- [x] Map reconciliation failures to actionable fixed Director messages and the existing `rejection_code` diagnostic field.
- [x] Run the focused narrative and Director suites; retain all no-repair and accepted-state preservation assertions.

## Task 2: Show a rejected reassessment as failure

**Files:** `bridge/miniapp_assets/director.js` and its existing Node tests.

**Interface:** The Reassess request resolves to `{accepted: boolean, message: string}`. An explicit false result uses the existing failure path.

- [x] Reproduce a resolved business rejection in the frontend test; assert visible failure, retained drafts and no success refresh.
- [x] Add a Reassess-only guard without changing the shared job controller or other actions.
- [x] Run the Director frontend tests and update the user guide to explain the prerequisite and rejection behavior.

## Task 3: Clarify the model contract

**Files:** `bridge/narrative_extraction.py` and `tests/test_narrative_acceptance_recovery.py`.

- [x] Reproduce quote mismatch with current canonical input on an isolated snapshot; keep all source prose private.
- [x] Clarify exact contiguous quotations, empty nonterminal evidence, unchanged-arc omission and exact continued-scene identity fields. Keep the instruction within the existing 4,200-character reserve.
- [x] Preserve the nonterminal invalid-evidence rejection test and the canonical request contract checks. Existing publication guards and repair budgets stay unchanged.
- [x] Verify the exact revised prompt through full isolated reconciliation and Director planning. The GLM 5.2 route completed four batches and accepted a plan in five calls without repair; the Utility route still reached an output cap.

## Task 4: Recover and release

- [x] Compare the current canonical reconciliation input using configured providers against isolated database snapshots; record only content-free outcomes.
- [ ] If supported by that comparison, change only the active session's explicit `director_reconcile` override, preserving its previous value for rollback.
- [ ] Review the complete branch, run required checks, open and merge a PR at the verified head, then deploy and publish the next signed release.
- [ ] Run normal reconciliation/reassessment with current revision and lease checks; verify an accepted plan and healthy service before reporting completion.

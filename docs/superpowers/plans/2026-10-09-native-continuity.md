# Native continuity implementation plan

> Implement inline with test-driven changes and an independent end-of-branch review. No live activation.

**Goal:** Native source-retention receipts, a reversible >=30%-capable candidate, and reproducible matched subscription measurements with automated blinded review.
**Architecture:** Pure codec, read-only native proof adapter, synthetic-native evaluation fixture, separately guarded live evaluator/reviewer. Default runtime untouched.
**Tech stack:** Existing Python, SQLite, provider transport, pytest; no new dependencies.
**Spec:** docs/superpowers/specs/2026-10-09-native-continuity-design.md

## Constraints
Preserve all source text/roles/order/multiplicity, recent dialogue and mandatory/current input. Exact scope and coverage revalidation. Synthetic-only model experiments. Max 24 physical calls, 300,000 input/24,000 output tokens, no paid overage/retry/fallback. Human approval cannot be inferred from automated review.

## Review focus
Tampered dictionary or inserted instruction must reject; repeated identical utterances remain distinct occurrences; Unicode and ambiguous negations round-trip exactly; same-text cross-story/stale-reader receipts fail; publication/review metadata must never reveal production transcripts or model keys.

## Task 1: Constructive native evidence preservation
- [ ] RED tests for codec round-trip, duplicate occurrence roles, mandatory/recent equality, Unicode, malformed refs, limits and no-savings fallback.
- [ ] Implement bridge/context_history_codec.py with pack_history()/unpack_history().
- [ ] RED native SQLite tests for healthy accepted source intervals, current incarnation, reader/rewrite change and tampered receipts/candidates.
- [ ] Implement bridge/context_native_receipt.py: capture_native_history()/verify_native_candidate(), deriving authority from DB state rather than caller manifests.
- [ ] Verify targeted tests, architecture/source-size gates and commit.

## Task 2: Frozen workload and trial planning
- [ ] Add six predeclared synthetic stories through existing native SQLite schema and accepted Summary worker; controls are mandatory.
- [ ] Implement tools/native_context_fixture.py and tools/evaluate_native_context.py to build same-setting baseline/candidate prompts through the real story builder.
- [ ] Verify all native receipts and exact source reconstruction; rerun untouched old replay and local production read-only shape study without exporting text.
- [ ] Commit the generated trial plan and immutable workload before any provider dispatch.

## Task 3: Bounded experiment and blinded review
- [ ] RED tests for quota admission, complete physical-attempt accounting, reviewer label exclusion, order-swapped scores and unknown/failure gates.
- [ ] Implement tools/native_context_trial.py and tools/native_context_review.py using explicit subscription-only guarded transport, no retries, atomic checkpointed results.
- [ ] Execute twelve matched story calls and twelve blinded reviewer calls inside the predeclared global budget; keep model/settings equal and review labels secret until judgments lock.
- [ ] Save synthetic evidence and human-review packet separately from unmasking/report; calculate aggregate/weighted/per-case and total accepted-work input.
- [ ] Run full CI, independent code review, merge only green, update issue #421 and report achieved scope and any unmet approval gates. Leave production unchanged.

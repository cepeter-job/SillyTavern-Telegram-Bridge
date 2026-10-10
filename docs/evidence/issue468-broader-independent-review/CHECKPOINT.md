# Broader independent review checkpoint - 2026-10-10

## Purpose and status

Prepare the next human gate under #468 after the separate 16-pair agency study was completed in merged #482 and intake #494. This checkpoint provides a new presentation of the **existing 38 profile/history pairs**, not another provider experiment or a quality verdict.

Independent human review and owner corroboration **remain pending for these 38 pairs**. The earlier signed 16-pair agency review and its owner confirmation remain complete; they are not reused as approval of this broader packet.

## Preserved evidence

- Source snapshot: `2fef95009fd63e707da3a519585bd9d8a7725b7c`; original generation runtime: `4e3b31af8c6f8c9f81d97cf09051a2ba31477b03`.
- Original coverage: four native profile comparisons with eight cases each, plus six hybrid-history comparisons.
- All 76 original story outputs were verified against their recorded provider observations. Every common canon, required fact, forbidden inference and review focus is retained verbatim.
- The original 152 requests, 235,024 input tokens and 31,483 output tokens remain unchanged. **Zero new inference requests** were made for this checkpoint.
- 38 new unpredictable pair IDs, shuffled order and exactly 19 randomized A/B swaps. The fresh correspondence/condition map is retained privately on the VPS with restrictive permissions; it is excluded from the repository and reviewer archive.
- The original eight scoring axes are retained: 608 blank numeric cells. Six explicit error categories add 456 blank flags, exact-quote fields, justification and independent reviewer ID.
- Score anchors and error definitions are specified before the new review. They do not rewrite or retroactively rescore the first review.

## Reviewer handoff

Give a human reviewer **only the six files in [REVIEWER](REVIEWER/)** or the matching six-file archive, `Issue468_Independent_38_Pair_Review.zip`. Start with [reviewer instructions](REVIEWER/REVIEWER_INSTRUCTIONS.md); return the [completed CSV](REVIEWER/HUMAN_SCORECARD.csv) and [declaration](REVIEWER/reviewer_declaration.json).

Archive SHA-256: `ddaa9e7590582d9101bd013899315b9663c6c1899dc11d0f6f5e494b8b12e52b`.
Packet raw SHA-256: `5889fdbc105481494ddc61deff9bbee38c089b504c32bde997817c557ded1a60`.

The reviewer must be a real human, different from the original 38-pair grader, with no access to the implementation, condition assignments or previous 38-pair ratings. These outputs were previously published; fresh IDs cannot prevent deliberate matching against public historical maps. Clean-room provenance is therefore a separate requirement, not a property an automated checker can certify. Keep maintainer reports and historical evidence away from the reviewer until scoring is complete.

## Verification

[Public remapping audit](PUBLIC_REBLIND_AUDIT.json) records source checksums and output provenance without exposing correspondences. [Puntoap WSL validation](WSL_VALIDATION.json) passed in Ubuntu-24.04 as `ci-runner` using a separate manual artifact audit. The scheduled one-shot runner was already handling another job and was not interrupted.

The WSL audit independently compared the complete canon/output multiset with the hash-pinned original packet, checked all six archive files and their hashes/CRC, validated the 38-row inventory and entirely blank forms, and rejected six deliberate corruptions: changed response, dropped canon, duplicate pair ID, condition metadata, prefilled score and mismatched CSV pair ID. This is artifact-integrity verification, not a newly run application regression suite.

## Intake and decision order

1. Preserve the submitted CSV/declaration bytes and actual receipt timestamps.
2. Validate all 38 IDs/order, 608 integer scores (1-5), 456 flags (Yes/No/Unclear), exact quotations in the corresponding output, preferences, justifications, a consistent reviewer ID and a genuine nonfuture UTC completion time. Any hard error must have exact evidence; resolve Unclear/Unresolved items before a winner claim.
3. Obtain separate owner corroboration of independence for this specific packet. The 16-pair checker/declaration is not the 38-pair intake validator.
4. Only after provenance and scoring are locked, privately apply the retained remapping, assess hard errors and critical-axis regressions, and report condition-level aggregates with uncertainty. Preserve every score and tie; do not overwrite earlier observations.
5. Record a bounded maintainer decision. This single-sample synthetic pilot cannot prove production equivalence or select a general winner by itself.

Actual Telegram client-button acceptance, representative long-scene causal/knowledge/branch continuity, and total story-plus-helper/repair/fallback input accounting remain separate gates. A future expanded agency study needs its own frozen protocol before inference. Production defaults and history pruning remain unchanged.

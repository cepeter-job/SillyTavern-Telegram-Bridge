# Post-v0.3.019 validation results

Recorded 2026-10-09T10:39:32.608197+00:00. Frozen native runtime: `4e3b31af8c6f8c9f81d97cf09051a2ba31477b03`.

## Decision

**Keep the existing production default and keep hybrid-history pruning disabled.** The automated experiments are complete, but they do not establish a reliable profile winner, semantic equivalence, independent human approval or lower total production accepted-work input.

## Completed model experiment

All **152/152 model requests** completed: 76 story continuations and 76 automated judge responses. Provider-reported logical usage totals **235,024 input tokens** and **31,483 output tokens**. No failed model request or unknown logical input/output reading was recorded. Allowance/catalogue GET checks are separate from this inference count.

One included NanoGPT subscription model, `z-ai/glm-5.2`, was used with paid overage disabled and quota checked before admission. No model repair, retry, provider fallback, humanizer or translation-helper request was added. The public observation copy omits private account quota; all experiment request/response text is synthetic.

### Writing-profile input cost and review coverage

| Profile | Baseline input | Profile input | Extra input per story | Valid automated reviews | Both-order usable cases |
|---|---:|---:|---:|---:|---:|
| Scene Continuity | 4,585 | 6,969 | 298.0 | 7/16 | 2/8 |
| Grounded Dialogue | 4,585 | 6,825 | 280.0 | 7/16 | 2/8 |
| Ensemble Focus | 4,585 | 6,897 | 289.0 | 9/16 | 4/8 |
| Magical Realism | 4,585 | 6,777 | 274.0 | 7/16 | 3/8 |

These are short synthetic requests, not average production session sizes. Added instructions naturally consume tokens; the extra-token figures are more useful here than extrapolating the percentage overhead to long live stories. Each candidate has its own matched baseline sample, not a selectively reused favorable baseline.

### Automated preferences — not human approval

| Profile | Baseline preferred in both orders | Candidate preferred in both orders | Tie in both | Order disagreement | Incomplete/unusable pair |
|---|---:|---:|---:|---:|---:|
| Scene Continuity | 0 | 0 | 1 | 1 | 6 |
| Grounded Dialogue | 0 | 0 | 0 | 2 | 6 |
| Ensemble Focus | 1 | 0 | 1 | 2 | 4 |
| Magical Realism | 0 | 0 | 2 | 1 | 5 |

The model returned **1 structurally invalid automated review(s)** and **34 review(s) with non-matching exact-quote evidence**. Those responses still count toward measured token use, but do not become valid quality votes. Empty evidence also remains unverified. No invalid review was repaired, discarded from accounting or selectively rerun.

For example, the same Scene Continuity cafe output is treated as acceptable connective narration in one order and a user-agency violation in the other. The Grounded Dialogue cafe pair also changes agency judgments when reversed. A nonblinded analyst spot-check confirms this inconsistency; it is not independent human scoring. Exact quote matching alone cannot guarantee a sound semantic judgment.

## Hybrid-history measurements

| Case | Baseline input | Candidate input | Reduction | Selector result |
|---|---:|---:|---:|---|
| unique_dialogue | 3,351 | 2,154 | 35.72% | semantic_review_required |
| commitment_dense | 4,055 | 4,055 | 0.00% | all_history_protected |
| indonesian | 4,205 | 2,608 | 37.98% | semantic_review_required |
| unsupported_script | 3,297 | 3,297 | 0.00% | unsupported_older_script |
| short_history | 1,142 | 1,142 | 0.00% | insufficient_older_history |
| large_character_card | 7,570 | 6,373 | 15.81% | semantic_review_required |

Aggregate provider-reported story input: **23,620 → 19,629 tokens**, a **16.90% reduction** including all six cases. Equal-case-weighted reduction is **14.92%**. The 30% aggregate story-input target is **not met**.

No-savings controls remain in the denominator. Identical-payload controls can still produce different stochastic prose; their preferences cannot be attributed to history selection. Native Summary source/checkpoint receipts authenticate processing, not exhaustive extraction of all causal facts. Production helper, repair and recovery costs were not simulated with real provider calls, so this trial does not establish reduced total accepted-work input.

Hybrid automated comparison consensus: {"baseline": 0, "candidate": 2, "tie": 1, "disagreement": 2, "unavailable": 1}. Independent human review remains pending.

### Read-only live check

The two inspected live histories both retained the full baseline: 73 source rows because all history was protected, and 15 source rows because native evidence was missing or changed. Both showed zero estimated reduction and zero production writes/model calls. This diagnostic uses history-only estimates, not reconstructed full production prompts. See [live metadata](hybrid-live-recovered.json).

## Acceptance and release housekeeping

Recovered native Telegram `getMe`, `getWebhookInfo` and `getMyCommands` checks passed. The targeted acceptance suite passed 179 tests, evaluator/transport/reporting/fake-wire tests passed 31 tests, and additional hybrid/history/updater checks passed 65 tests. These suites overlap and must not be added together. Manual Telegram button-click acceptance was not performed.

PR #474 backfilled versions 0.3.007–0.3.019 and preserved the old changelog byte-for-byte. After checking the recovery bundle, metadata, refs and all 48 archived assets, 12 older releases and tags were retired. The cleanup receipt records only the original signed v0.3.019 remaining; no tag was re-signed. Private backups were retained. See [retention receipt](release-retention.json).

This evidence work changes offline tools/tests/docs only. It does not select a writing profile, edit a production story, deploy unsigned source or turn on context pruning. The separate #467 codec and #475 statement-selection work is not evaluated or modified by this trial.

## Artifacts and reproducibility

- [Frozen plan](frozen-trial-plan.json) and [protocol](PLAN.md): committed before inference.
- [Provider observations](provider-observations.json) and [final request payloads](provider-requests.jsonl): every model call, including unusable reviews.
- [Machine-readable results](RESULTS.json) and [test verification](test-verification.json).
- [Human review packet](HUMAN_REVIEW.md) or [JSON packet](human-review-packet.json): 38 anonymous pairs, complete canon and empty score forms. Reviewers should not open the separate anonymous-pair map or automated results before scoring.

Offline result reconstruction makes no model calls:

```bash
python tools/postrelease_report.py --plan docs/evidence/post-v0319-validation/frozen-trial-plan.json \
  --state docs/evidence/post-v0319-validation/provider-observations.json --output /new/output/directory
```

The runner is POSIX-specific because it uses file locking. Snapshot file hashes and the request/usage audit are retained. Any new sampling, model, prompt or judge procedure requires a new frozen experiment, not an alteration of these observations.

### Frozen-generation checkout

The exact v0.3.019-generation code and all completed evidence are preserved at commit `f35e2090d584a99c10fab8bbca6c3ecbb55a3175` (the pre-inference protocol itself was committed at `a05bc9a56cb327c1350c9cadf6702ecef4de4ec8`). Later `main` commits, including the separately merged statement-selector PR #475, are **not** covered by these model measurements. Use that pinned checkpoint—not a later working tree—when reconstructing original generation payloads. The saved runner deliberately rejects a frozen plan when its source-file hashes no longer match. Do not rewrite those hashes or restart the already-completed trial to bypass that check. Offline reporting from the recorded observations requires no provider request.

## Remaining gates

Independent blinded human review, actual client-button acceptance, representative production causal-continuity evidence and lower total story-plus-helper accepted-work input are not completed here. The completed experiments provide evidence for those decisions, not permission to bypass them.

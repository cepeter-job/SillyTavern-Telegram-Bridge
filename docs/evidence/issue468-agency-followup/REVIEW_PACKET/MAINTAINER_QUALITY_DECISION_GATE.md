# Maintainer-only decision gate — Issue #468 native writing

**Do not give this document to the blinded reviewer before their submission.** Reviewer-facing instructions are in [HUMAN_QUALITY_APPROVAL_TASK.md](HUMAN_QUALITY_APPROVAL_TASK.md).

## Established evidence, not sign-off

A source-frozen experiment compared 16 matched A/B native writing continuations in 32 NanoGPT requests. Old prompt provider input: **9,261**; revised prompt input: **10,093** (**+832**, mean **+52** per revised response), provider output 5,667. All 32 physical calls are recorded. Results and original packet SHA256 are in [EVIDENCE_AUDIT.json](EVIDENCE_AUDIT.json); private unblinding assignments remain off GitHub.

One earlier user-provided sheet scored the 16 pairs and identified **three hard-error responses on the baseline and zero on the updated wording**. That sheet did not establish a different independent reviewer, signature, or A/B preferences. Treat as informative but **not** the independent human quality gate.

## Approve a completed second human review for analysis, not automatic rollout

- [ ] A *different human* from the original rater and the implementer completes and signs the declaration, and their provenance is independently verified off-ChatGPT.
- [ ] An unchanged blinded 16-pair packet was used, with 16 unique pair IDs and complete Yes/No/Unclear ratings for both outputs in all five dimensions.
- [ ] Exact error quotes occur in the corresponding frozen A or B outputs. Every pair has a justified A/B/Tie/Unresolved preference and consistent reviewer ID.
- [ ] Preserve the signed response and hash it **before** unblinding. Verify the signed timestamp uses real UTC (not local WIB mislabeled with `Z`) and is not in the future; never silently correct someone else's signature. Do not publish identifiable reviewer information in the public PR.
- [ ] Run the structure-only validator below. Any **Unclear** or **Unresolved** item is examined by the owner rather than silently treated as No.
- [ ] Only after recording the blind judgments, privately unblind the mapping and count failures by variant; investigate causal/agency/knowledge errors individually.
- [ ] Document a human decision with reviewer provenance: **APPROVE MORE TESTING / REJECT / INCONCLUSIVE**. Do not use “production approved” from this small synthetic sample.
- [ ] Keep native default, profiles, live sessions and pruning unchanged until real Telegram button acceptance, representative long-story causal/reader-knowledge proof, and total provider story+helper accounting pass.

## Reproducible submission structure check

```bash
python tools/issue468_human_gate.py \
  --packet docs/evidence/issue468-agency-followup/REVIEW_PACKET/HUMAN_REVIEW.json \
  --scorecard /private/location/completed_human_scorecard.csv \
  --declaration /private/location/signed_reviewer_declaration.json
```

The checker pins the exact original packet bytes; it rejects missing, modified or duplicate pairs, unsupported ratings, invalid/missing quotes and incomplete declarations. It does **not** access the private assignment map or claim that human independence and quality are proven.

**Required final record:** reviewer code, attestations independently corroborated or not, scored-sheet SHA256, total unresolved/critical findings after unblinding, per-profile fail counts, narrative-quality rationale and next action. Issue [#468](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/issues/468) remains the roadmap; draft [PR #482](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/pull/482) is evidence and tooling, not activation.

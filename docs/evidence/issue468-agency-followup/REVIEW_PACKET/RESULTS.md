# Issue #468 — Fresh matched NanoGPT agency study

This is a fresh **16 paired-case / 32 physical generation request** synthetic test, not a production rollout or a human-quality approval.

- Matched model: NanoGPT included subscription z-ai/glm-5.2.
- Identical native card, history, user input and sampling for each pair; the **only** prompt difference is the added user-agency clarification from PR #479.
- Temperature 0.7, maximum 800 output tokens and common 140-word narrative scope.
- No extra generation, humanizer, language rewrite, fallback, retry, or repair call.
- Provider request accounting: **32/32 completed**, 0 failed, 0 unknown logical usage.
- Provider input tokens: baseline **9,261**, candidate **10,093**, total **19,354**.
- Total provider output tokens: **5,667**.
- Scenarios: 10 unquoted conversational acts, 3 verbatim-quoted controls, 3 no-speech/steering/physical controls.
- The response content and A/B assignment are **blinded** to the human reviewer. Full results and private mapping must not be shown to the reviewer until scoring completes.
- **No independent second human scores were obtained here.** No effectiveness or failure-rate improvement claim may be made until independent blind scoring; a fresh matched A/B is necessary but insufficient.
- The original 152-request experiment and original human review are not rewritten.
- Production default and history-pruning settings remain unchanged.

## Reviewer files

- [Blinded review packet](HUMAN_REVIEW.md)
- [Blank scorecard](HUMAN_SCORECARD.csv)

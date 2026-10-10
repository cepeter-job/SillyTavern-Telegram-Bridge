# Agency study reporting erratum — 2026-10-10

This note corrects descriptive metadata; the frozen protocol, request bodies, provider observations and blinded packets remain unchanged.

## Output-token cap

`FROZEN_PLAN.json`'s `method` description and `REVIEW_PACKET/RESULTS.md` say **800** maximum output tokens. The executable, pre-dispatch request bodies contain **`max_tokens: 1000` in all 32 requests** (16 baseline and 16 candidate). The actual experiment therefore used a **1,000-token request cap**, temperature **0.7**, and the same 140-word narrative scope for both variants.

The descriptive 800-token statement was inaccurate. The request cap was identical across the matched pair; the sole prompt-content difference remains the native agency clarification. The observed totals remain **9,261 baseline input tokens**, **10,093 candidate input tokens**, and **5,667 output tokens**, with **32 completed requests**, no retry, repair or fallback, and zero additional inference requests for review or this correction. A cap is not an observed output-token count.

Authoritative source: [frozen plan at its pre-inference commit](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/blob/3ff373847786ced87fb4412495df27da3d156c4a/docs/evidence/issue468-agency-followup/FROZEN_PLAN.json). Do not edit the frozen `method`, report generator or evidence files to hide this inconsistency, and do not repeat the experiment to repair reporting text.

## Review checkpoint

The final amended independent scorecard was preserved and structurally validated before unblinding. It contains 16 complete pairs, two flagged categories in one response, and no unresolved items. The owner separately confirmed reviewer independence on 2026-10-10. Private unblinding and adjudication subsequently completed after access was restored. The independent review found one baseline hard-error response, no updated-wording hard-error response, and preferences of six for each condition with four ties. The [completed review record](ADJUDICATED_REVIEW_2026-10-10.md) documents provenance, verification and the bounded further-testing disposition; overall narrative preference remains inconclusive.

No production-quality, winning-profile, default-policy or history-pruning approval follows from this checkpoint.

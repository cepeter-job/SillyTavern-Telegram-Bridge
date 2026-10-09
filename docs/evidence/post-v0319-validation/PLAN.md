# Frozen post-v0.3.019 experiment

Plan digest: 27b6262f810a56d4e17cfcd54c4b56df60910a66be4f98e3f6b6daf6e77862aa. Frozen before provider generation.

Four independent eight-case profile comparisons use Off versus each optional writing profile. Each has 16 story generations and 16 order-swapped automated reviews. A separate six-case native hybrid-history comparison uses 12 story and 12 review requests, retaining protected-history and unsupported-script/short-history negative controls. Full baseline prompt canon is supplied to both orders of automated review. One sample per condition/case; no general quality or production-savings inference.

Maximum 152 model POSTs, 3,000,000 input tokens, 152,000 allocated output tokens; ten independently bounded blocks with at most 16 requests/300,000 input/16,000 output each. GET allowance checks are separate and do not generate text. NanoGPT included subscription model z-ai/glm-5.2 only, no paid overage, fallback, repair or retry. Requests are sequential, quota checked before each admission, and a pending/unknown-usage failure stops the driver rather than being silently replayed. The existing tested transport/limits are reused from the preserved #467 evaluation tools without importing its codec or activating runtime changes.

Each story request has the same 160-word scope and 1,000-token output ceiling; sampling temperature 0.7. Judge temperature zero with strict JSON object. No translation/humanizer/helper pass. Provider usage counts logical input including cache; unknown cache/reasoning details remain unknown. Automated judgments and exact quotes are preserved, including disagreement, invalid JSON or failures. Independent human review remains required before any profile-default or pruning activation.

The latest inspected active session optional System Prompt was Off. All cards/history in these trials are synthetic and no production transcript is sent. The hybrid source fixture uses a deterministic test Summary provider to obtain real native source/checkpoint receipts; those receipts authenticate processing but do not prove exhaustive semantic coverage. Full native system, language, agency and post-history policies are included equally in baseline/candidate.

Acceptance recovery: 179 targeted regressions passed; native getMe/getWebhookInfo/getMyCommands succeeded. These do not claim human button-click acceptance. The earlier outage/EndpointPolicyError artifacts are retained as historical failed probes.

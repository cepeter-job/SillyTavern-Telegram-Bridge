# Issue #468 — Pre-registered matched agency prompt study

User authorization: fresh matched NanoGPT trial and independent human adjudication.

## Boundary and scientific question

Assess whether the prompt clarification merged in PR #479 reduces *invented verbatim user dialogue* after an **unquoted** user conversational act, without inducing new user actions, knowledge leaks, branch violations, or format failures. This is a bounded synthetic exploratory study, not production activation or a proof of complete continuity.

The old native narrative policy (before PR #479) and updated policy differ by **one appended agency clause**, with all other system/card/history/user content, model, temperature and output allocation identical within each pair. The harness asserts this exact invariant and source hashes before any provider call. No prior provider outputs are re-used as new experiment observations.

## Frozen inventory

- **16 synthetic cases:** 10 unquoted speech acts (including Indonesian), 3 verbatim-quoted speech controls, and 3 no-speech/action/off-screen controls. Cards/history derive from the earlier immutable synthetic roleplay fixture; none comes from the user's production stories.
- **32 model POSTs total:** 16 old-policy and 16 updated-policy continuations, with order alternating per pair. Each body includes the actual assembled native prompt including persona, card, policy, language, history, current user request and matched narrative-length instruction.
- **One NanoGPT included subscription model:** `z-ai/glm-5.2`, temperature **0.7**, max completion tokens **1,000**, streaming off, independent session IDs not required for non-persistent completion API. No separate automated judge, translation helper or humanizer.
- **Budget caps:** at most 32 physical model calls, 600,000 reported input tokens and 32,000 reserved output tokens. Two 16-request blocks; no parallel requests. Preflight requires active NanoGPT subscription, included model catalogue, `allowOverage=false` and at least 250,000 remaining allowance tokens. Per-request quota GET before dispatch.
- **No retries, repairs, continuations, fallback, or paid overage.** Usage is recorded for every attempted generation. Ambiguous/unknown usage or interrupted pending request stops the experiment; the saved state is never automatically replayed.
- Freeze source hashes, request bodies, schedule, model/options and code **on GitHub before inference**. Changes to source after freeze stop execution.

## Human adjudication

A separately qualified **human reviewer** must score the new pair outputs, without seeing condition assignments, the current author's conclusions, earlier scorecards, or an unblinding map. The rendered packet contains full synthetic canon, A/B continuations, required facts and forbidden inferences. The map is saved privately on VPS, not published. A blank reviewer CSV has **Yes/No/Unclear** fields for invented user words, unrequested user actions, knowledge leaks, branch/causal violations, format errors, exact supporting quotes and overall A/B/Tie/Unresolved preference.

The assistant and automated models cannot impersonate an independent person. If no independent reviewer is connected/provided, mark adjudication **pending**, publish the packet for human scoring, and do not claim quality certification. Previous user-provided human scoring is an earlier separate first pass; it cannot assess these new generations retroactively.

## Conclusions permitted

Provider-reported logical input/output tokens and completed physical request counts are measurable. **Error-rate improvement, semantic equivalence, new default, live pruning or 30% global optimization are NOT established without independent human scoring and representative full-workload tests.** Leave active SillyTavern v0.3.019 state, provider selection and live stories unchanged.

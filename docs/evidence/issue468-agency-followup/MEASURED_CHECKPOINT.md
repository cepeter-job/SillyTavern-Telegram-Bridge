# Issue #468 — Measured provider checkpoint (packet publication pending)

A fresh, pre-registered, source-frozen **16-case matched NanoGPT A/B trial** completed on 2026-10-09. This is a **verified measurement checkpoint**, not human adjudication or production activation.

- **32/32** physical `z-ai/glm-5.2` included-subscription generation calls completed; **0** failures, unknown logical usage, retries, repairs, paid overage, or fallback calls.
- **Old native agency policy:** 9,261 provider-reported input tokens in 16 generations.
- **Updated PR #479 agency policy:** 10,093 provider-reported input tokens in 16 matched generations, **+832 input tokens total** (~52 extra per generation).
- **All generated outputs:** 5,667 provider-reported output tokens, across all 32 calls.
- Test includes 10 unquoted user speech acts, 3 verbatim-quoted controls and 3 no-speech/physical/off-screen controls, with matched full native prompts differing only in the appended agency clause, identical model and sampling parameters, alternating order.
- Frozen generation plan SHA-256: `69ceed2e538b8c3d5f910fd5f7ca58354c7b78cc61904cb1edd1e3a84ddb5873`, source checkpoint commit `3ff373847786ced87fb4412495df27da3d156c4a`, runtime code `8f278d4b92391702c7f1af2464c6afc66e1a1091`.
- The on-VPS audit reconciled all 32 saved request digests with the frozen plan, complete provider usage accounting and 16 anonymous A/B outputs. Independent human scoring has **NOT** occurred.
- The actual fresh **16-pair blinded packet, blank CSV and evidence audit** were generated and checked on the VPS under `docs/evidence/issue468-agency-followup/REVIEW_PACKET/`. The remote VPS connection went offline during upload staging, so these files are **NOT YET in this draft PR**; do not imply otherwise. The raw provider state and A/B mapping remain private under `.superpowers`, not in GitHub.
- **Never rerun the 32 provider calls** merely to recreate the public packet. Restore access to the VPS and upload the existing checked files, without committing the private unblinding map.
- Do not conclude generative user-agency compliance improvement from token totals. The existing production native default, user stories, signed deployment and history-pruning settings were not changed.

Follow up by publishing the existing blind packet, obtaining genuine second-person blinded adjudication, and only then evaluating a production change. The work remains related to [issue #468](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/issues/468).

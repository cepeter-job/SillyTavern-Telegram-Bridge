# Issue #468 — Unquoted user-speech attribution follow-up

## Confirmed incident and root-cause boundary

The first completed blinded human scoring pass found three occurrences of **invented user speech** in the same synthetic quiet-cafe scenario. The latest authorized user text was "I thank Rowan and ask how the morning has been." No exact words were supplied. Three generated continuations nonetheless wrote a new direct utterance attributed to Ari:

- Original review pair 6 (Grounded Dialogue trial; baseline output B) scripts "Thanks for the seat" and attributes it to Ari.
- Original review pair 9 (Ensemble Focus trial; baseline output B) scripts "Thanks again" and attributes it to Ari.
- Original review pair 34 (Scene Continuity trial; candidate output B) scripts "Thanks for the seat" and attributes it to Ari.

These outputs and the complete source canon remain unchanged in [the original blinded human packet](HUMAN_REVIEW.md) and its frozen JSON source. No original model request or scored result has been replayed or rewritten.

The shared native narrative policy already prohibited invented user dialogue, and story rendering did not enforce semantic attribution. The failure is that the model **did not comply consistently with the general instruction**, specifically when the user *described* a conversational action but did not provide its verbatim words. No deterministic parser can safely recover those missing words from the prose; blindly deleting sentences or retrying a model could damage roleplay and consume additional subscription tokens.

## Minimal policy change

The shared narrative policy now says explicitly that unquoted user speech descriptions are **already completed**, that the AI must not *script, restage, or paraphrase* a new user line, and that only the user's exact quoted words count as established verbatim dialogue. It continues with AI-controlled characters rather than speaking for the user. It is shared by Story, Group and choices, including cases with late character-card guidance.

- No message-content rewriting, automatic model repair, retry, fallback or additional runtime state.
- No native writing profile is chosen or made the default.
- No changes to Light Novel response envelopes, speaker routing, canonical trackers or installed sessions.
- Frozen-review excerpts are covered by targeted regression tests; the tests verify the policy in the assembled prompt and the underlying incident evidence. **They do not prove future generative compliance.**
- Additional fixed prompt cost: **59 estimated input tokens** under the repository's message-estimation function, not provider-reported usage. A small future provider trial must include this overhead in its denominators.

## Second independent human review — protocol

The first reviewer gave 34 ties out of 38 pairs and 590 of 608 rubric scores were 5/5. A repeat 1–5 survey alone is unlikely to distinguish narrower agency violations. Use a **new reviewer** without access to the first scorecard, unblinding map, automated judge outputs or the known failure labels.

Provide only the [blinded human review packet](HUMAN_REVIEW.md) and [blank focused scorecard](ISSUE468_AGENCY_SECOND_REVIEW.csv). The preselected 12 anonymous pair IDs cover the four cafe handoffs and eight different agency, knowledge, branch, language and off-screen controls. Their presentation order is arbitrary; all A/B output strings and complete canon remain unmodified. The scorecard contains **no correctness labels or filled grades**.

For *each* A and B, record:

1. Did it attribute new dialogue to the user when the canon contains no exact words? **Yes / No / Unclear**.
2. Did it transform an attempted or already completed user action into a new action or commitment? **Yes / No / Unclear**.
3. Did it give any speaker knowledge they could not have obtained? **Yes / No / Unclear**.
4. Did it contradict a required fact, negation or the active branch? **Yes / No / Unclear**.
5. Did it break the required narration/dialogue or selected-language transport format? **Yes / No / Unclear**.
6. For any Yes, supply the **exact output quote** and the specific authorized canon it violates. Then record A/B/Tie/Unresolved and one sentence of reasoning.

Keep *uncertain* responses unresolved, not silently counted as pass or fail. Save the signed reviewer sheet before opening the mapping. The reviewer's independence must be documented; **a second generated judge response is not an independent human review**.

## Remaining evidence gate

The original 38-pair outputs predate this policy clarification. Even a second independent adjudication of them cannot establish that the *changed policy* prevents the failure. Before a signed production rollout claiming quality improvement, freeze a small matched NanoGPT subscription-only study of the same unquoted conversational acts with identical cards/history/sampling, balanced old/new order, every physical request counted, no paid overage, no hidden repair/retry/fallback, and independent scoring of its new outputs. Include quoted-user-speech positive controls and wrong-speaker negative controls.

Do not claim a fixed model failure rate, winning profile, production accepted-work saving, or independent review based on prompt-string assertions alone. Keep production defaults and history-pruning settings unchanged while these gates remain open.

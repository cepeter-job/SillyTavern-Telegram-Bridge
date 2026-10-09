# Independent human-quality review — native roleplay writing

**Assignment:** Review **16 pairs** of anonymous A/B story continuations for natural writing, established facts and boundaries. Both versions are unlabeled. This is a *review task*, not a certificate that the output is acceptable.

## Give the reviewer only these three files

- [16-pair blinded story packet](HUMAN_REVIEW.md) — full canon, latest user action, allowed facts, forbidden inferences, and outputs A/B
- [Blank human-review scorecard](HUMAN_SCORECARD.csv) — editable in Excel, Sheets or a CSV editor
- [Blank reviewer declaration](REVIEWER_DECLARATION_TEMPLATE.json) — signed only **after** the review

A reviewer must be a **real, separate person** who did not implement the writing changes or conduct the earlier evaluation. They should have no access to prior scores, hidden output assignments or implementation details before submitting the finished sheet. If these conditions are not true, state that openly and do not claim an independent review.

## What to score, for each A and B independently

| Scorecard field | Mark **Yes** when... |
| --- | --- |
| `invented_user_speech` | The output authors words for the user that were not explicitly established, including turning an unquoted `I thank them` into a newly spoken quote |
| `unrequested_user_action` | The output invents the user's gesture, reply, emotional conclusion, agreement, commitment or completion of an attempted act |
| `knowledge_boundary` | A character or narrator claims knowledge they could not know from established events |
| `causal_or_branch_error` | The output contradicts established facts, negations, causal links, reader knowledge or the active branch |
| `format_or_language_error` | The response violates the required action/dialogue styling, speaker attribution or selected language |

For each of these ten A/B fields choose exactly **Yes**, **No**, or **Unclear**. Do not use numeric ratings, guessing or unchecked blanks. If you select Yes, paste the **exact offending text** from that same output into `A_exact_error_quote` or `B_exact_error_quote` and explain which authorized fact it violates.

For every pair also select **A**, **B**, **Tie** or **Unresolved** in `preference_A_B_Tie_Unresolved`, and provide a meaningful reason in `reviewer_justification`. A tie means genuinely comparable outputs, not that you skipped them. Unclear and Unresolved are valid if you cannot decide.

## Finish and submit

1. Read and score **all 16 pairs** without looking up which output was produced by which setting.
2. Write the **same reviewer ID** in `independent_reviewer_id` for each row. An anonymous reviewer code is acceptable if the project owner can verify it privately.
3. Save the completed scorecard as a CSV with the **original column names and pair IDs**; do not delete any rows.
4. Complete `REVIEWER_DECLARATION_TEMPLATE.json`: truthfully set each statement to `true` or `false`, enter your reviewer ID, sign or identify yourself, and include the UTC review date. Do not claim independence if uncertain.
5. Return **both completed files privately** to the project owner, then refrain from viewing the concealed assignments until the owner confirms receipt.

A submission checker will verify all required fields, pair IDs and copied supporting quotes. **It cannot verify independence or decide narrative quality automatically**. Reviewer contact information should not be committed to a public repository.

**Return exactly:** completed scorecard CSV plus completed reviewer declaration JSON. No model calls or access to private story data are required.

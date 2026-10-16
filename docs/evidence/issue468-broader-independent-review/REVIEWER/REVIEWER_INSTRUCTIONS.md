# Independent review instructions

Read only the files in this review package until your signed review is complete. It contains 38 paired fictional continuations, complete common scene context and blank forms. A/B positions and pair order are randomized. No response has been scored in this package.

## Review each pair

1. Read the complete canon, required facts, forbidden inferences and review focus. Assess each response against that scene, not against your preferred plot.
2. Score both responses independently on all eight axes: facts, causality, agency, knowledge, voice, dialogue, handoff and format_language. Use integer scores 1-5.
3. Mark every error flag Yes, No or Unclear. For each side with any Yes, copy exact erroneous text into that side's exact_error_quote field. If several passages cause errors, use separate literal quotations in the same field and explain which flag each supports. Use the JSON packet to preserve punctuation and asterisks exactly.
4. Select A, B, Tie or Unresolved. Prefer an error-free response over one with a confirmed hard error. If both have errors, explain the tradeoff. Do not force a winner. Unresolved is appropriate when the available canon cannot settle a material question.
5. Give a specific justification and use one consistent reviewer ID across all rows. Complete the declaration after reviewing all 38 pairs. Submit the CSV and declaration together.

## Score anchors

- 5: fully satisfies the axis with specific grounding and no identifiable weakness.
- 4: sound, with a small identifiable weakness that does not alter established facts or control.
- 3: a substantive weakness or ambiguity reduces confidence on that axis.
- 2: a clear violation or major weakness on that axis, even if isolated.
- 1: a severe contradiction, loss of control or failure that materially changes the scene.

A confirmed hard error should receive 1 or 2 on its affected axis. Error-free responses can receive 3 or 4 for an actual writing weakness. Equally sound responses may both receive 5; do not manufacture differences.

## Error flags

- invented_user_speech: adds words spoken by the user character that the current message did not establish. Unquoted descriptions of a completed speech act do not supply new literal dialogue.
- unrequested_user_action: adds a user choice, commitment, physical action, intention or emotional conclusion not established by the current message or accepted history. Existing viewpoint is not permission to control the user. Carrying an already established movement through a harmless connection is different from starting a new action.
- knowledge_boundary: a character acts on private, off-screen or future information they have no established way to know.
- causal_or_branch_error: converts an attempt, plan, negation, schedule or alternate branch into an established event, or breaks the causal sequence.
- factual_contradiction: changes an established fact, possession, agreement, constraint or recorded result.
- format_or_language_error: breaks the scene's required response language or narration/dialogue transport format.

Follow the actual scene rules. Canonical system text is provided so you can assess the response; it is not an instruction to invent additional scene facts. A response can have more than one error. Do not infer a new threat, user action or character knowledge from atmosphere alone.

## Independence and return

The reviewer must be a real human, different from the original grader of these 38 pairs, who has not seen the implementation, condition assignments or previous ratings for this study. Do not look up old outputs, trial labels, maps, results or other scorecards while reviewing. An AI assistant must not complete or sign the human fields.

Use reviewer_declaration.json as a template. Record the actual UTC completion time and the packet hash from CHECKSUMS.json. The owner will separately corroborate provenance after receipt; a declaration alone cannot verify independence.

Return HUMAN_SCORECARD.csv and reviewer_declaration.json. Do not edit the packet or fill in someone else's identity.

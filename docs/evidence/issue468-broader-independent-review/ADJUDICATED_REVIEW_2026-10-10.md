# Independent 38-pair review — completed 2026-10-10

## Decision

**Retain the current native default and leave production history pruning disabled.** The independent review intake is complete. No optional writing profile is approved as a new default from this pilot. Approve further bounded history/agency testing only; this is a maintainer interpretation of the locked review, not a new human score or provider measurement.

The four optional profiles have **12 baseline preferences, 5 candidate preferences and 15 ties** across 32 pairs. Hard errors occur in **5 baseline and 6 candidate responses** across the full 38-pair set. Those pooled counts describe several different comparisons; they are not a statistical test of one candidate.

## Signed input and provenance

The final declaration was received at **2026-10-10T11:21:24Z** with human-declared completion **2026-10-10T11:12:45Z**. It matches the packet, reviewer identity and all clean-room attestations. The owner separately confirmed independence at **2026-10-10T06:19:52Z**; the existing attestation was bound to the final input without changing that original record or requesting another confirmation.

All **38 ordered IDs, 34 columns, 608 integer ratings, 456 explicit flags, literal evidence spans, affected-axis scores, preferences and justifications** were validated. There are no Unclear flags or Unresolved preferences. Row 11's category annotation separates two exact source quotations and is not itself source text. The scorecard was not edited by the assistant.

Final scorecard SHA-256: `ef07e556eb18c57ff0ef0e3c4c57985d411a53c7670d1bbce2c58848b99b3d75`.
Final declaration SHA-256: `23819acfbd423f24e9177c92bdb3dddbcf06bd5a46d274678702ad76990df91f`.
Packet SHA-256: `5889fdbc105481494ddc61deff9bbee38c089b504c32bde997817c557ded1a60`.
Private lock SHA-256: `1c58f09ca04eff1849f4df6dc139da1cbb61fdeaf61015f1810d2dc9306013bb`.

The input was locked privately at **2026-10-10T11:22:36.231973+00:00**, before condition-map access. All submitted file hashes remained unchanged after adjudication. The private remapping checksum, 38 original correspondences, 19 swaps, complete common canon and all 76 outputs were reverified against the hash-pinned original assignments and provider observations. Raw mappings and signed reviewer identity remain private; public aggregates are in [PUBLIC_ADJUDICATION.json](PUBLIC_ADJUDICATION.json).

Independence rests on the human declaration and separately accepted owner attestation. The original outputs/maps were already public; fresh IDs cannot automatically certify clean-room provenance. The human amended the review following source/rubric feedback; all prior submissions remain preserved. This is one independent scoring pass, not several blind replications.

## Preferences and hard errors

| Comparison | Pairs | Baseline preferred | Candidate preferred | Tie | Baseline hard-error responses | Candidate hard-error responses |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Scene Continuity | 8 | 4 | 1 | 3 | 1 | 2 |
| Grounded Dialogue | 8 | 2 | 1 | 5 | 2 | 1 |
| Ensemble Focus | 8 | 3 | 1 | 4 | 1 | 1 |
| Magical Realism | 8 | 3 | 2 | 3 | 1 | 2 |
| Hybrid history | 6 | 1 | 3 | 2 | 0 | 0 |
| All comparisons | 38 | 13 | 8 | 17 | 5 | 6 |

There are **16 Yes flags in 11 responses**; several responses have multiple flags. Baseline flags: 2 invented-speech, 2 unrequested-action and 3 format violations. Candidate flags: 1 invented-speech, 3 unrequested-action and 5 format violations. No knowledge-boundary, causal/branch or factual-contradiction flag was marked Yes by this reviewer. That does not prove the absence of such failures in production.

Scene Continuity has a candidate speech/action failure and more candidate format failures in this sample. Grounded Dialogue reduces the number of flagged responses from two to one, but still has candidate action and format flags in that one response. Ensemble Focus replaces a baseline speech/action failure with a candidate format failure; neither side is universally clean. Magical Realism has one additional candidate action classification and no reliable preference advantage. None has an error-free, replicated basis for default promotion.

## Critical axes

The table counts pairs where the candidate score is lower than its matched baseline score. These are ordinal score regressions, not necessarily confirmed hard errors.

| Comparison | Facts | Causality | Agency | Knowledge |
| --- | ---: | ---: | ---: | ---: |
| Scene Continuity | 2 | 3 | 2 | 0 |
| Grounded Dialogue | 1 | 2 | 0 | 0 |
| Ensemble Focus | 2 | 0 | 0 | 0 |
| Magical Realism | 2 | 1 | 2 | 0 |
| Hybrid history | 1 | 1 | 0 | 1 |
| All comparisons | 8 | 7 | 4 | 1 |

The only critical-axis scores of 1 or 2 are agency: two baseline responses and three candidate responses. Fact/causality/knowledge scores of 3 or 4 still indicate weaknesses or ambiguity and remain in the machine-readable histograms. Equal or improved mean prose scores do not cancel a confirmed error or establish continuity equivalence.

## Interpretation sensitivity

Row 17's reviewer rationale assigns the initial Thursday-if-weather-holds line to Mara, while the source assigns it to Rowan. The isolated `*"If."*` has no explicit speaker attribution. The human classifies it as spoken dialogue with a format error. The attribution note is preserved without rewriting human fields.

Row 29's train-specific question is classified by the human as an unrequested user-state assertion. An NPC clarification reading is also plausible. The primary results retain that signed classification.

A robustness check excludes both source-interpretation-sensitive pairs entirely while keeping every remaining score and preference unchanged: **36 pairs, 12 baseline preferences, 7 candidate preferences, 17 ties; 4 baseline and 5 candidate hard-error responses**. This does not replace the human review or supply counterfactual preferences. The decision to retain the current default is unchanged.

## Hybrid-history decision and cost

All six history pairs have zero human-flagged hard-error responses on either side. Preferences are **1 baseline, 3 candidate, 2 ties**. Separate strata matter:

| Stratum | Pairs | Baseline preferred | Candidate preferred | Tie | Lower candidate facts/causality/agency/knowledge scores |
| --- | ---: | ---: | ---: | ---: | --- |
| Input-reduced cases | 3 | 0 | 2 | 1 | 0 / 0 / 0 / 0 |
| Identical-payload controls | 3 | 1 | 1 | 1 | 1 / 1 / 0 / 1 |

The three critical-axis regressions in the full history table occur among identical-payload no-savings controls. Their stochastic output differences cannot be attributed to removing history. The three actual input-reduced cases provide encouraging bounded evidence, with no observed lower critical-axis score; three short samples cannot demonstrate representative long-scene causal, knowledge or branch preservation.

The original provider-measured story input remains **23,620 → 19,629 tokens (16.90%)**, including all six cases and the no-savings controls. It is below the owner's current **20%** target. The historical frozen experiment's 30% target is not retroactively rewritten. Story/helper/repair/fallback accepted-work cost remains unmeasured by this fixture. **Further testing only; no production pruning activation.**

## Measurement and integration boundaries

The original experiment remains **152 model requests** (76 story and 76 automated judge), **235,024 input tokens** and **31,483 output tokens**, using the frozen native runtime `4e3b31af8c6f8c9f81d97cf09051a2ba31477b03`. **Zero new inference requests** occurred during reblinding, intake or adjudication. Original provider observations, requests, maps and frozen protocol are unchanged.

This evidence PR changes documentation only. The earlier Puntoap WSL check verified packet artifacts and deliberate corruptions; it was not an application regression run. Puntoap was offline at final intake, so no new WSL run is claimed. Protected CI on the final evidence head must pass separately before merge.

The separate 16-pair agency study in merged #482 / completed #494 stays complete. It does not become another review of these 38 pairs. Representative long-scene continuity and full accepted-work accounting remain open under #468/#421. Earlier client-acceptance records remain scoped to their original actions; this review neither reruns them nor revokes the owner's prior Telegram acceptance confirmation.

## Next evidence step

Prepare a new frozen matched protocol focused on representative long histories and the specific speech/action/format failures found here. Include commitments, negations, causal links, private knowledge and branch boundaries; count actual story plus helper/repair/fallback provider input and retain no-savings controls. Require at least 20% matched aggregate story-input reduction, lower total story/helper/repair/retry/fallback input per accepted work, and independent review of those newly generated outputs before activation. No additional inference is started by completion of this intake.

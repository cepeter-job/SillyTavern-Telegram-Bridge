# Completed independent agency review — 2026-10-10

This records the final review of the 16-pair native-agency comparison. It supersedes earlier pending-review checkpoint descriptions; the frozen plan, provider state and reviewer packets remain unchanged.

## Provenance and verification

The final signed amendment was received at 04:19:35 UTC and preserved before unblinding. The owner separately corroborated reviewer independence at 04:39:38 UTC and authorized proceeding. Private unblinding completed at 05:08:45 UTC.

The original condition-map checksum matched the published audit. All 16 fresh-to-original pair correspondences, both outputs and associated canon matched; eight display positions were reversed. All 32 condition-labeled outputs subsequently matched the frozen provider state, with request-body digests and usage reconciled. Full maps, per-pair condition assignments and signer identity remain private.

Final CSV SHA-256: `3331f28aae6731de18ec1932651658961a18ed3661df4ee2961259b2ad0ac5d9`. The actual submission passed the structural checker. Owner provenance and private adjudication are separate from the checker's structure-only outputs.

## Independent human findings

| Measure | Old wording | Updated wording |
| --- | ---: | ---: |
| Hard-error responses | 1 | 0 |
| Invented user speech flags | 1 | 0 |
| Unrequested user action flags | 1 | 0 |
| Knowledge, causal/branch and format flags | 0 | 0 |
| Preferred responses | 6 | 6 |

There are four ties and no unresolved items. Both agency flags occur in one response; they are not two failing responses.

The signed error quotation is `"Thanks," *Ari says, settling into the chair across from Rowan.*` The prompt requests thanking Rowan and asking about the morning; the sitting action is outside that instruction. Both signed category labels are preserved.

## Bounded disposition

**Maintainer disposition: APPROVE FURTHER TESTING. Overall narrative preference: INCONCLUSIVE.**

The observed agency-error difference supports targeted follow-up. Six preferences for each condition and four ties establish no overall prose-quality winner. This small synthetic sample does not establish a general error-rate improvement or production quality. The owner authorized continuation and corroborated independence; this disposition is the assistant's maintainer assessment of the signed human review, not an invented additional human verdict.

This completes the agency-review intake in [#494](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/issues/494). It does not adjudicate all 38 pairs from the separate profile/hybrid study. Broader independent review, actual Telegram client acceptance, representative long-scene causal/knowledge/branch continuity and total story-plus-helper accounting remain open in [#468](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/issues/468).

## Accounting and code verification

The original 32 NanoGPT requests remain the only inference calls: baseline input 9,261, updated input 10,093, total output 5,667; zero failed/unknown-usage requests, retry, repair or fallback. Review, reblind and adjudication added zero provider requests. The [reporting erratum](REPORTING_ERRATUM_2026-10-10.md) records the actual 1,000-token request cap.

On code head `6f15804fa9e1c9adcd216f06e055aa2e0ee58b65`, all required protected checks passed in [CI run 38025378774](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38025378774). The owner's requested Puntoap WSL validation passed 5,176 tests, skipped 11, and passed 817 subtests on a local unpushed merge with main `3f7eac7033f691c573b33045017adb274fdabde2`; final revision freshness passed. An initial manual virtualenv-PATH omission was corrected before that full rerun. Final documentation changes require their own exact-head protected CI.

Production defaults, active stories and pruning are outside this evidence checkpoint. The recommendation does not authorize additional provider requests.

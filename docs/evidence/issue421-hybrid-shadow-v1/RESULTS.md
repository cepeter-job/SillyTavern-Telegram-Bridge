# Hybrid shadow implementation: results and limits

## Disposition

Action 2 is implemented as a **shadow-only proposal**, not an activated production optimization. Actual story dispatch remains the full baseline in every mode. The dispatcher rejects marked hybrid packets. Native source/checkpoint, archived-digest, reader, protected-text and fallback tests are separate from semantic quality approval, which remains false.

Implementation commit: `11d1a9f902d2ec019f780d5b500590a9a908331e`.
Base: `98570bff313a120bbb83b16279ac7a5af32c4605`.
Implementation digest (selector, native capture, runtime contracts and evaluator): `ad70c539be72d1a38892195abc6426328efe8bf40f4b795984f4ba3bbc9cdf0b`.

## Synthetic native-worker estimates

All six cases are retained with equal case weights. These are deterministic fixtures using the real native SQLite/durable Summary path and a local synthetic extractor. They validate behavior and accounting, not narrative equivalence. No HTTP/model request was made.

| Case | Baseline estimate | Candidate estimate | Estimated reduction | Decision |
| --- | ---: | ---: | ---: | --- |
| Unique dialogue | 3,304 | 1,886 | 42.92% | Shadow proposal; semantic review required |
| Commitment-dense history | 4,145 | 4,145 | 0% | All history protected |
| Indonesian dialogue | 2,957 | 1,717 | 41.93% | Shadow proposal; semantic review required |
| Unsupported-script history | 3,237 | 3,237 | 0% | Full-history fallback |
| Short-history control | 643 | 643 | 0% | Insufficient older history |
| Large character card | 28,054 | 26,636 | 5.05% | Card/system text retained in full |
| **Aggregate** | **42,340** | **38,264** | **9.63%** | **Below 30% target** |

The average case-weighted reduction is **14.98%**. Both aggregate and weighted results include the no-savings controls. The large-card case correctly lowers whole-prompt savings because mandatory character/world instructions are not pruned.

## Read-only live-history shape

The live diagnostic opened the existing SQLite database with `mode=ro` and resolved each existing card's reader. It emitted counts, reasons and estimates only—no transcript, Summary text, card/reader names or session identifiers. It uses existing history plus candidate reference overhead, **not a reconstructed complete production story prompt**.

| Sample | Existing turns | History baseline estimate | Candidate estimate | Result |
| --- | ---: | ---: | ---: | --- |
| 1 | 73 | 21,312 | 21,312 | Full history retained: 42 anchor turns plus protected neighbors/recent turns cover the whole history |
| 2 | 15 | 5,918 | 5,918 | Full-history fallback: current native evidence was incomplete or changed |

The first sample passes native source verification; it is not being rejected merely because historical Summary checkpoints have aged out of the native checkpoint ring. Archived windows retain their own canonical source/digest proof. The second sample is not made eligible by forcing Summary state or ignoring missing evidence.

**No live savings have been demonstrated.** The conservative whole-turn policy protects too much of the observed 73-turn history to reduce it. That finding is preserved, not hidden by removing controls or relaxing privacy/source checks.

## Verification

- 204 local regression tests and 65 subtests passed on the updated main base, covering native capture, hybrid selection, mode isolation, dispatch, compaction, post-history instructions and existing memory services.
- Mypy: no issues in 174 typed-surface files.
- Repository-wide Ruff lint/format, module-size, module-reference audit and dependency-direction checks passed; zero cyclic modules.
- Leak scan found zero high-severity issues. Existing unrelated lower-severity findings were not modified in this task.
- Source/authentication regressions were observed failing before fixes, then passing: missing native capture; forbidden hybrid dispatch; forged classification; archive checkpoint-retention mismatch; unsafe callback telemetry; report identity omitting runtime files.
- Production source/configuration/database were not modified by this work. Existing PR #467 remains at `53ab904e015ad9c1996293231498b0c62b6387e2`; its frozen measurements and human-review checkpoint were not overwritten.
- Review in this session was local code review and automated checks. Independent PR review and CI remain separately visible; this file does not assert their completion.

## Remaining approval work

The >=30% **provider-measured** production-representative target is not met by this report. No provider A/B calls, new blinded narrative reviews or deployment approvals are included in action 2. Accepted Summary provenance is not exhaustive causal-closure proof, and the prior dictionary experiment's human review does not approve this different candidate.

A later candidate may need more precise, explicitly source-supported causal spans rather than whole-turn lexical protection. That must be independently specified and tested; this implementation does not silently loosen the current fallbacks to manufacture a passing savings figure.

Machine-readable evidence: `synthetic-estimates.json`, `live-history-metadata.json`, and `verification.json` in this directory. Prior draft measurements remain in the private operator directory and are not overwritten by the CLI.

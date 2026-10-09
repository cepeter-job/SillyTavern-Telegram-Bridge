# Completed native context experiment: results and disposition

## Decision

The predeclared, subscription-only trial completed all **24 physical model calls**
with complete provider usage. The reversible source-retention candidate reduced
logical story-input tokens by **54.18% aggregate** and **38.46% with
all six cases equally weighted**, exceeding the numerical 30% threshold on this
synthetic workload. **The blinded automated quality gate failed: 3/6 cases
passed.** Human approval and production activation remain false.

This is not a completed production-efficiency solution. The workload deliberately
contains four repeated-context stories plus two mandatory negative controls. The
read-only live history diagnostic found **0% estimated savings** for the eligible
73-turn production history, and the unchanged original offline replay still
reported **0%** for its existing candidate. Neither result may be replaced by the
54% synthetic headline. The new codec has not been enabled or deployed.

## Preserved chain of evidence

Implementation was committed before dispatch (`025ccb0`); the exact synthetic plan
was then committed (`01cbc8b`). Completed measurements and the original locked
judgments were preserved in checkpoint `9c6ed11`. The implementation, source
snapshots, prompt identity, weights, generation settings, request order and rubric
were frozen before observing model outputs.

- Frozen plan: `809a8e5c7e4e4e26e10f433f33bbcdbb53cbd0e6fdb180f1ceaf582e34a8edb9`.
- Blinded judgment lock: `9643f627e5d490fae4df06e3162fc9686b16d87529cf9fa55c7b6c02e9361cd1`.
- All 24 attempts completed; no unknown-usage request, transport retry, repair,
  fallback or extra judge run was used.
- `evidence-manifest.json` authenticates the stored report, original attempt
  journal, synthetic prompts and separated human-review packet/unmasking map.
- `native-source-snapshots.zip` preserves all six synthetic SQLite sources. A clean
  restore matched every frozen snapshot hash and revalidated all native receipts.
- `state.json` and `report.json` were copied unchanged from the completed trial.
  Recomputing the report reproduces the same failed quality decision.

## What the native proof establishes

All six synthetic sessions went through the native SQLite source/checkpoint
pipeline. Their receipts verified **222 canonical source rows**, source hashes,
complete accepted source intervals, classified Summary state, reader scope and
session/rewrite identity. Exact decoding reproduces every original retained
character, chronological occurrence and speaker role. Repeated statements remain
separate events; non-history instructions, the first historical turn and recent
eight turns remain unchanged. The proof was revalidated from SQLite at request
boundaries and again after trial completion.

This is a constructive **source-evidence-retention proof**, including the causal
links, negations and promises present in those sources. It does **not** establish
that a stochastic language model interprets the encoded representation identically,
that an initial baseline contained every necessary fact, or that private source
material can be given to another reader. A structural retention proof is not a
semantic causal-equivalence certificate.

## Matched provider measurements

Both variants used NanoGPT's included `z-ai/glm-5.2`, the same per-case story
settings and output allocation, and the same canonical question and protected
non-history context. Story-generation presentation order was counterbalanced.
There was one stochastic pair per case; no repeated-seed confidence interval or
powered noninferiority inference is claimed. Logical prompt tokens include cache
hits; missing cache-specific detail is unknown, not fabricated as zero.

| Case | Baseline input | Candidate input | Reduction | Automated review | Identical prompts |
|---|---:|---:|---:|---|---|
| oath_negation | 7,912 | 3,409 | 56.91% | Fail | No |
| causal_branch | 7,922 | 3,419 | 56.84% | Pass | No |
| reader_unknown | 7,918 | 3,415 | 56.87% | Pass | No |
| indonesian_commitment | 11,245 | 4,481 | 60.15% | Pass | No |
| unique_history_control | 1,617 | 1,617 | 0.00% | Fail | Yes |
| short_continuation_control | 807 | 807 | 0.00% | Fail | Yes |
| **Total** | **37,421** | **17,148** | **54.18%** | **Fail** | — |

The candidate uses no additional runtime helper-model calls. Across both story
variants, story output was 2,593 tokens (baseline 1,269; candidate 1,324). The 12
separate evaluation reviews consumed 9,220 input and 1,937 output tokens. Total
physical experiment consumption was **63,789 input and 4,530 output tokens** for
24 calls. Allocating half the offline review input to each variant gives a
sensitivity comparison of 42,031 versus 21,758 tokens: **48.23%**.
This allocation is disclosed evaluation overhead, not a production cost claim.

The predeclared ceiling was 24 calls, 300,000 logical input tokens, 24,000 reserved
output tokens, 150,000 bytes per request and a 100,000-unit subscription reserve.
Every admission required an active included subscription and paid overage disabled.
The last admission observed 46,311,626 weekly input units remaining; this is a
recorded checkpoint, not a claim about the current account balance.

## Blinded review and why it did not pass

The 12 stateless judge requests received task, supplied canon, language and
anonymous A/B prose, not variant names, original prompts, dictionary metadata,
token counts or result hashes. Each case was presented twice with A/B order
reversed. All judgments were durably recorded and locked before unmasking. The
predeclared rule required no critical candidate violations and no worse than
-0.25 overall or -0.5 per dimension. It was not relaxed after seeing results.

Three cases passed: `causal_branch`, `reader_unknown`, and
`indonesian_commitment`. Three did not: `oath_negation`,
`unique_history_control`, and `short_continuation_control`. Recorded flags include
unsupported facts, knowledge-boundary errors and grounding deficits. The latter
two are **identical-prompt negative controls**: their differing outputs and scores
show stochastic/model-review variation even without encoding. They cannot establish
that the codec caused those deficits, but they also cannot be dropped from the
predeclared gate or treated as proof of equivalence.

Post-hoc limitation: the supplied judge canon did not repeat all shared protected
background from the generation prompts. Some judgments called the inspection
procedure or archive setting unsupported even though these appeared in the shared
generation context. One judgment also treated a broken seal as equivalent to an
opened box. These observations motivate a better-calibrated, source-complete
review protocol; they do **not** erase the locked failures or convert this trial
into a pass. The exact original prompts, output pairs and judgments remain
available for independent assessment.

## Human review and next decision

`human-blinded-review.md` and `human_blinded_review.json` contain the anonymous
packet. A reviewer should assess them before opening `unmasking_key.json` or the
unblinded `state.json`. No human review has been performed or recorded. Because
the original packet uses the same limited review canon, a next preregistered study
should supply complete relevant shared canon identically to both labels, calibrate
the reviewer on identical-prompt repeated samples, and use a broader workload
matching actual production repetition before any activation proposal.

The code/evidence PR remains an evaluation checkpoint, not a runtime rollout.
Further model spending would require a new explicit, separately preserved
experimental plan. No extra calls were made to obtain a passing result.

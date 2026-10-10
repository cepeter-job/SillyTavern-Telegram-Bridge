# Role-preserving extractive context v2 — offline experiment

Issue #421. Base: e4be1f0 (merged #467, #475 and #476). No production
activation, automatic model calls, release or runtime import is included.

## Intended outcome and constraints

Investigate lower full-prompt input while protecting narrative continuity.
The 30% target is a gate, not permission to remove mandatory evidence.
Do not rewrite the original experiments or their frozen results.

## Candidate

Reuse #475 source-aligned statement selection and #467 native baseline receipts.
Keep every user turn, first source turn, last eight or more source turns,
quoted speech plus neighboring statements, and unparsable material unchanged.
Other older assistant turns keep selected exact substrings in their original
roles and chronological positions. Explicit omission markers separate gaps.
Keep every non-history message unchanged, including fixed system/card/world,
summary, current request, and any optional payload already in the baseline.

Unlike #475, no JSON reference replaces speaker roles and no historical archive
Summary is appended to the prompt. Native archives still authenticate source;
they are not an unlimited extra prompt payload. This does not prove omitted
text was semantically redundant. All-source preservation and semantic
continuity remain explicitly false.

Recapture native source, Summary, reader and branch state before returning the
preview. Bad or stale evidence falls back. Changed messages reuse the existing
shadow-only marker, rejected by normal dispatch in all modes. The full baseline
is always the returned dispatch prompt. No production runtime module imports the evaluator.

## Alternatives evaluated

Increasing the old Summary bounds did not yield live savings. Dictionary
encoding was previously efficient only on repetition-heavy synthetic fixtures
and failed its quality gate. A finer sentence-boundary probe with quote/format
protection exceeded mandatory evidence bounds and was not adopted. None of
these experiments alters the preserved historical measurements.

## Validation and acceptance

Regression tests cover roles, user agency inputs, first/recent turns, quoted
speech, unsafe markup, source mutation, Summary invalidation, changed readers,
negative controls, private-data-free metrics, no writes and dispatch rejection.
Report complete declared synthetic prompts separately from partial live local
reconstructions. Do not treat character estimates as provider usage or an
incomplete prompt reconstruction as a production benchmark. No selective case
or fallback exclusion from aggregate denominators.

Provider A/B requires a viable, precommitted candidate and bounded plan. The
independent blinded narrative gate requires actual separate human scores;
source receipt checks and self-review cannot satisfy it. Any new model sampling
must preserve earlier failed observations rather than overwrite or replay them.


## Screening disposition

Preserve this implementation on a separate research branch. Do not merge it as
a production optimizer or spend provider tokens to relabel a failed screening
result. Complete declared synthetic and partial local-core results must remain
separate. Stronger user/quote preservation is deliberately more conservative
than the old statement-packet experiment; lower savings are not a quality claim.

The implementation fingerprint includes every bridge Python module and the
specific evaluator/fixture files. Replay is bound to that code, not merely a
mutable branch name. Reports refuse overwrite; private source text is never
part of published metadata. A production-quality assessment requires an exact
full prompt capture and genuinely independent output review, neither of which
can be replaced with these estimates.

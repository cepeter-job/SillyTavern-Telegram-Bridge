# Context efficiency implementation specification

Implementation scope follows [issue #421](https://github.com/cepeter/SillyTavern-Telegram-Bridge/issues/421).
The October 8 instruction authorizes implementation, pull request creation and
merge. Deployment, paid synthetic comparison and activation remain separate.

## Outcome

Attribute prompt input, remove proven redundant derived evidence and reduce one
helper's serialization overhead while preserving the existing story model,
settings, output allowance, character instructions and reader boundaries.
Thirty percent lower provider-reported story input is an evaluation target, not
a runtime cap or a claimed result. Total accepted-work input includes every
story, helper, failed repair and fallback attempt. Missing usage stays unknown.

## Architecture and invariants

One captured reader scope and locally revalidated canonical evidence feed a
pure selector before memory channels are flattened. Keep the baseline values
alongside an ephemeral candidate. Prompt construction retains original roles,
mandatory policies, current input, images, scene, simulation and conversational
history. Candidate changes use construction-owned payload boundaries, never
regular expressions that infer provenance from final prompt text.

Deduplication requires the same canonical evidence, source revision, reader
scope, audience and payload. Repeated prose, facts from different times or
audiences, and dialogue are not interchangeable. The retained representation
keeps its evidence associations. An ambiguous mapping, historical request,
missing coverage or pending invalidation with still-valid evidence preserves
the baseline. A detected source, audience, reader or session-incarnation change
stops before any provider request and asks the caller to retry: the captured
baseline contains the same revoked evidence and cannot be a safe fallback.

Older-history substitution additionally requires accepted coverage for the
exact removed interval and proof that required facts and causal support remain
available to the same reader. Coverage and lexical relevance alone do not
establish that proof. Do not add an unsafe pruning path to meet a savings goal;
report unavailable proof explicitly and retain complete dialogue.

Finalization rechecks candidate eligibility before dispatch and applies the
existing hard model-window/output-reservation checks. Oversized protected input
continues to fail explicitly. It never triggers a second paid quality response.

## Controls

`SILLYTAVERN_CONTEXT_SELECTION_MODE` accepts `off`, `shadow`, or `enabled`, with
`off` the default. Shadow constructs a candidate locally and dispatches the
original baseline while its source and reader remain valid. Enabled additionally
requires an explicit slice in
`SILLYTAVERN_CONTEXT_SELECTION_SLICES`: `dedup`, `history`, or `summary`.
No slice is approved by default. Controls operate independently of token caps.

## Attribution and summary helper

Record estimates for mandatory instructions, history, World Info, derived
payloads and task additions directly from construction. Save only fixed
category names, bounded numbers, booleans and fixed selection reason codes.
Transport observations cannot overwrite construction accounting. Estimates
exclude image/framing accounting and are separate from provider-reported usage.

Summary extraction already receives accepted classified state plus the next
source part. Its initial candidate only removes JSON separator whitespace,
preserving every parsed value, audience, schema, source byte, model and setting.
Use existing durable acceptance/checkpoint logic; never reuse failed or
invalidated work or skip pending parts. Add no database or result cache.

## Evaluation and rollout

Synthetic captures replay the actual MemoryService, full-story builder and
final budget path. The current service already fuses repeated native facts, so
the initial canonical deduplication capability can be a no-op on native
captures. Deliberately duplicated blocks belong in mechanism tests and must not
inflate the evaluation's baseline or claimed savings.
Freeze paired case weights, model/settings and protected input. Required-answer
annotations are evaluation data, never prompt hints. Report story totals,
weighted distribution and total accepted-work input from supplied observations;
include failures, repairs and fallback and treat cached input as logical input.

Offline structural tests cannot establish narrative quality. Approval requires
complete matched provider accounting and blinded review of voice/readability,
causal and temporal continuity, agency, reader knowledge and unsupported claims.
Unknown accounting, unreviewed output, stale evidence or any regression is not a
pass. Paid comparison requires separately approved endpoint, model, current
prices, request/token/cost caps. This implementation performs zero model calls
for evaluation and leaves activation off.

## Verification

Use public-behavior failing tests before implementation, focused regression
tests, independent review, the complete Python suite with coverage, security
coverage, dependency/size/static gates and repository CI. Preserve privacy,
transaction ownership, cancellation and source mutation fences throughout.

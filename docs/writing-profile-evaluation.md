# Writing-profile evaluation protocol

Status: the original offline protocol is preserved below. A completed, bounded v0.3.019 subscription-only pilot is recorded in [post-release results](evidence/post-v0319-validation/RESULTS.md). It does not approve a new default or replace independent human review.
This protocol does not assert semantic correctness from a unit test or declare
any profile better than an existing user prompt.

## Reproducible offline gate

Run `python -m pytest -q tests/test_native_writing_profiles.py`.
The fixture `tests/fixtures/writing_profile_workload.json` contains eight synthetic
cases: attempted actions, promises and negation, reader/character knowledge,
quiet handoffs, off-screen ensemble scenes, established magical rules, branch
boundaries and Indonesian continuity. Every case contains its complete card,
current-branch history, user request, narrative settings, required facts,
forbidden inferences and review focus. No production chat or credentials are used.

Golden-delta assertions require all five configurations (Off plus four profiles)
to produce identical native requests except for the chosen optional profile
section. Repeated construction must be deterministic, user content and supplied
facts must remain present, and native contracts must remain in the request.
Separate long-history tests preserve fixed profiles and the current request
under compaction. These are request-wiring checks, not model-output judgments.

## Freeze before any provider calls

Choose one candidate profile and one baseline for a trial. Off is the fixture's
baseline; a trial against an existing custom prompt must preserve that exact
text and must not label Off as the production baseline. Freeze repository commit,
profile and fixture file hashes, model/provider identity, full card/history inputs,
request construction settings, sample ordering, sampling values, output limits,
context limits, language rendering and humanizer settings in a reviewed plan.
Record both construction requests and final adapter payloads because some
providers consolidate system messages. Do not change the candidate after seeing
outputs within the same experiment.

Agree on an explicit token/spend ceiling and use an included subscription model
only when its current allowance is verified. Never enable paid overage silently.
Default pilot: eight paired cases, sixteen story requests and sixteen optional
order-swapped judge requests, at most thirty-two physical requests total. Failures
consume that allowance. No silent retry, fallback or repair; an expanded budget
or altered procedure requires a new recorded plan. This document does not grant
permission to run the experiment or mutate a live session.

Use the same model and settings for baseline and candidate, with balanced request
order. Hold renderer/humanizer behavior constant; Off/auto isolates prose generation
but does not measure the complete production workflow. A repeated-sample study
must predeclare its repetition count and analyze every result, including failures.

## Review without profile labels

Give reviewers the complete case canon and the two anonymous story outputs,
not the preset names or a shortened summary of the canon. Keep the label mapping
separate from review materials. An automated comparison should be repeated with
presentation order reversed; contradictory judgments remain disagreements rather
than being selectively rerun. An independent human review is still required
before changing a production default.

Grade required-fact retention, causal continuity, user agency, character knowledge,
voice, dialogue usefulness, natural handoff and requested formatting/language.
A new user decision, unsupported private knowledge, branch leak or contradicted
required fact is a hard failure, not something prose scores can compensate for.
Report case-level reasons and quotations from the generated outputs, including
ties, unavailable outputs and reviewer disagreement. Do not lower thresholds
after reading the results.

## Usage and retention accounting

Record provider-reported input/output/cache/reasoning usage and latency for every
physical request. Unknown usage stays unknown. Include helpers, renderers, repairs,
retries and fallbacks in total workflow accounting; report judging separately and
include its cost in the total experiment budget. A prose-only trial cannot imply
production-wide savings when production helpers were not measured.

Compare final retained context as well as input size. An added fixed profile can
reduce the history that fits. Include a separate predeclared long-history extension
with unresolved facts in old turns, while holding canonical summaries and retrieval
inputs identical. Do not claim continuity preservation from lower token counts or
from a prompt-size estimate. Report both absolute totals and per-case changes.

## Full preset compatibility decision for this release

Keep complete SillyTavern Chat Completion exports unsupported. The supported subset
is native TXT and the documented simple JSON catalogue formats; this is a deliberate
native interface, not partial execution of a modular export. Reserved `prompts`
and `prompt_order` structures must be rejected before auxiliary strings can be
selected. Existing manual text is not silently rewritten to remove unknown macros.

Any later importer needs an independently reviewed contract for activation/order,
marker expansion, message placement, sampler translation and unsupported-feature
reporting. Stateful variables and conditionals need scoped deterministic evaluation.
Preview must be read-only; retries must be idempotent; regeneration and committed-turn
counter semantics must be explicit. A source preset's generation counter cannot
silently become a committed-story counter. No such counter or interpreter is added
by the native writing profiles. Track that separate feature in roadmap #468.

# Native source-retention and matched context experiment

This is an **explicit evaluation-only** candidate, not an enabled runtime optimizer.
It preserves the original source evidence by encoding repeated older paragraphs in
a reversible dictionary. Every occurrence, role, chronological position, literal
character, first historical turn, recent eight turns and non-history instruction
survives exact decoding. Repeated utterances are not merged into one event.

`bridge/context_native_receipt.py` issues a read-only receipt only after matching
prompt history against current native SQLite source rows, valid complete source
intervals, accepted classified Summary checkpoints, session incarnation, reader
scope, and rewrite state. The receipt is reconstructed from current native state
before each experimental model request. A caller-provided boolean or summary is
not a substitute for canonical sources. An altered scope, missing source interval,
changed source or malformed dictionary fails closed.

**Production dispatch fence:** the standard context-selection runtime rejects any
`_history_codec`-marked candidate in `off`, `shadow`, and `enabled` modes,
including while ordinary baseline dispatch remains operational. The codec may
be used for explicit offline replay only; source reconstruction is not
authorization for model use.

**What this proves:** exact retention of the original causal evidence, including
facts and negations not recognized by an extractor. **What it does not prove:**
that a stochastic model will interpret the representation identically, that a
selected baseline was already semantically complete, or that private source text
may be shown to a different reader. No production activation authority is issued.

## Predeclared workload and budget

The six synthetic native SQLite cases include four recurrent-context long stories
and two mandatory negative controls. Each has weight 1/6. The original frozen
context-efficiency benchmark is unchanged and must be reported separately. Results
on the repetition-heavy workload must not be presented as traffic-wide savings.

The same included NanoGPT `z-ai/glm-5.2` model, temperature and output allocation are
used for both story variants. Generation order is counterbalanced. Twelve paired
story requests precede twelve stateless, label-blinded review requests, including
both A/B and B/A presentation orders per case. No retries, output repair or fallback
are allowed. The total ceiling is 24 physical model requests, 300,000 logical input
tokens, 24,000 reserved output tokens, 150,000 request bytes per request, and a
100,000-unit subscription reserve. Every admission checks an active subscription
and `allowOverage=false`. Cache-hit tokens remain part of logical input; missing
usage stays unknown and blocks further spending.

Reviewers receive only task, relevant canon, language and anonymous A/B prose—not
variant names, prompts, dictionary metadata, token counts or hashes. Both judgments
must be journaled and locked before unmasking. The predeclared automated margins
are at least -0.25 overall and -0.5 on each dimension, with no critical violations
or direction-changing presentation-order preference. This small paired study is
**automated blinded review**, not independent human approval or a powered universal
noninferiority result. A separate unlabelled human packet and separate unmasking key
are exported; human approval remains false.

## Reproducible execution

```bash
python tools/evaluate_native_context.py --directory /private/trial-directory \
  --model nano-gpt::z-ai/glm-5.2
python tools/run_native_context_trial.py --directory /private/trial-directory --freeze
```

Commit the code, synthetic fixture declaration and frozen plan identity before
sending a model request. The frozen plan binds exact source snapshots, both wire
prompts, generation settings, case weights, request order, reviewer rubric and
implementation digest. Keep synthetic SQLite snapshots outside the repository.

```bash
python tools/run_native_context_trial.py --directory /private/trial-directory
python tools/run_native_context_trial.py --directory /private/trial-directory \
  --execute --expected-plan-sha256 THE_PRECOMMITTED_HASH
```

Each attempt is durably marked pending before its single send. Completed calls
are not repeated on resume; failed, pending or unknown-delivery calls prohibit
automatic retry. The report retains all observed attempt usage and separates story
input, review overhead and shared-audit-cost sensitivity. Human-review packets,
raw synthetic outputs, frozen source snapshots and unmasking keys are distinct
files. No API credentials or production transcript is written to any artifact.

A passing source reconstruction test, a >=30% measured reduction, and a passing
machine review are separate results. None enables production history pruning.

This describes the **original frozen v1 experiment** and its historical 30% gate. Future separately predeclared matched trials use the current **20% target** (effective 2026-10-09) plus complete accepted-work and independent human-review gates; the original evidence is never re-scored in place.

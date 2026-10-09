# Native continuity and matched quality experiment

## Approved intent
The October 9 request authorizes implementing native causal-continuity evidence, a candidate capable of a 30% reduction, and a bounded matched provider/blinded comparison. Use NanoGPT's available subscription tokens, never paid overage. Do not export private production transcripts, change the story model between variants, lower candidate output limits, or enable production pruning. Report after execution.

## Chosen design
A generative summary cannot certify that it captured every causal fact. Instead, retain **every original source character, speaker role, occurrence and order** in a reversible representation. Compress repeated older dialogue paragraphs through an explicit verbatim dictionary, keeping individual message roles and all recent messages unchanged. Each dictionary reference denotes a separate occurrence, never a merged event. Decoder equality is a constructive proof of retention of the original causal evidence, not a proof that a stochastic model interprets the representation identically.

A native read-only SQLite receipt binds the exact prompt history to real canonical message rows, current session incarnation/rewrite revision, reader scope and healthy accepted Summary coverage. Each source interval must have valid, gap-free accepted source evidence. Revalidate native state and exact decoder equality before any experiment dispatch. Caller booleans and synthetic witness claims cannot issue authority. No new schema or persisted memory cache.

Alternatives rejected: summary-only pruning (unproven semantic completeness), more role packing alone (the existing study misses 30%), and arbitrary context limits (loses unconstrained evidence). This experiment changes representation, not which events exist.

## Scope and negative controls
The original frozen six-case evaluator is retained and rerun; it is not relabeled or tuned to pass. Predeclare six additional native SQLite scenarios before provider dispatch: four recurrent-context long stories and two nonrepetitive/short controls. Include delayed commitments, negations, reader knowledge, branch choice, and multilingual text. Record exact prompts, source/proof digests, settings, weights and request order in a committed plan. Any 30% result applies only to the declared workload and eligible repetitions, not all production traffic.

## Measurement and review
Baseline and candidate use the same included NanoGPT GLM-5.2 model and generation parameters. Maximum 24 model requests: twelve story generations and twelve independent order-swapped, label-blinded reviewer requests. No repair/retry/fallback. Record every physical request's logical input/output/cache usage and status; unknown usage fails the experiment. Reserve at most 300,000 input and 24,000 output tokens, with per-request byte limits and live subscription/no-overage checks. Include the reviewer overhead separately; candidate construction uses no helper calls.

Review packets contain reference canon, current task, and anonymous outputs A/B, but no prompt representation, variant label, usage, hashes, or selection metadata. Use the same reviewer model in both orderings and lock judgments before unmasking. This is **automated blinded review**, not independent human approval. Produce a separate unlabeled packet for later human review; never fabricate a human signature or treat model scoring as universal narrative equivalence.

## Activation boundary
All new functionality is explicit evaluation/shadow-only. Existing off/shadow/enabled runtime behavior stays unchanged. Native source-retention proof, measured savings, automated review and human production sign-off are reported as distinct fields. A failed control, stale source, wrong reader, missing coverage, malformed dictionary, incomplete usage, quality regression or inconclusive review cannot authorize activation.

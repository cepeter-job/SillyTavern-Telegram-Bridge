# Paired full-story context efficiency checkpoint

`tools/evaluate_context_efficiency.py` builds a paired, synthetic full-story
prompt replay. Its default mode performs zero network requests and does not
execute a model. The 30% input-token reduction is a **goal**, never an offline
result. Estimated tokens, reported provider usage and human narrative review
are separate report fields.

```bash
python tools/evaluate_context_efficiency.py --output /tmp/context-plan.json
```

For a future paired evaluation, predeclare the actual model and output allocation
without dispatching it:

```bash
python tools/evaluate_context_efficiency.py \
  --model "$EVAL_MODEL" --max-output-tokens 1200 \
  --output /tmp/context-plan.json
```

The output allocation must be 1 through 4096 tokens, matching the existing
answer evaluator's cap. Use the same declarations when validating observations
and review; they are part of the plan identity. The default model is an explicit
synthetic placeholder, not a provider recommendation or a live routing choice.

## What the replay compares

The frozen `tests/fixtures/story_memory/context_efficiency_v1.json` captures
character instructions, session instructions, response language, author note,
scene state, narrative policy, group speaker policy, current input and recent
transcript. Its six cases and their workload weights are declared before any
observations: ordinary roleplay (20%), repeated and negated dialogue (20%),
exact-ending continuation (20%), audience-scoped recall (15%), mandatory
language (10%) and a long-running conversation (15%). These synthetic weights
are a declared study assumption, not an estimate of production traffic.

Memory evidence comes from the existing frozen retrieval corpus and its
production acceptance, scope resolution, retrieval and block validation. The
existing generated-answer evaluator supplies query identities and retrieval
metadata. Its question/extraction prompt and required/forbidden answer judgments
never enter a story request. Baseline payloads come directly from the actual
`MemoryService.prompt_context` on the fixture database and session, with the
captured local readers and as-of boundary. Current-tip cases use the natural
native current scope and must match the captured scope exactly; the historical
case retains its explicit earlier cutoff. Scope drift fails capture rather than
silently reading beyond that boundary. The existing service already fuses
native recall and episodic facts; replay never copies an episodic block into
recall or introduces a duplicate after validation. Artificial duplicates belong
only in mechanism tests, never in checkpoint totals.

The candidate uses the same actual memory service with the production
store-backed selection callback and freshness guard. It resolves the current
scope and revalidates the actual baseline blocks before dispatch. Both variants
build from the same baseline context and use the actual
`bridge.generation.build_chat_messages`
with matched settings and state, add the actual Light Novel output contract,
and apply the actual pre-dispatch selector to construction-marked optional
spans. Original mandatory policy text remains byte-identical. Both then run
final production budgeting with the same model and explicit output
reservation. Continuation protects the exact last assistant ending. This
checkpoint therefore compares full story requests, including mandatory policies,
NPC/simulation/RAG context and conversational history.

The checkpoint evaluates memory deduplication. History and summaries remain
conservatively present; it does not invent coverage or causal-closure proofs
for history pruning. Selection results and budget statistics remain visible.
All pairs check that character/session/narrative/language/output instructions,
current dialogue/action text, required repeated/negated history and continuation
endings survive. A failed invariant blocks readiness even if accounting shows
large savings. The fixture is deliberately small and is not representative
model-quality or deployment evidence. The native frozen replay currently has
**zero estimated reduction**: its baseline is already fused. This is a valid
no-savings result, not evidence that a 30% target was achieved. The audience
case asks about Mira's private recognition phrase, which Rowan does not know;
its exact private canary is checked against every emitted prompt.

Known source, reader or session-incarnation revocation stops the current request
before provider dispatch and asks the caller to retry. Ambiguity, historical reads, missing coverage or a
pending invalidation with a still-valid baseline retain the baseline. Shadow
mode emits only the stable baseline; known revocation also aborts shadow
rather than permitting stale context through.

## Runtime controls and remaining gates

`SILLYTAVERN_CONTEXT_SELECTION_MODE` defaults to `off`. Unknown values also
resolve to `off`.

| Mode | Behavior |
| --- | --- |
| `off` | Preserve the existing provider payload and skip selection revalidation. Construction estimates are still recorded. |
| `shadow` | Build and measure a local candidate, then dispatch the valid baseline once. |
| `enabled` | Apply only the explicitly approved slices in `SILLYTAVERN_CONTEXT_SELECTION_SLICES`. An empty allowlist applies no changes. |

The slice allowlist is comma separated. `dedup` permits exact canonical memory
deduplication when all eligibility checks pass and the complete candidate fits
the final budget. The current native fusion often leaves it no additional work.
`summary` removes JSON separator whitespace from the accepted summary accumulator
while preserving all values and source bytes. `history` currently checks accepted
coverage and reports eligibility only: no history is removed because the stored
contracts do not prove required-fact and causal-support closure.

Section estimates cover `mandatory`, `history`, `world_info`, `derived`, and
`task` construction categories. They exclude image/framing costs and are kept
separate from final transport estimates and provider usage. Persisted selection
metrics contain only bounded counts, flags and fixed reason codes; no new prompt
text, source IDs or private facts are saved in these fields.

This change leaves live activation off. The 30% provider-input target,
representative paired model evaluation, blinded narrative review, and any
history substitution remain open gates in issue #421. Paid evaluation requires
separate approval of endpoint, model, current prices and request/token/cost caps.

## Supplied paired observations

This tool has no live-dispatch flag, endpoint or credential argument. After a
separately authorized evaluation, supply the complete observations for the
exact plan and prompt hashes:

```bash
python tools/evaluate_context_efficiency.py \
  --observations /tmp/paired-observations.json \
  --review-output /tmp/blinded-story-outputs.json \
  --output /tmp/context-accounting.json
```

An observation document has this shape. Repeat the case entry for **every**
predeclared case and provide both variants. The hash values below are placeholders,
not usable evidence:

```json
{
  "schema_version": 1,
  "replay_plan_sha256": "COPY_THE_EXACT_PLAN_HASH",
  "blinding_seed": "COPY_A_FRESH_PRIVATE_64_HEX_CHARACTER_RANDOM_SEED",
  "cases": [{
    "case_id": "ordinary",
    "variants": {
      "baseline": {
        "prompt_sha256": "COPY_THIS_VARIANT_PROMPT_HASH",
        "model": "synthetic-story-model",
        "settings": {"max_tokens": 1000, "temperature": 0.7},
        "output": "The actual accepted model continuation.",
        "accepted": true,
        "accounting_complete": true,
        "requests": [
          {"kind": "story", "outcome": "accepted", "input_tokens": 1200,
           "cached_input_tokens": 200, "output_tokens": 160,
           "output_sha256": "SHA256_OF_THE_ACCEPTED_OUTPUT_UTF8_BYTES"},
          {"kind": "helper", "outcome": "success", "input_tokens": 80,
           "cached_input_tokens": 0, "output_tokens": 20},
          {"kind": "repair", "outcome": "failed", "input_tokens": 900,
           "cached_input_tokens": 0, "output_tokens": 60},
          {"kind": "fallback", "outcome": "failed", "input_tokens": null,
           "cached_input_tokens": null, "output_tokens": null}
        ]
      },
      "candidate": {
        "prompt_sha256": "COPY_THIS_VARIANT_PROMPT_HASH",
        "model": "synthetic-story-model",
        "settings": {"max_tokens": 1000, "temperature": 0.7},
        "output": "The actual paired accepted continuation.",
        "accepted": true,
        "accounting_complete": true,
        "requests": [
          {"kind": "story", "outcome": "accepted", "input_tokens": 800,
           "cached_input_tokens": 200, "output_tokens": 160,
           "output_sha256": "SHA256_OF_THE_ACCEPTED_OUTPUT_UTF8_BYTES"}
        ]
      }
    }
  }]
}
```

Generate a fresh private seed with `python -c "import secrets; print(secrets.token_hex(32))"`.
Keep the observations file and seed away from the reviewer. Assignment is
randomized from that seed, counterbalanced and reproducible for later validation.
The seed is never included in the review packet or model prompt.

Use the same model, generation settings, captured state and output allocation
for each pair. Record all requests attributable to accepted work, including
helpers, failed attempts, repairs and fallback requests. An accepted output must
have at least one successful or accepted story,
repair or fallback request with its `output_sha256` equal to the SHA-256 hash
of that exact output's UTF-8 bytes. A successful helper alone cannot establish
an accepted story output; an inventory with only failed attempts is rejected.
Do not discard an expensive failed call merely because a later response was
accepted. Do not omit
a helper request because its model differs from the story model. Share common
work explicitly and consistently between the two matched variants.

`input_tokens` means full **logical** provider input. Cached input is a subset
and is never subtracted. If only uncached/billable tokens are available, logical
usage is unknown. Unknown input or output usage uses `null`, remains unknown
and blocks
readiness; estimates never replace it. Cached-token breakdowns may stay unknown
when full logical input is reported; no caching discount is inferred. An absent
request inventory or a false
`accounting_complete` assertion cannot support a total accepted-work comparison.
Provider counts and inventory completeness are supplied observations; the
offline tool does not independently audit provider logs or billing.

The report separately gives aggregate story input, the predeclared weighted
story input, nearest-rank p50/p95 story-input counts, and total input for all
accepted work. Full request inventories retain output and cached counts.
`(baseline - candidate) / baseline` is the reduction fraction; a zero baseline
makes that fraction unknown. Monetary savings are not inferred from logical
input reduction. Request outputs, model semantic correctness and provider
billing are not established by token accounting.

## Blinded human comparison

Give the reviewer only `--review-output`'s separate packet, which includes the
shared captured story context, current input and transcript. Keep the unblinded accounting report, variant hashes
and label mapping away from the reviewer until ratings are recorded. The
packet counterbalances A/B assignments across cases and contains neither
variant names nor prompt hashes. Its identity binds the accepted output texts.

Rate each A/B output from 1 (poor) to 5 (excellent) on every predeclared axis:
voice/readability, causal and temporal continuity, character agency, and
knowledge boundaries/contradictions. Record ratings before unmasking. Use a
review document with the packet hash printed in the accounting report:

```json
{
  "schema_version": 1,
  "review_packet_sha256": "COPY_THE_REVIEW_PACKET_HASH",
  "reviewer": "Reviewer identity",
  "blinded_before_unmasking": true,
  "cases": [{
    "case_id": "ordinary",
    "scores": {
      "A": {"voice_readability": 4, "causal_temporal_continuity": 4,
            "character_agency": 4, "knowledge_and_contradictions": 4},
      "B": {"voice_readability": 4, "causal_temporal_continuity": 4,
            "character_agency": 4, "knowledge_and_contradictions": 4}
    }
  }]
}
```

Include every case, both labels and every axis. Validate the completed review
against the same observations:

```bash
python tools/evaluate_context_efficiency.py \
  --observations /tmp/paired-observations.json \
  --human-review /tmp/blinded-review.json \
  --output /tmp/context-reviewed.json
```

`decision.approval_ready` stays false until the supplied matched observations
have complete known usage, accepted outputs, preserved prompt invariants, at
least 30% reduction in aggregate/weighted story input and total accepted-work
input, and no candidate regression on any human-rated axis for any case. This
is conservative readiness for this synthetic checkpoint. It is not automatic
deployment approval, independent evidence of a genuine blinded process, or a
claim that production quality is preserved.

A default plan explicitly states `model_evaluation.status: not_executed` and
`narrative_quality.status: human_review_required`. With observations it states
`supplied_observations_only`; this CLI still has made zero requests. Exit 0
means a plan or evidence-validation report was written, including reports with
blocked readiness. Exit 2 means invalid input or CLI configuration. Inspect
`decision`, rather than treating exit 0 as a benchmark pass.


## Additional shadow-only history-framing checkpoint

An optional **shadow-only** preview now reads an ephemeral, bounded canonical
history snapshot and compares exact source row identities, roles, and contents
to the prompt builder's history. It runs only for non-historical current-tip
readers with complete, non-invalidated summary source coverage. A source,
reader, incarnation, or content change invalidates the capture before dispatch.

When eligible, the preview packs some older dialogue into quoted JSON pairs of
original role and complete text, in order, while keeping the first turn, eight
recent turns, direct query callbacks, negations, unresolved commitments, and
causal neighbors in their original roles. It declines uncertain mappings and
non-ASCII older dialogue. No dialogue text is summarized, paraphrased, silently
removed, or promoted to policy in this *local preview*.

The safe baseline remains the only dispatched prompt. Even
SILLYTAVERN_CONTEXT_SELECTION_MODE=enabled with a history allowlist **cannot**
send role-reframed history in this revision. Shadow stores only allowlisted
numeric estimates and fixed reason codes: history_shadow_candidate_tokens,
history_shadow_reframed_turns and history_shadow_reason. Source identities,
dialogue, and candidate prompts remain ephemeral and are never saved in those
metrics. Preview estimates are *not* provider token savings.

The initial focused fixtures verify source/text/order reconstruction, role and
reader boundaries, repeated/negated lines, long-range commitments, continuation
protection, source rewrites, and unchanged off/shadow dispatch. This is a
preparation for separately approved model and blinded narrative comparisons,
not a certificate of narrative equivalence or permission to enable.
The 30% provider-measured goal, holistic helper cost and quality release gates
remain open until representative matched evidence and explicit rollout approval.


## Frozen standalone history-shadow stress study

Run `python tools/evaluate_context_history_shadow.py --output /tmp/history-shadow-study.json`
to estimate a separate synthetic role-framing candidate **without network calls**.
Its eight predeclared weighted scenarios cover long archives, repeated
negations, delayed promises, exact-ending continuations, historical branches,
non-ASCII dialogue, stale source snapshots and missing summary coverage.

Every preview must reconstruct all original source text and order exactly,
preserve mandatory/current input and the last continuation target, and keep
negation and callback anchors in their original roles. The report writes
bounded metrics and fixed identifiers only; it does not export story contents.
Cases with missing authorization or an ambiguous source use the baseline.

The frozen synthetic study estimates **6.25% aggregate** and **7.55%
preweighted** input reduction by the character-ratio estimator. The result is
**not** provider-reported usage, a live-SQLite coverage or causal proof, or
a blinded narrative comparison. Reframing dialogue into a quoted user payload
might alter model interpretation even if every source byte is recoverable.
The existing native full-story evaluator and separate provider-accounting,
human-review and staged-activation gates remain authoritative; no history
pruning or paid/live request is enabled by the study. The 30% goal remains
unmet and uncertain rather than forcing a weaker prompt.


## Summary recovery preflight — read-only before signed rollout

The durable Summary, Scene and Episodes extractor format-recovery changes landed
in PR #428, but merging that code does **not** process live backlog. The
separately maintained `tools/inspect_summary_recovery.py` provides a
content-free operator preflight for a *local* bridge SQLite database:

```bash
python tools/inspect_summary_recovery.py \
  --database "$HOME/.local/share/sillytavern-telegram/scripts/sillytavern_telegram.sqlite3"
```

The utility opens SQLite with `mode=ro`, makes **no network or model calls**,
and does not print chat IDs, session IDs, provider text, prompts or story
contents. It reports current summary invalidation and coverage-lag counts,
parked/backoff/leased/inactive/eligible job counts, separately due manual claims,
maximum attempts and allowlisted internal error-code counts. Unknown raw errors
are grouped as `other`, never serialized.

`summary.catchup_complete` means only that this **advisory read-only
snapshot** found no invalidated or unprocessed summary source, and no pending
summary jobs for existing session incarnations. It is not a sufficient condition
for history pruning, semantic/causal closure or quality approval.
`approval_ready` stays false until those independent checks occur.

The ACK guard also refuses to complete a Summary job when canonical rows from
the existing accepted coverage boundary through the claimed target remain
unprocessed. The accepted publisher must advance durable source coverage before
the scheduler clears invalidation. It allows actual deleted-row gaps without
inventing missing text, and preserves the session/lease/revision fences.

Some older jobs have exhausted autonomous retries: a restart by itself **will
not** safely unpark them. After a verified SSH-signed deployment, an explicitly
approved, budget-bounded operator invocation of the existing manual summary
worker can retry canonical source parts under the same lease and publication
fences. Each failed JSON response may cause one additional repair request.
Record all such input and any model fallbacks as real helper work. Never set
`invalidated_from_id`, `covered_id`, `attempts`, or `completed_version`
directly to invent successful coverage, and do not force an unbounded replay.
Compare the inspector output before and after the controlled catch-up and only
claim recovery when the accepted checkpoint and source evidence agree.

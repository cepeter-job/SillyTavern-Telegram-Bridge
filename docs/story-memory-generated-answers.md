# Synthetic generated-answer checkpoint

`tools/evaluate_story_memory_answers.py` separates model-generated answer,
fact-extraction and narrative checks from the mandatory offline memory/retrieval
tests. Its default operation builds a synthetic evaluation plan and performs no
HTTP requests. It does **not** claim that a model has been evaluated.

## Corpus and context

The tool reuses the frozen `retrieval_v1.json` fixture and `RetrievalCorpus`.
That corpus accepts facts through production story paths, closes the original
ending and creates its alternate branch through production checkpoint cloning.
Queries keep the fixture's reader, audience, branch and historical boundary.
The context comes from production FTS retrieval and validated memory blocks,
using the existing `fused_blocks` composition. No parallel memory store or
private transcript input is accepted.

The model sees the question, selected production context and key/statement
bindings for selected facts. Required and forbidden judgments stay in the
evaluation report; they are not supplied as answer hints. A required fact that
retrieval omitted remains omitted. `required_missing_from_context` makes that
limitation visible instead of silently adding an oracle answer.

The default four queries cover a direct question, audience exclusion, a
historical boundary and a question without an eligible answer. `--queries`
selects unique IDs from Q01 through Q24; it cannot introduce arbitrary inputs.

```bash
python tools/evaluate_story_memory_answers.py \
  --queries Q01,Q14,Q19,Q23 \
  --output /tmp/story-answer-plan.json
```

The JSON report contains source revision/tree/hash, dirty status, fixture hash,
query scopes, selected/required/forbidden fact identities, prompts and prompt
hashes. Offline reports state `model_evaluation.status: not_executed`, zero
requests and `narrative_quality.status: human_review_required`.

## Explicitly bounded live operation

Live operation requires `--enable-live-model`, the **complete** OpenAI-compatible
chat-completions URL, an explicit model, the name of an environment variable
containing a credential, all five token/request limits, both per-million-token
USD prices and a total USD cap. The tool neither reads bridge configuration nor
chooses a default provider, model, price or credential. Live flags without the
opt-in flag are rejected.

After setting the endpoint, model and prices from the provider's current
contract, a four-request invocation is:

```bash
python tools/evaluate_story_memory_answers.py \
  --enable-live-model \
  --endpoint "$EVAL_ENDPOINT" \
  --model "$EVAL_MODEL" \
  --key-env EVAL_API_KEY \
  --queries Q01,Q14,Q19,Q23 \
  --request-budget 4 \
  --input-token-budget 60000 \
  --output-token-budget 6000 \
  --max-output-tokens 1500 \
  --context-token-cap 24000 \
  --input-usd-per-million "$EVAL_INPUT_USD_PER_MILLION" \
  --output-usd-per-million "$EVAL_OUTPUT_USD_PER_MILLION" \
  --max-cost-usd 0.10 \
  --output /tmp/story-answer-live.json
```

Do not increase a rejected budget automatically. Review the planned work and
provider prices first. The report records requested model and endpoint hash;
credentials and provider error bodies are never written. The model receives
only synthetic story material. A completion that echoes its credential is
rejected before its text can enter the report. This check includes decoded
string values and object keys, so JSON escaping cannot hide a credential.
Decoded strings must also be valid UTF-8; malformed Unicode produces a readable
failure report and stops further dispatch instead of breaking final reporting.

### Reservation and stopping rules

Before the first request, the tool reserves the full output allowance for
**every** selected query and a conservative input allowance equal to the entire
UTF-8 JSON request size plus 256 framing tokens. The reserve must fit the total
input/output budgets, each request's input-plus-output context cap, request
budget and supplied USD cap. Decimal arithmetic rounds reservations upward;
nonfinite, negative, absent or numerically unpriceable limits fail before HTTP.
All selected work must fit: the tool does not silently truncate the query list.

The hard maximums are 24 requests, 1,000,000 aggregate input tokens, 100,000
aggregate output tokens, 4,096 output tokens per request, 100,000 context tokens
per request and USD 10 per run. Lower explicit limits still control each run.
Zero prices are accepted only when explicitly supplied; they are not inferred.

The existing `BoundedHTTP` transport provides single-dispatch requests, no
redirects or automatic retries, at most 10 seconds per request and a 300-second
absolute run deadline. Failures, malformed JSON, duplicate object keys, invalid
response schema, truncated completions, excessive usage or credential echo
stop the remaining queries. No repair call or fallback model is attempted.

In addition to sending `max_tokens`, the tool requires the generated JSON
content to fit a UTF-8 **byte** cap numerically equal to that token limit. This
is deliberately stricter than ordinary token counting and also bounds text
when the provider omits usage. Reported usage, when present, must fit that
request's reservation and have consistent prompt/completion/total counts.
Absent usage keeps the full conservative reservation; it does not imply zero
cost or verified billing.

These are controlled text-model limits, not a provider billing guarantee.
The input allowance assumes ordinary byte-tokenized text and bounded chat
framing. Provider hidden prompts, separately billed reasoning, nonstandard
tokenizers, or additional charges are outside the priced contract. Provider
enforcement of `max_tokens` remains conditional; a local check cannot undo
charges already incurred by a provider that ignores its request contract.

## What the scores mean

Each completion must contain exactly `answer`, `facts` and `narrative`.
`facts` contains key/statement pairs. Structured checks independently report
missing required facts, forbidden and otherwise ineligible fact identities,
unknown identities and statements that disagree with canonical fixture text.
Repeated claimed identities and malformed schema fail the response contract.

Prose checks inspect the **actual answer and narrative**, independently of the
model's self-labelled facts. They require each expected canonical sentence in
both texts, and flag canonical forbidden sentences appearing in either text.
The matching normalizes case and whitespace. Consequently, labelling the
extraction correctly cannot hide an omitted answer or an exact forbidden
sentence leaked in narrative prose.

Identical checkpoint clones need care. If a forbidden identity has the same
canonical text as an eligible fact, prose alone cannot identify its origin.
The tool avoids calling that shared sentence a prose leak; structured claims
still must use an eligible identity. Multiple forbidden identities sharing an
otherwise forbidden sentence may appear together in the diagnostic list.

`machine_checks_satisfied` and the aggregate `machine_contracts_satisfied`
refer only to these deliberately narrow contracts. A sentence mention is not
proof of a true assertion: negation, paraphrase, contradiction, invented claims
outside extracted facts, tone and causality need human review. Requiring exact
sentences also rejects some semantically correct paraphrases. The report always
states `semantic_truth: not_assessed` and leaves narrative quality for human
review. The rubric covers voice/readability, causal and temporal continuity,
character agency, audience/branch knowledge and unsupported claims.

Exit status 0 means an offline plan was built, or all live cases completed and
satisfied the machine contracts. Status 1 means a live response failed or a
machine check found a violation. Status 2 means invalid CLI configuration or
preflight budget. These statuses never establish general model quality or
story safety.

## Verification and evidence limits

The mandatory tests run the real offline CLI and explicitly opted-in live
transport against a loopback fake provider. They cover conservative reservation,
missing/invalid opt-in limits, exact required/forbidden scoring, prose leaks
despite valid extraction, credential redaction and stopping without retry.
The transport fixtures supply **contract evidence**, not actual model quality.

No real model evaluation result is shipped with this checkpoint. A live run
must be executed under explicit endpoint, capability, pricing and budget inputs
before reporting empirical generated-answer results. Keep its full synthetic
report, source/fixture identities and a separately completed human narrative
review together; do not infer model performance from the offline plan or fake
provider tests.

## Full-story efficiency replay

The generated-answer checkpoint above uses a controlled retrieval/extraction
prompt. It does not measure complete production story-request input or establish
context-efficiency savings. For paired **full-story** prompt replay, use
[`evaluate_context_efficiency.py`](context-efficiency.md). That separate offline
checkpoint captures the actual native `MemoryService.prompt_context`, runs the actual story
builder and final budget gate, preserves matched settings and protected story
state, and reports estimates separately from supplied provider usage. Its
30% reduction remains a goal until complete paired accounting and a separately
recorded blinded human review are available. Neither tool's default offline
plan executes a real model.

The current native full-story replay reports zero estimated reduction because
the baseline memory service already fuses native recall and episodic facts.
Artificial duplicates are excluded from checkpoint totals.

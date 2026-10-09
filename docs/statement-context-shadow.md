# Statement-level continuity selection (evaluation only)

This phase-3 candidate extends the merged whole-turn hybrid shadow selector
without changing story-generation prompts. It is **not** an activation or release
of history pruning.

## Design

The current native reader scope, same-session story rows, valid source intervals,
accepted Summary checkpoint and archived Summary digests must be authenticated
through the existing native source-capture contract.

Older dialogue is segmented at conservative complete-sentence boundaries.
Balanced roleplay quotations, stage directions and paragraphs remain indivisible;
unknown-language scripts or ambiguous markup fall back to full source turns.
The first history turn and recent eight turns (or a larger explicitly requested
tail) remain verbatim.

Exact source substrings containing promises, refusals, negations, choices,
causality, knowledge boundaries or query-relevant details receive character
offset and SHA-256 receipts, checked against canonical source. Adjacent
statements within their originating turn preserve referents without retaining
whole neighboring turns. Short ambiguous consent/refusal retains its preceding
assistant referent. No protected statement is clipped to hit a budget.

Reader-authorized classified Summary blocks supplement older source spans,
but critical blocks are not silently discarded to meet a token target.
If evidence exceeds the reference bounds, or source/reader state changes,
the proposal falls back to full history.

Character cards, session system rules, world context, persona, author notes,
post-history directives, current input and any media remain unchanged.
A marked proposal cannot be dispatched in any context-selection mode.
Only explicit shadow mode computes statement diagnostics; telemetry
whitelists scalar status/count fields.

## Evidence and limitations

The offline evaluator assembles complete **synthetic** prompts using the
production build_chat_messages pipeline: system/card/world scenario,
history, accepted Summary, author/continuation policy and current user
input. It retains unique dialogue, commitment-dense, Indonesian,
unsupported-script, short-history and large-card controls. No network calls.

The optional live audit uses SQLite mode=ro, resolves the existing reader
card and reports history-only metadata, never private story text, reader IDs,
character names or session identifiers. This does not reconstruct full
production prompts and cannot establish provider-measured savings.

Exact source reconstruction is not proof of exhaustive causal interpretation
by a language model. Even if estimates exceed 30%, history pruning remains
OFF until matched provider input-token evaluation, independent blinded
narrative review and explicit human approval.

## Replay

Run after CI validation in a checkout with project dependencies.

    python tools/evaluate_statement_context.py --output /private/new-synthetic-report.json
    python tools/evaluate_statement_context.py --database /authorized/live.sqlite3 \
      --metadata-only --output /private/new-live-metadata.json

Reports refuse overwrite and always mark production_activation_allowed=false
and provider_measured_reduction=null.

The recovery branch was created through the GitHub connector when the
authorized VPS became unreachable. The previous local exploratory observations
are recorded in issue #421. GitHub CI must independently validate this branch.

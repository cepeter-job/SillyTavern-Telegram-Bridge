# Source-proven hybrid context shadow candidate

## Approved intent
User approved action 2: preserve recent verbatim dialogue, causal-critical commitments/refusals/choices/knowledge, retrieve relevant older dialogue and source-backed archived continuity, protect complete character/world/system prompts, and fall back to full history when evidence is insufficient. Implement and report; no production activation or new paid evaluation was requested.

## Design
Separate PR from main; PR #467 and its experiment/human-review checkpoint remain untouched. A new native read-only capture verifies the exact baseline history suffix against current SQLite, source intervals, healthy Summary/checkpoint state, archive digests, story incarnation, reader scope and cutoff. It rehydrates only authorized current/archive blocks; no other story or newly granted private knowledge.

The pure selector retains the first history turn, the recent eight or caller-requested larger tail, all detected critical utterances and adjacent referents, and four query-relevant older turns with neighbors. Non-Latin history, malformed markers, incomplete source coverage, stale scopes, unavailable archive proof or bounds exhaustion return unchanged full history. All non-history content stays byte-for-byte unchanged, including multimodal content, card/world/system directives and continuation targets. Oversized required continuity is not truncated to meet savings goals.

Authorized accepted Summary windows provide chronological reference context for other older turns. Every selected older raw turn comes from the same verified baseline source suffix. This is provenance plus explicit-anchor coverage, not a proof that a generative Summary exhaustively preserved every latent causal fact. Candidate material is available only for shadow evaluation; actual dispatch remains full baseline, with semantic proof/production approval false. No heuristic exclusion can grant deployment authority. Missing native evidence yields no reduced preview.

Shadow runtime stores only a callable producing content-free diagnostics, invoked only under the existing explicit shadow mode. It never substitutes the hybrid candidate into a story request. No new database schema, provider calls, dependencies, activation setting or universal prompt cap. Resource ceilings (256 history rows, 64 archive windows, bounded total source payload) fail closed instead of dropping material.

## Evidence
Native deterministic fixtures cover unique dialogue, promises/refusals/branches, authorized private summaries, missing source intervals, reset/edit/purge, unknown readers, language uncertainty, nonhistory directives and continuation. A CLI produces baseline/candidate token estimates and identity/provenance metrics without HTTP calls. Optional read-only live-shape audit exports only counts/reasons/estimates, never transcript, scope identities or summary text. Keep frozen prior benchmarks unchanged and report no-savings controls. Matched provider quality review remains a separate gate.

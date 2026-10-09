# Hybrid context selection: shadow only

This implementation adds a distinct hybrid **proposal** without changing any prompt sent to the story model. It does not replace the frozen dictionary-codec experiment in PR #467 and is not a production optimization approval.

## What the candidate contains

The first historical turn and the recent eight turns (or a larger explicit caller selection) remain verbatim with their original roles. English/Indonesian lexical anchors protect commitments, refusals, negations, causal explanations, branch choices, private knowledge and callbacks, together with neighboring referents. Up to four query-relevant older turns also survive with their neighbors. Repeated critical utterances are not merged into a single event.

Other older dialogue is represented by chronological, reader-authorized native Summary windows. Current and archived windows are complete blocks rather than character-clipped fragments. The additional reference is untrusted historical data, not a new user action or system instruction. Older raw dialogue is retrieved only from the already-authenticated baseline history; no foreign-session or newly granted raw memory is introduced.

All non-history payloads are unchanged: character cards, world setup, system/developer contracts, post-history instructions, current input and media. There is no new universal prompt cap. If mandatory continuity or protected turns consume the budget, the selector keeps full history rather than forcing a savings target.

## Native evidence and fallback

Native SQLite checks bind the exact source suffix, session incarnation, reader set, revision, cutoff, complete source intervals, active accepted Summary checkpoint and artifact payload. Archived windows retain their accepted canonical-source/digest contract after native checkpoint-ring cleanup; surviving checkpoints are also cross-checked. Corrupt archives, missing current checkpoints, incomplete coverage, rewritten sources, unknown readers, historical scopes and resource-bound exhaustion produce unchanged-baseline fallback.

The capture is repeated after candidate construction. Runtime rechecks its normal scope guard after shadow observation; a revoked baseline still stops dispatch rather than being sent as an allegedly safe fallback. Summary blocks must retain their exact accepted classification, and restricted facts require every active reader to be authorized.

**Important distinction:** source provenance and preserved explicit anchor text are not proof that a generative Summary exhaustively captured every latent causal fact or that the model interprets it equivalently. Lexical anchors intentionally favor retaining too much. Non-Latin older histories are not compacted by the English/Indonesian anchor policy. Even a successful shadow proposal always returns the original full history for actual dispatch and reports `semantic_continuity_proven=false` and `production_activation_allowed=false`.

## Runtime behavior

The existing explicit `SILLYTAVERN_CONTEXT_SELECTION_MODE=shadow` captures a lazy metrics-only hybrid probe. Off and enabled modes do not run that probe; the default remains off. Diagnostics contain only whitelisted reason codes, booleans and bounded counts. Candidate bodies are not stored in runtime telemetry.

A marked hybrid proposal is rejected by the dispatch gate in every mode. This prevents accidental activation by treating an offline proposal as a normal prompt. There is no hybrid activation switch in this change.

## Offline evaluation

```bash
python tools/evaluate_hybrid_context.py --output /new/path/synthetic-shadow.json
python tools/evaluate_hybrid_context.py --database /authorized/native.sqlite3 \
  --metadata-only --output /new/path/live-history-metadata.json
```

The first command runs six deterministic native-worker fixtures with unique dialogue, commitment-dense history, Indonesian dialogue, unsupported script, short-history and large-card controls. No model or network call is made. The live mode opens SQLite with `mode=ro`, resolves the existing character card reader, and exports only counts, estimates and reasons. It does not export transcripts, summaries, reader names or session identifiers.

Live diagnostic estimates use **history only plus candidate reference overhead**, not a reconstructed full production prompt. Synthetic estimates include their declared protected card/system payloads. Neither is provider-reported usage; neither constitutes a voice/readability or semantic-quality pass. Original no-savings controls and failed gates remain in every report. Output files must be new paths; prior evidence cannot be overwritten by the CLI.

The follow-up approval work is a separately declared matched provider experiment and calibrated blinded narrative review, with representative sessions and all helper/review costs accounted. Production prompts remain unchanged until those independent gates are satisfied.

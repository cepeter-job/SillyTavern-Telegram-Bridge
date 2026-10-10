# Live memory reliability and efficiency implementation plan

**Goal:** Execute issue #421 priorities 1–4 from the October 10 owner request: repair durable helper work, reduce avoidable helper continuations, investigate caching, and run a bounded new matched evaluation against the 20% story-input gate.

**Baseline:** main `5370e435f56804fd37b1d57f21b82a3bba6efd7b`; live `5539643`, CPython 3.11, observation on, context selection off. Use this isolated worktree; do not change the running source or private database in development.

## Constraints
- Preserve exact canonical source, audience, incarnation, revision, transaction and late-write fences. Never resolve an ambiguous external attempt by timeout alone.
- Do not weaken a parser to accept invalid data, shorten protected history, change story model/output allocation, or claim cached tokens are removed input.
- Keep historical frozen protocol files/evidence unchanged; older independent reviews do not approve new outputs.
- Existing runtime dependencies only. Tests first; no raw live prompt/response exports. Retain unknown usage and no-savings controls.

## Tasks and verification
1. **Helper contracts** (`episodic_extraction.py`, `npc_extraction.py`, `memory_curator.py`, `scene_state.py`): add integration tests over synthetic SQLite proving explicit root keys and `json_once` for structured helpers. Align episodic prompt with JSON-object mode; set explicit no-change forms and bounds; prevent prose continuation. Preserve source-rewrite/invalid-audience tests.
2. **External-memory lifecycle** (`memory_backend.py`, `memory_store.py`): test successful-but-ambiguous deletion watches and failed-retain backoff remain pending, then gradually extend retry intervals to a bounded one hour. Use a separate 90-second synchronous retention timeout (server advertises 60 seconds) without changing recall timeout or assuming a timeout is success. Investigate upstream service failures independently; no automatic clearing of existing flags.
3. **Cache and accounting investigation**: correlate allowlisted actual request metadata by native session, runtime revision, purpose, attempt and model; pin report interval. Report stable prefix and provider cache separately, and helper overhead separately from story input. Do not alter role/section ordering based only on cache observations.
4. **Fresh matched evaluation**: predeclare synthetic public canon, identical models/output settings/weights, A/B wire payloads and a strict budget; use existing subscription-only admission checks. Include full story controls and affected helpers; all physical calls count. Export a fresh blinded human packet and separately hidden mapping. Any missed 20% story gate remains a failure, not a reason to relabel helper savings. Independent human scoring cannot be fabricated.

## Execution ledger
- Read-only health: Hindsight 0.10.2 `/health` and document listing succeed; SDK 0.10.0. Current `retain` client deadline 30 s is shorter than server retention LLM deadline 60 s. Recent errors include Timeouts/ServiceExceptions; no endpoint switch authorized.
- Root mismatch: episodic asks for a top-level JSON array while `json_once` applies `response_format=json_object` on NanoGPT GLM-5.2. NPC and curator do not set `json_once`, permitting up to three prose-style continuations of JSON output.
- All 604 unfinished retirement documents inspected had ambiguous outstanding attempts and no protected current source. They must remain fenced; a repeated successful delete is not proof that a late write cannot appear.
- Initial broad local baseline hit its 120 s wall limit after 21 tests; not claimed green. Use focused reproductions and GitHub-hosted full CI.
- Helper RED: four intended contract failures and one existing rejection pass. GREEN: 41 helper/source/parser regression tests passed (133.90 s). Parser behavior and source fences unchanged.
- External RED: three intended failures (watch/backoff 300 s vs 3600 s; retain timeout 30 s vs 90 s). GREEN: four targeted tests passed. A broader retirement run exceeded its 90-second local wall limit after five tests; no broad-green claim.
- Checkpoint: all modified files pass Ruff, git diff --check passes, and every original frozen native protocol file matches the baseline exactly. No production mutation or provider inference performed so far.
- Resume 2026-10-10: PR #508 is the durable checkpoint. First hosted CI had one stale Hindsight test factory and two strict module-size failures; no production changes. Extracted SDK creation/close to `hindsight_client_runtime.py`, moved lease recovery to its queue owner, and lowered size baselines rather than weakening the ratchet. Updated the fake factory signature only. Eleven focused regressions passed in 17.33 seconds on resume.
- Ruling: This corrective candidate leaves story prompts identical. Include them as unchanged negative controls in the new study; helper savings cannot satisfy the separate >=20% story-input target. The new study can validate helper reliability, not authorize history pruning.

# Matched helper-correction study — October 10, 2026

**Decision: the 20% story-input gate is NOT MET. No production pruning is authorized.**

Baseline `5370e435f568`; evaluated candidate `b4c426b698cb`. Plan committed before inference, SHA-256 `02df404c0353722ca108485832df93c9bcbbe479a8d4564a8bfe3855c7809eb7`. All 23 physical calls had reported usage, with active subscription-only admission and overage disabled. No private production transcript was used.

| Purpose | Baseline calls | Candidate calls | Baseline input | Candidate input | Input reduction |
|---|---:|---:|---:|---:|---:|
| episodes | 3 | 2 | 1,249 | 839 | 32.83% |
| npc | 3 | 3 | 2,713 | 2,775 | -2.29% |
| scene | 2 | 2 | 769 | 851 | -10.66% |
| curator | 2 | 2 | 695 | 695 | 0.00% |
| story | 2 | 2 | 1,308 | 1,308 | 0.00% |

Complete declared workload: **6,734 → 6,468 input tokens (3.95% reduction)**. Known output: 6,196 → 3,348. Physical calls: 12 → 11. Both arms accepted 9/10 logical cases by native shape validators; the English NPC case failed in each. Its costs remain included.

Unchanged story controls produced **0% input reduction**. Episodic requests used one fewer repair in the candidate, but this two-case result does not establish production savings. Parser-valid results have not received independent semantic approval. Complete production accepted-work cost, including external-memory service inference, remains unproven.

## Failure discovered and follow-up

The candidate English NPC extraction returned an empty object. The existing retry repaired only the simulation object and could not recover missing NPC output. A separate follow-up regression now tests repairing both required roots from the exact canonical inputs, still using only the existing single repair allowance. That subsequent code correction was **not** sent for additional inference and is **not** certified by this frozen study. Historical outputs and metrics are unchanged.

## External-memory status

The retention-only client deadline was 30 seconds while the configured server retention LLM deadline was 60 seconds; the corrective code uses 90 seconds for retention only. Repeated ambiguous deletion watches and failed-retain retries back off to at most one hour. Outstanding late-write evidence is not expired or cleared. Upstream Hindsight logs also showed JSON extraction failures on its existing model route; this PR does not switch that route or claim those server errors are resolved.

## Human review

`HUMAN_REVIEW.md` and `HUMAN_SCORECARD.json` contain ten randomized A/B pairs from the frozen evaluated revisions. The mapping and raw provider ledgers remain owner-private on the VPS. Reviewers must not inspect those records, the unblinded plan or this conversation. The packet is an audit of this failed candidate, not approval of the later NPC repair or of production pruning. No scores or reviewer declaration have been invented.

## Scope

No production database, service configuration, live code checkout, provider catalog or historical frozen native-v1 protocol was modified during the study. The experiment used fresh disposable native SQLite fixtures and strict per-arm caps of 12 calls, 150,000 input and 16,000 output tokens.

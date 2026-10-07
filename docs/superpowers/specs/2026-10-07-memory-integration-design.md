# Memory integration simplification
Approved by the user on 2026-10-07 after the source audit. Implementation, testing, GitHub PR creation, and merge at each checkpoint are authorized.

## Outcome
Keep SQLite as the authority for accepted story, source revisions, branch and historical boundaries, and character audiences. Keep Hindsight as an optional semantic ranker for locally verified native facts. Remove unnecessary raw remote ingestion, make local reset and deletion independent of Hindsight availability, and bound foreground recall latency.

## Required behavior
- Requests and client closure use one owned event loop; owned loops and transports close on success and error.
- Local reset/delete atomically revoke old authority and save durable external cleanup before clearing canonical state.
- Remote cleanup targets old exact document identities or an explicit old generation. Delayed work must never delete new post-reset or recreated-session memory.
- Reset must not wait for a lock held across a Hindsight network request. Remote write/cleanup completion must revalidate generation, source, claim and cleanup ownership.
- Pending deletion survives deleted sessions, crashes, restarts, offline Hindsight, and memory-off mode.
- Known completion and unresolved remote writes remain distinct. Never expire unresolved erasure obligations on a timer or drop authority tombstones merely for size.
- Stop new raw source_segment ingestion. Preserve local complete source segmentation, source provenance, episodic extraction, summaries, scene, NPC, and curator data.
- Alternate-ending seeding/readiness must use locally required work, without awaiting raw Hindsight archives.
- Existing remote archival debt remains discoverable and retryable. Compact only records proved complete and unreferenced.
- Optional semantic recall has a finite foreground deadline and short-lived failure bypass; local retrieval remains available.
- Proxy routing is established predictably without per-request global-environment growth. Preserve the numeric-loopback destination restriction.
- Benchmark FTS5, facts-only Hindsight, and exact local embedding scoring on the same authorized fact corpus. Record measurement boundaries. Scripted vectors prove mechanics only; do not claim semantic quality or production memory improvement from them.
- Do not automatically replace Hindsight or add a resident embedding model. A production backend migration follows evidence, not this implementation.
- Keep current Python 3.11 support and hash-locked dependency discipline. Do not weaken required CI, coverage, module-size or architecture gates.

## Checkpoints
1. Loop ownership fix and narrow regression coverage.
2. Durable local-first reset/delete, generation-scoped cleanup, off-mode cleanup, and stale completion fences.
3. Facts-only remote indexing, local branch readiness, legacy archival retirement, and safe completed-state compaction.
4. Foreground recall deadline/failure bypass and predictable client/proxy lifecycle.
5. Reproducible retrieval comparison tooling, measured results where available, and operational documentation.

Each checkpoint is a separate branch, independently reviewed pull request, passing required checks, and merge before the next checkpoint starts. Design and progress are stored in git with the work. Release publication and live deployment are separate operations from these checkpoint merges.

## Acceptance
Test cleanup success/failure loop lifetime; reset during remote outage and in-flight work; restart after durable enqueue; new generation protection; memory-off deletion; late raw/native completion; facts-only ingestion; complete local source/alternate-ending readiness; bounded recall failure; and deterministic authorization in comparison candidates. Run focused tests locally and the repository's full required CI before merge.
